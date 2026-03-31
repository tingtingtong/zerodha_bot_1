"""
brokers/zerodha_headless_login.py

Headless Zerodha login using requests + pyotp (no browser, no Playwright).
Works on Linux cloud servers (GCP, Oracle) without a display.

Flow:
  1. POST credentials to kite.zerodha.com/api/login
  2. POST TOTP to kite.zerodha.com/api/twofa
  3. GET the KiteConnect login URL (with session cookies) → captures request_token
  4. Exchange request_token for access_token via KiteConnect
  5. Save to config/.zerodha_token.json

Required env vars (set in .env):
  ZERODHA_API_KEY, ZERODHA_API_SECRET, ZERODHA_USER_ID,
  ZERODHA_PASSWORD, ZERODHA_TOTP_SECRET

Usage:
  python brokers/zerodha_headless_login.py           # login, save token
  python brokers/zerodha_headless_login.py --check   # check if today's token exists
  python brokers/zerodha_headless_login.py --force   # re-login even if token exists
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

TOKEN_FILE = "config/.zerodha_token.json"

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded",
    "Origin": "https://kite.zerodha.com",
    "Referer": "https://kite.zerodha.com/",
}


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)
    except ImportError:
        pass


def _get_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"[headless-login] ERROR: {key} is not set in environment / .env")
        sys.exit(1)
    return val


def _check_today_token() -> bool:
    path = Path(TOKEN_FILE)
    if not path.exists():
        return False
    try:
        data = json.load(open(path))
        return data.get("date") == str(date.today())
    except Exception:
        return False


def _save_token(token: str, api_key: str) -> None:
    path = Path(TOKEN_FILE)
    path.parent.mkdir(parents=True, exist_ok=True)
    from datetime import datetime
    with open(path, "w") as f:
        json.dump({
            "access_token": token,
            "date": str(date.today()),
            "timestamp": datetime.now().isoformat(),
            "api_key": api_key,
        }, f, indent=2)
    print(f"[headless-login] Token saved to {TOKEN_FILE}")


def headless_login() -> str:
    """
    Perform Zerodha login without a browser.
    Returns the access_token on success; exits with code 1 on failure.
    """
    try:
        import pyotp
        import requests
    except ImportError as e:
        print(f"[headless-login] Missing dependency: {e}")
        print("  Install with: pip install pyotp requests")
        sys.exit(1)

    _load_env()

    api_key     = _get_env("ZERODHA_API_KEY")
    api_secret  = _get_env("ZERODHA_API_SECRET")
    user_id     = _get_env("ZERODHA_USER_ID")
    password    = _get_env("ZERODHA_PASSWORD")
    totp_secret = _get_env("ZERODHA_TOTP_SECRET")

    session = requests.Session()
    session.headers.update(BASE_HEADERS)

    # ------------------------------------------------------------------
    # Step 1 — POST credentials
    # ------------------------------------------------------------------
    print("[headless-login] Step 1: Submitting credentials...")
    resp = session.post(
        "https://kite.zerodha.com/api/login",
        data={"user_id": user_id, "password": password},
        timeout=20,
    )

    if resp.status_code != 200:
        print(f"[headless-login] Login failed (HTTP {resp.status_code}): {resp.text[:300]}")
        sys.exit(1)

    try:
        login_data = resp.json()
    except Exception:
        print(f"[headless-login] Non-JSON response from login: {resp.text[:300]}")
        sys.exit(1)

    if login_data.get("status") != "success":
        print(f"[headless-login] Login rejected: {login_data}")
        sys.exit(1)

    request_id = login_data["data"]["request_id"]
    twofa_type = login_data["data"].get("twofa_type", "totp")
    print(f"[headless-login] Step 1 OK — request_id: {request_id[:8]}..., twofa_type: {twofa_type}")

    # ------------------------------------------------------------------
    # Step 2 — POST TOTP
    # ------------------------------------------------------------------
    print("[headless-login] Step 2: Submitting TOTP...")
    totp_code = pyotp.TOTP(totp_secret).now()
    print(f"[headless-login] TOTP: {totp_code}")

    resp = session.post(
        "https://kite.zerodha.com/api/twofa",
        data={
            "user_id": user_id,
            "request_id": request_id,
            "twofa_value": totp_code,
            "twofa_type": twofa_type,
            "skip_totp": "false",
        },
        timeout=20,
    )

    if resp.status_code != 200:
        print(f"[headless-login] TOTP failed (HTTP {resp.status_code}): {resp.text[:300]}")
        # TOTP may be expiring; retry once with a fresh code after a brief wait
        print("[headless-login] Retrying with fresh TOTP in 5s...")
        time.sleep(5)
        totp_code = pyotp.TOTP(totp_secret).now()
        resp = session.post(
            "https://kite.zerodha.com/api/twofa",
            data={
                "user_id": user_id,
                "request_id": request_id,
                "twofa_value": totp_code,
                "twofa_type": twofa_type,
                "skip_totp": "false",
            },
            timeout=20,
        )
        if resp.status_code != 200:
            print(f"[headless-login] TOTP retry also failed: {resp.text[:300]}")
            sys.exit(1)

    try:
        twofa_data = resp.json()
    except Exception:
        print(f"[headless-login] Non-JSON response from twofa: {resp.text[:300]}")
        sys.exit(1)

    if twofa_data.get("status") != "success":
        print(f"[headless-login] TOTP rejected: {twofa_data}")
        sys.exit(1)

    print("[headless-login] Step 2 OK — TOTP accepted.")

    # ------------------------------------------------------------------
    # Step 3 — GET KiteConnect OAuth URL to capture request_token
    # The authenticated session will be redirected to the app's redirect URL
    # We disable redirects so we can read the Location header directly
    # ------------------------------------------------------------------
    print("[headless-login] Step 3: Fetching OAuth redirect...")
    connect_url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"

    resp = session.get(
        connect_url,
        allow_redirects=False,
        timeout=20,
    )

    # Expected: 302 redirect to redirect_url?request_token=...
    # But Zerodha may chain multiple redirects; follow until we see request_token
    location = resp.headers.get("Location", "")
    max_hops = 5
    for _ in range(max_hops):
        if "request_token=" in location:
            break
        if not location:
            break
        # Follow redirect manually
        resp = session.get(location, allow_redirects=False, timeout=20)
        location = resp.headers.get("Location", "")

    if "request_token=" not in location:
        # Last attempt: follow all redirects and check final URL
        resp = session.get(connect_url, allow_redirects=True, timeout=20)
        location = resp.url

    if "request_token=" not in location:
        print(f"[headless-login] Could not capture request_token. Last URL: {location[:200]}")
        print("[headless-login] Hint: Check that your Kite app's redirect URL is correct.")
        sys.exit(1)

    params = parse_qs(urlparse(location).query)
    token_list = params.get("request_token", [])
    if not token_list:
        print(f"[headless-login] request_token missing from: {location[:200]}")
        sys.exit(1)

    request_token = token_list[0]
    print(f"[headless-login] Step 3 OK — request_token: {request_token[:8]}...")

    # ------------------------------------------------------------------
    # Step 4 — Exchange request_token for access_token
    # ------------------------------------------------------------------
    print("[headless-login] Step 4: Generating access token...")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from kiteconnect import KiteConnect
    kite = KiteConnect(api_key=api_key)
    data = kite.generate_session(request_token, api_secret=api_secret)
    access_token = data["access_token"]

    _save_token(access_token, api_key)
    print(f"[headless-login] Done. Session ready for {date.today()}.")
    return access_token


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Zerodha headless login (no browser)")
    parser.add_argument("--check", action="store_true",
                        help="Exit 0 if today's token is valid, 1 if not")
    parser.add_argument("--force", action="store_true",
                        help="Re-login even if today's token already exists")
    args = parser.parse_args()

    if args.check:
        if _check_today_token():
            print(f"[headless-login] Token valid for today ({date.today()}).")
            sys.exit(0)
        else:
            print("[headless-login] No valid token for today.")
            sys.exit(1)

    if not args.force and _check_today_token():
        print(f"[headless-login] Token already valid for today ({date.today()}). Skipping.")
        sys.exit(0)

    logging.basicConfig(level=logging.WARNING)
    headless_login()


if __name__ == "__main__":
    main()
