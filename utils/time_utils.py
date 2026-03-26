from datetime import datetime, time, date, timedelta
from typing import Optional
import pytz

IST = pytz.timezone("Asia/Kolkata")

MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)
PRE_OPEN_START = time(9, 0)

NSE_HOLIDAYS = {
    # 2024
    date(2024, 1, 22), date(2024, 3, 25), date(2024, 3, 29),
    date(2024, 4, 11), date(2024, 4, 14), date(2024, 4, 17),
    date(2024, 4, 21), date(2024, 5, 23), date(2024, 6, 17),
    date(2024, 7, 17), date(2024, 8, 15), date(2024, 10, 2),
    date(2024, 10, 24), date(2024, 11, 1), date(2024, 11, 15),
    date(2024, 11, 20), date(2024, 12, 25),
    # 2025
    date(2025, 2, 26), date(2025, 3, 14), date(2025, 3, 31),
    date(2025, 4, 10), date(2025, 4, 14), date(2025, 4, 18),
    date(2025, 5, 1), date(2025, 8, 15), date(2025, 8, 27),
    date(2025, 10, 2), date(2025, 10, 21), date(2025, 10, 22),
    date(2025, 11, 5), date(2025, 12, 25),
    # 2026 — source: NSE official holiday list
    date(2026, 1, 26),   # Republic Day
    date(2026, 2, 17),   # Mahashivratri
    date(2026, 3, 20),   # Holi (Dhuleti)
    date(2026, 3, 26),   # Ram Navami  ← TODAY
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 10),   # Dr. Ambedkar Jayanti / Mahavir Jayanti
    date(2026, 4, 14),   # Dr. Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day / Labour Day
    date(2026, 7, 6),    # Bakri Id (Eid ul-Adha)
    date(2026, 8, 17),   # Independence Day (Aug 15 falls on weekend, observed Mon)
    date(2026, 9, 17),   # Milad-un-Nabi / Id-E-Milad
    date(2026, 10, 2),   # Gandhi Jayanti / Dussehra
    date(2026, 10, 21),  # Diwali Laxmi Puja (muhurat trading only)
    date(2026, 11, 4),   # Diwali Balipratipada
    date(2026, 11, 25),  # Gurunanak Jayanti
    date(2026, 12, 25),  # Christmas
}


def now_ist() -> datetime:
    return datetime.now(IST)


def is_trading_day(d: Optional[date] = None) -> bool:
    d = d or now_ist().date()
    return d.weekday() < 5 and d not in NSE_HOLIDAYS


def market_status() -> str:
    now = now_ist()
    if not is_trading_day(now.date()):
        return "closed"
    t = now.time()
    if PRE_OPEN_START <= t < MARKET_OPEN:
        return "pre-open"
    if MARKET_OPEN <= t <= MARKET_CLOSE:
        return "open"
    return "closed"


def is_market_open() -> bool:
    return market_status() == "open"


def minutes_to_close() -> int:
    now = now_ist()
    close_dt = now.replace(hour=15, minute=30, second=0, microsecond=0)
    delta = (close_dt - now).total_seconds() / 60
    return max(0, int(delta))


def ist_time_str(dt: Optional[datetime] = None) -> str:
    dt = dt or now_ist()
    if dt.tzinfo is None:
        dt = IST.localize(dt)
    return dt.astimezone(IST).strftime("%Y-%m-%d %H:%M:%S IST")


def next_trading_day(from_date: Optional[date] = None) -> date:
    d = from_date or now_ist().date()
    d += timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d
