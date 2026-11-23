import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import pandas as pd
import pytz

from .base import DataProviderBase, TickData, SymbolInfo, DataUnavailableError

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

KITE_INTERVAL_MAP = {
    "1m": "minute", "3m": "3minute", "5m": "5minute",
    "15m": "15minute", "30m": "30minute", "60m": "60minute",
    "1h": "60minute", "1d": "day",
}

# Kite's per-request lookback cap (days) for each interval. Longer ranges are chunked.
KITE_MAX_DAYS = {
    "minute": 60, "3minute": 100, "5minute": 100, "15minute": 200,
    "30minute": 200, "60minute": 400, "day": 2000,
}

# Symbols used elsewhere in the bot (yfinance style) -> Kite NSE tradingsymbol
SYMBOL_ALIASES = {
    "^NSEI": "NIFTY 50", "NIFTY50.NS": "NIFTY 50", "^NIFTY50": "NIFTY 50", "NIFTY50": "NIFTY 50",
    "^NSEBANK": "NIFTY BANK", "BANKNIFTY": "NIFTY BANK",
    "^INDIAVIX": "INDIA VIX", "INDIA_VIX": "INDIA VIX",
    "^CNXIT": "NIFTY IT", "NIFTY_IT": "NIFTY IT",
}

# Kite allows 3 historical-data requests/second
_MIN_REQUEST_GAP = 0.36


class ZerodhaMarketDataProvider(DataProviderBase):

    def __init__(self, api_key: str, access_token: str):
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._tokens: Optional[Dict[str, int]] = None
        self._lock = threading.Lock()
        self._last_request = 0.0

    @property
    def provider_name(self) -> str:
        return "ZerodhaMarketDataProvider"

    @property
    def supports_realtime(self) -> bool:
        return True

    @property
    def supports_intraday_history(self) -> bool:
        return True

    def get_historical(
        self, symbol: str, interval: str,
        from_date: datetime, to_date: datetime,
    ) -> pd.DataFrame:
        token = self._get_instrument_token(symbol)
        kite_interval = KITE_INTERVAL_MAP.get(interval, "day")
        step = timedelta(days=KITE_MAX_DAYS[kite_interval])

        start, end = self._naive_ist(from_date), self._naive_ist(to_date)
        records: list = []
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + step, end)
            self._throttle()
            records.extend(self.kite.historical_data(
                instrument_token=token, from_date=cursor, to_date=chunk_end,
                interval=kite_interval,
            ))
            cursor = chunk_end + timedelta(seconds=1)

        if not records:
            raise DataUnavailableError(f"No data from Zerodha for {symbol}")
        df = pd.DataFrame(records).rename(columns={"date": "timestamp"})
        ts = pd.to_datetime(df["timestamp"])
        df["timestamp"] = ts.dt.tz_localize(IST) if ts.dt.tz is None else ts.dt.tz_convert(IST)
        df = df.drop_duplicates("timestamp").sort_values("timestamp")
        df = df[df["volume"] > 0] if symbol not in self._index_names() else df
        df["symbol"] = symbol
        return df[["timestamp", "open", "high", "low", "close", "volume", "symbol"]].reset_index(drop=True)

    def get_quote(self, symbol: str) -> TickData:
        return self.get_quotes([symbol])[symbol]

    def get_quotes(self, symbols: List[str]) -> Dict[str, TickData]:
        """One API call for many symbols (Kite allows up to 500 per quote request)."""
        keys = {f"NSE:{self._resolve(s)}": s for s in symbols}
        raw = self.kite.quote(list(keys))
        out: Dict[str, TickData] = {}
        for key, sym in keys.items():
            q = raw.get(key)
            if not q:
                continue
            depth = q.get("depth", {})
            buy, sell = depth.get("buy") or [{}], depth.get("sell") or [{}]
            ltp = q["last_price"]
            prev = q.get("ohlc", {}).get("close") or 0
            out[sym] = TickData(
                symbol=sym, timestamp=datetime.now(IST), ltp=ltp,
                bid=buy[0].get("price", ltp), ask=sell[0].get("price", ltp),
                volume=q.get("volume_traded", q.get("volume", 0)), oi=q.get("oi", 0),
                change_pct=round((ltp - prev) / prev * 100, 2) if prev else 0.0,
            )
        if not out:
            raise DataUnavailableError(f"No quotes returned for {symbols}")
        return out

    def get_index_data(self, index_name: str) -> dict:
        kite_sym = f"NSE:{self._resolve(index_name)}"
        q = self.kite.quote([kite_sym]).get(kite_sym)
        if not q:
            return {}
        prev = q["ohlc"]["close"]
        return {"name": index_name, "close": q["last_price"],
                "change_pct": round((q["last_price"] - prev) / prev * 100, 2) if prev else 0.0,
                "high": q["ohlc"]["high"], "low": q["ohlc"]["low"],
                "prev_close": prev, "volume": q.get("volume", 0)}

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(symbol=symbol, isin="", exchange="NSE", sector="", industry="")

    def get_market_status(self) -> dict:
        from utils.time_utils import market_status, is_market_open
        return {"status": market_status(), "is_open": is_market_open()}

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _index_names() -> set:
        return set(SYMBOL_ALIASES)

    @staticmethod
    def _resolve(symbol: str) -> str:
        if symbol in SYMBOL_ALIASES:
            return SYMBOL_ALIASES[symbol]
        return symbol[:-3] if symbol.endswith(".NS") else symbol

    @staticmethod
    def _naive_ist(dt: datetime) -> datetime:
        if dt.tzinfo is not None:
            dt = dt.astimezone(IST).replace(tzinfo=None)
        return dt

    def _throttle(self):
        with self._lock:
            wait = _MIN_REQUEST_GAP - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()

    def _get_instrument_token(self, symbol: str) -> int:
        if self._tokens is None:
            self._tokens = {i["tradingsymbol"]: i["instrument_token"]
                            for i in self.kite.instruments("NSE")}
        name = self._resolve(symbol)
        try:
            return self._tokens[name]
        except KeyError:
            raise DataUnavailableError(f"Instrument token not found: {symbol}")
