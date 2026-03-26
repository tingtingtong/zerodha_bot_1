"""
NSE Corporate Actions Scraper
Fetches upcoming ex-dividend dates and board meeting / results dates from NSE
and loads them into the EventCalendar.

NSE's public API requires a browser-like session (cookies from homepage visit).
No API key needed — this is the same data visible on nseindia.com.
"""
import logging
import time
from datetime import date, datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NSE_BASE = "https://www.nseindia.com"
NSE_CA_API = (
    "https://www.nseindia.com/api/corporates-corporateActions"
    "?index=equities&from_date={from_dt}&to_date={to_dt}"
)
NSE_BM_API = (
    "https://www.nseindia.com/api/home-corporate-actions"
    "?index=equities&from_date={from_dt}&to_date={to_dt}"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


def _get_session() -> Optional[requests.Session]:
    """Create a session with NSE cookies.

    The NSE homepage (nseindia.com/) returns 403 via Cloudflare.
    Visiting the corporate-filings-actions subpage returns 200 and sets
    the required session cookies (nsit, bm_sz) for subsequent API calls.
    """
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        seed_url = f"{NSE_BASE}/companies-listing/corporate-filings-actions"
        resp = session.get(seed_url, timeout=15)
        resp.raise_for_status()
        time.sleep(1)  # polite delay before hitting the API
        return session
    except Exception as e:
        logger.warning(f"NSE session init failed: {e}")
        return None


def _fmt(d: date) -> str:
    return d.strftime("%d-%m-%Y")


def fetch_ex_dates(from_date: date, to_date: date,
                   session: Optional[requests.Session] = None) -> list:
    """
    Fetch ex-dividend/ex-bonus/ex-split records from NSE for the date range.
    Returns list of dicts: [{symbol, ex_date, purpose}, ...]
    """
    if session is None:
        session = _get_session()
    if session is None:
        return []

    url = NSE_CA_API.format(from_dt=_fmt(from_date), to_dt=_fmt(to_date))
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for row in data:
            sym = row.get("symbol", "").strip().upper()
            ex_dt_str = row.get("exDate") or row.get("ex_date", "")
            purpose = row.get("subject", row.get("purpose", "")).strip()
            if not sym or not ex_dt_str:
                continue
            try:
                ex_dt = datetime.strptime(ex_dt_str.strip(), "%d-%b-%Y").date()
            except ValueError:
                try:
                    ex_dt = datetime.strptime(ex_dt_str.strip(), "%Y-%m-%d").date()
                except ValueError:
                    continue
            results.append({"symbol": sym, "ex_date": ex_dt, "purpose": purpose})
        logger.info(f"NSE ex-dates fetched: {len(results)} records ({_fmt(from_date)} to {_fmt(to_date)})")
        return results
    except Exception as e:
        logger.warning(f"NSE ex-dates fetch failed: {e}")
        return []


def fetch_board_meetings(from_date: date, to_date: date,
                         session: Optional[requests.Session] = None) -> list:
    """
    Fetch board meetings / results dates from NSE for the date range.
    Returns list of dicts: [{symbol, meeting_date, purpose}, ...]
    """
    if session is None:
        session = _get_session()
    if session is None:
        return []

    url = NSE_BM_API.format(from_dt=_fmt(from_date), to_dt=_fmt(to_date))
    try:
        resp = session.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results = []
        for row in data:
            sym = row.get("symbol", "").strip().upper()
            dt_str = row.get("bm_date") or row.get("date", "")
            purpose = row.get("bm_purpose", row.get("purpose", "")).strip().lower()
            if not sym or not dt_str:
                continue
            # Only care about financial results announcements
            if not any(k in purpose for k in ("result", "financial", "quarter", "earning")):
                continue
            try:
                dt = datetime.strptime(dt_str.strip(), "%d-%b-%Y").date()
            except ValueError:
                try:
                    dt = datetime.strptime(dt_str.strip(), "%Y-%m-%d").date()
                except ValueError:
                    continue
            results.append({"symbol": sym, "meeting_date": dt, "purpose": purpose})
        logger.info(f"NSE board meetings fetched: {len(results)} records")
        return results
    except Exception as e:
        logger.warning(f"NSE board meetings fetch failed: {e}")
        return []


def load_into_calendar(event_calendar, lookahead_days: int = 7) -> dict:
    """
    Main entry point — fetch next `lookahead_days` of NSE corporate actions
    and load them into the provided EventCalendar instance.

    Returns summary: {ex_dates: N, board_meetings: N, symbols: [...]}
    """
    today = date.today()
    to_date = today + timedelta(days=lookahead_days)

    session = _get_session()
    if session is None:
        logger.warning("Could not connect to NSE — skipping corporate actions load")
        return {"ex_dates": 0, "board_meetings": 0, "symbols": []}

    ex_records = fetch_ex_dates(today, to_date, session)
    bm_records = fetch_board_meetings(today, to_date, session)

    loaded_symbols = set()

    for rec in ex_records:
        event_calendar.register_ex_date(rec["symbol"], rec["ex_date"])
        loaded_symbols.add(rec["symbol"])
        logger.debug(f"Ex-date: {rec['symbol']} on {rec['ex_date']} ({rec['purpose']})")

    for rec in bm_records:
        event_calendar.register_earnings(rec["symbol"], rec["meeting_date"])
        loaded_symbols.add(rec["symbol"])
        logger.debug(f"Board meeting: {rec['symbol']} on {rec['meeting_date']} ({rec['purpose']})")

    summary = {
        "ex_dates": len(ex_records),
        "board_meetings": len(bm_records),
        "symbols": sorted(loaded_symbols),
    }
    if loaded_symbols:
        logger.info(
            f"Corporate actions loaded: {len(ex_records)} ex-dates, "
            f"{len(bm_records)} board meetings, "
            f"{len(loaded_symbols)} unique symbols blocked"
        )
    return summary
