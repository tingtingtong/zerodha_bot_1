import logging
from datetime import date, datetime, timedelta
from typing import Set
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

RBI_POLICY_DATES = {
    date(2024, 4, 5), date(2024, 6, 7), date(2024, 8, 8),
    date(2024, 10, 9), date(2024, 12, 6),
    date(2025, 2, 7), date(2025, 4, 9), date(2025, 6, 6),
    date(2025, 8, 8), date(2025, 10, 8), date(2025, 12, 5),
}

BUDGET_DATES = {date(2025, 2, 1)}


class EventCalendar:

    def __init__(self):
        self._ex_dates: dict = {}
        self._earnings_dates: dict = {}

    def has_event_soon(self, symbol: str, within_days: int = 3) -> bool:
        today = datetime.now(IST).date()
        window = {today + timedelta(days=i) for i in range(-1, within_days + 1)}
        for d in self._ex_dates.get(symbol, []):
            if d in window:
                return True
        for d in self._earnings_dates.get(symbol, []):
            if d in window:
                return True
        return False

    def is_macro_event_day(self, d: date = None) -> bool:
        d = d or datetime.now(IST).date()
        return d in RBI_POLICY_DATES or d in BUDGET_DATES

    def get_event_symbols_today(self, within_days: int = 3) -> Set[str]:
        result = set()
        today = datetime.now(IST).date()
        window = {today + timedelta(days=i) for i in range(-1, within_days + 1)}
        for sym, dates in {**self._ex_dates, **self._earnings_dates}.items():
            if any(d in window for d in dates):
                result.add(sym)
        return result

    def register_ex_date(self, symbol: str, ex_date: date):
        self._ex_dates.setdefault(symbol, []).append(ex_date)

    def register_earnings(self, symbol: str, earnings_date: date):
        self._earnings_dates.setdefault(symbol, []).append(earnings_date)

    def load_from_nse_csv(self, filepath: str):
        try:
            import pandas as pd
            df = pd.read_csv(filepath)
            for _, row in df.iterrows():
                try:
                    sym = str(row.get("SYMBOL", "")).strip()
                    ex_dt = pd.to_datetime(row.get("EX DATE", "")).date()
                    if sym and ex_dt:
                        self.register_ex_date(sym, ex_dt)
                except Exception:
                    pass
            logger.info(f"Loaded ex-dates: {len(self._ex_dates)} symbols from {filepath}")
        except Exception as e:
            logger.warning(f"Could not load {filepath}: {e}")
