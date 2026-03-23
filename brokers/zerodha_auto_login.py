"""
brokers/zerodha_auto_login.py

Fully automated Zerodha daily re-authentication.

Flow:
  1. Generate TOTP using the stored TOTP secret (pyotp)
  2. Drive the Zerodha login page headlessly via Playwright
  3. Capture the request_token from the redirect URL
  4. Exchange for an access_token via KiteConnect
  5. Save to config/.zerodha_token.json (same format as manual login)

Required env vars (set in .env or OS environment):
  ZERODHA_API_KEY       — from Kite developer console
  ZERODHA_API_SECRET    — from Kite developer console
  ZERODHA_USER_ID       — your Zerodha client ID  (e.g. AB1234)
  ZERODHA_PASSWORD      — your Zerodha login password
  ZERODHA_TOTP_SECRET   — base32 TOTP secret (shown when you enrolled 2FA)

Usage:
  python brokers/zerodha_auto_login.py          # auto-login, print result
  python brokers/zerodha_auto_login.py --check  # check if today's token already exists
"""

import argparse
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Dependency guards — give clear errors rather than cryptic ImportError
# ---------------------------------------------------------------------------

def _require(package: str, install_as: str = "") -> None:
    import importlib
    try:
        importlib.import_module(package)
    except ImportError:
        pkg = install_as or package
        print(f"[auto-login] Missing dependency: {pkg}")
        print(f"             Install with:  pip install {pkg}")
        sys.exit(1)


def _load_env() -> None:
    """Load .env if python-dotenv is available (non-fatal if absent)."""
    try:
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=False)
    except ImportError:
        pass


# ---------------------------------------------------------------------------
# Core auto-login
# ---------------------------------------------------------------------------

def _check_today_token() -> bool:
    """Return True if a valid today-dated token already exists on disk."""
    token_path = Path("config/.zerodha_token.json")
    if not token_path.exists():
        return False
    import json
    try:
        data = json.load(open(token_path))
        return data.get("date") == str(date.today())
    except Exception:
        return False


def _get_env(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        print(f"[auto-login] ERROR: environment variable {key} is not set.")
        print("             Add it to your .env file or system environment.")
        sys.exit(1)
    return val


def auto_login(headless: bool = True, timeout_ms: int = 30_000, manual_totp: str = "") -> str:
    """
    Perform the full Zerodha OAuth login flow automatically.

    Returns the access_token string on success.
    Exits the process with a non-zero code on failure.
    """
    _require("playwright", "playwright")
    _require("pyotp", "pyotp")

    _load_env()

    api_key     = _get_env("ZERODHA_API_KEY")
    api_secret  = _get_env("ZERODHA_API_SECRET")
    user_id     = _get_env("ZERODHA_USER_ID")
    password    = _get_env("ZERODHA_PASSWORD")
    totp_secret = _get_env("ZERODHA_TOTP_SECRET")

    import pyotp
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

    login_url = f"https://kite.zerodha.com/connect/login?api_key={api_key}&v=3"

    print("[auto-login] Starting headless browser...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        page = ctx.new_page()
        request_token: str = ""

        try:
            # ----------------------------------------------------------------
            # Step 1 — Set up non-blocking request listener to capture redirect
            # Zerodha redirects to the app redirect_url with ?request_token=
            # ----------------------------------------------------------------
            captured_url: list = []

            def _on_request(request):
                url = request.url
                if "request_token=" in url:
                    print(f"[auto-login] *** CAPTURED: {url[:120]}")
                    captured_url.append(url)

            page.on("request", _on_request)

            print(f"[auto-login] Opening login page...")
            page.goto(login_url, timeout=timeout_ms)
            page.wait_for_load_state("networkidle", timeout=timeout_ms)

            # ----------------------------------------------------------------
            # Step 2 — Fill user ID + password
            # ----------------------------------------------------------------
            page.wait_for_selector('input[type="text"]', timeout=timeout_ms)
            page.fill('input[type="text"]', user_id)
            page.fill('input[type="password"]', password)
            page.click('button[type="submit"]')
            print("[auto-login] Credentials submitted.")

            # ----------------------------------------------------------------
            # Step 3 — Switch to External TOTP if needed
            # ----------------------------------------------------------------
            page.wait_for_timeout(1500)

            # Zerodha login may default to "Mobile App Code" (Kite app).
            # If so, click "Problem with Mobile App Code?" to switch to external TOTP.
            try:
                problem_link = page.get_by_text("Problem with Mobile App Code?", exact=False)
                if problem_link.is_visible(timeout=3000):
                    print("[auto-login] Switching to external TOTP...")
                    problem_link.click()
                    page.wait_for_timeout(1000)
                    # Take diagnostic screenshot to see options
                    _save_debug_screenshot(page)
                    # Look for "Use external TOTP" or "TOTP" option
                    for label in ["Use external TOTP", "External TOTP", "TOTP app",
                                  "Authenticator app", "Use TOTP"]:
                        try:
                            opt = page.get_by_text(label, exact=False)
                            if opt.is_visible(timeout=1500):
                                opt.click()
                                page.wait_for_timeout(1000)
                                print(f"[auto-login] Selected: {label}")
                                break
                        except Exception:
                            continue
            except Exception:
                pass   # Already on TOTP page

            # ----------------------------------------------------------------
            # Step 4 — Fill TOTP
            # ----------------------------------------------------------------
            auto_totp = pyotp.TOTP(totp_secret).now()
            totp_code = manual_totp if manual_totp else auto_totp
            print(f"[auto-login] Using TOTP: {totp_code}")

            page.wait_for_selector("input", timeout=timeout_ms)

            filled = page.evaluate("""
                (code) => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    const target = inputs.find(el =>
                        el.offsetParent !== null &&
                        el.type !== 'hidden' &&
                        !el.name?.toLowerCase().includes('password')
                    );
                    if (!target) return false;
                    const setter = Object.getOwnPropertyDescriptor(
                        window.HTMLInputElement.prototype, 'value'
                    ).set;
                    setter.call(target, code);
                    target.dispatchEvent(new Event('input',  { bubbles: true }));
                    target.dispatchEvent(new Event('change', { bubbles: true }));
                    target.focus();
                    return true;
                }
            """, totp_code)

            if not filled:
                raise RuntimeError("Could not find TOTP input field on page")

            print(f"[auto-login] TOTP filled via JS.")
            page.wait_for_timeout(1000)

            # Check if React already auto-submitted (redirect happened)
            if "request_token=" in page.url:
                print("[auto-login] TOTP auto-submitted by React.")
            else:
                # Try clicking Continue; swallow all errors — redirect may still come
                try:
                    page.get_by_role("button", name="Continue").click(timeout=4000)
                    print("[auto-login] Clicked Continue.")
                except Exception:
                    try:
                        page.locator("button").first.click(timeout=2000)
                    except Exception:
                        pass  # page may have already navigated

            print("[auto-login] TOTP step complete.")

            # ----------------------------------------------------------------
            # Step 5 — Click Authorize on the KiteConnect consent page
            # After TOTP, Zerodha shows an app authorization page (sess_id=)
            # before finally redirecting to the app's redirect_url
            # ----------------------------------------------------------------
            try:
                page.wait_for_url("*sess_id*", timeout=8000)
                print("[auto-login] Authorization page loaded.")
                page.wait_for_load_state("domcontentloaded", timeout=8000)
                page.wait_for_timeout(1000)
                # Try common Authorize button labels
                for label in ["Authorize", "I Agree", "Allow", "Continue"]:
                    try:
                        btn = page.get_by_role("button", name=label)
                        if btn.is_visible(timeout=2000):
                            btn.click()
                            print(f"[auto-login] Clicked '{label}' on authorization page.")
                            break
                    except Exception:
                        continue
            except PWTimeout:
                pass  # Already past authorization page or not required

            print("[auto-login] Waiting for redirect...")

            # Wait up to timeout_ms for the interceptor to catch the redirect
            deadline = time.time() + timeout_ms / 1000
            while not captured_url and time.time() < deadline:
                page.wait_for_timeout(300)

            if not captured_url:
                raise RuntimeError("Redirect not captured — check Zerodha app redirect URL setting")

            final_url = captured_url[0]
            print(f"[auto-login] Redirect captured.")
            params = parse_qs(urlparse(final_url).query)
            token_list = params.get("request_token", [])
            if not token_list:
                raise RuntimeError(f"request_token not found in redirect URL: {final_url}")
            request_token = token_list[0]
            print(f"[auto-login] request_token captured: {request_token[:8]}...")

        except PWTimeout as exc:
            print(f"[auto-login] TIMEOUT during login flow: {exc}")
            # Save a screenshot for debugging
            _save_debug_screenshot(page)
            browser.close()
            sys.exit(1)
        except Exception as exc:
            print(f"[auto-login] ERROR during browser automation: {exc}")
            _save_debug_screenshot(page)
            browser.close()
            sys.exit(1)

        browser.close()

    # ----------------------------------------------------------------
    # Step 5 — Exchange request_token for access_token
    # ----------------------------------------------------------------
    print("[auto-login] Exchanging request_token for access_token...")
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from brokers.zerodha_auth import ZerodhaTokenManager
    tm = ZerodhaTokenManager(api_key=api_key, api_secret=api_secret)
    access_token = tm.generate_token(request_token)
    print(f"[auto-login] Access token saved. Session is ready for today ({date.today()}).")
    return access_token


def _save_debug_screenshot(page) -> None:
    try:
        path = Path("journaling/logs/auto_login_debug.png")
        path.parent.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(path))
        print(f"[auto-login] Debug screenshot saved: {path}")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Zerodha auto-login")
    parser.add_argument("--check", action="store_true",
                        help="Only check if today's token exists; exit 0 if valid, 1 if not")
    parser.add_argument("--visible", action="store_true",
                        help="Run browser visibly (useful for debugging)")
    parser.add_argument("--force", action="store_true",
                        help="Re-login even if today's token already exists")
    parser.add_argument("--totp", default="",
                        help="Manually supply the 6-digit TOTP code")
    args = parser.parse_args()

    if args.check:
        if _check_today_token():
            print(f"[auto-login] Token valid for today ({date.today()}). No login needed.")
            sys.exit(0)
        else:
            print("[auto-login] No valid token for today.")
            sys.exit(1)

    if not args.force and _check_today_token():
        print(f"[auto-login] Token already valid for today ({date.today()}). Skipping login.")
        sys.exit(0)

    logging.basicConfig(level=logging.WARNING)
    auto_login(headless=not args.visible, manual_totp=args.totp)


if __name__ == "__main__":
    main()
