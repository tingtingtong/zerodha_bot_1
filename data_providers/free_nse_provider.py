import time
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Optional
import pytz

from .base import DataProviderBase, TickData, SymbolInfo, DataUnavailableError

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

YFINANCE_INTERVAL_MAP = {
    "1m": "1m", "5m": "5m", "15m": "15m",
    "30m": "30m", "1h": "60m", "1d": "1d",
}

INDEX_TICKER_MAP = {
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "NIFTY_IT": "^CNXIT",
    "NIFTY_MIDCAP": "^NSEMDCP50",
    "INDIA_VIX": "^INDIAVIX",
}


class FreeNSEProvider(DataProviderBase):

    SUFFIX = ".NS"
    RATE_LIMIT_DELAY = 0.35

    @property
    def provider_name(self) -> str:
        return "FreeNSEProvider"

    @property
    def supports_realtime(self) -> bool:
        return False

    @property
    def supports_intraday_history(self) -> bool:
        return True

    def get_historical(
        self, symbol: str, interval: str,
        from_date: datetime, to_date: datetime,
    ) -> pd.DataFrame:
        try:
            df = self._fetch_yfinance(symbol, interval, from_date, to_date)
            if df is not None and len(df) >= 2:
                return self._normalize(df, symbol)
        except Exception as e:
            logger.warning(f"[yfinance] {symbol}/{interval}: {e}")

        if interval == "1d":
            try:
                df = self._fetch_nsepy(symbol, from_date, to_date)
                if df is not None and len(df) >= 2:
                    return self._normalize_nsepy(df, symbol)
            except Exception as e:
                logger.warning(f"[nsepy] {symbol}: {e}")

        raise DataUnavailableError(f"No data for {symbol}/{interval}")

    def _yf_symbol(self, symbol: str) -> str:
        """Return yfinance symbol — index tickers (^) never get .NS suffix."""
        return symbol if symbol.startswith("^") else f"{symbol}{self.SUFFIX}"

    def _fetch_yfinance(self, symbol, interval, from_date, to_date):
        import yfinance as yf
        yf_interval = YFINANCE_INTERVAL_MAP.get(interval, "1d")
        time.sleep(self.RATE_LIMIT_DELAY)
        df = yf.Ticker(self._yf_symbol(symbol)).history(
            start=from_date.strftime("%Y-%m-%d"),
            end=to_date.strftime("%Y-%m-%d"),
            interval=yf_interval, auto_adjust=True, prepost=False,
        )
        return df if len(df) > 0 else None

    def _fetch_nsepy(self, symbol, from_date, to_date):
        # nsepy's upstream NSE URLs are no longer reliable; skip gracefully
        raise DataUnavailableError("nsepy disabled — NSE API deprecated")

    def _normalize(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        if "timestamp" not in df.columns:
            df["timestamp"] = df.index
        keep = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True).dt.tz_convert(IST)
        df = df.dropna(subset=["close"])
        df = df[df["volume"] > 0]
        df["symbol"] = symbol
        return df.reset_index(drop=True)

    def _normalize_nsepy(self, df: pd.DataFrame, symbol: str) -> pd.DataFrame:
        df = df.copy()
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                  "Close": "close", "Volume": "volume"})
        df["timestamp"] = pd.to_datetime(df.index)
        keep = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
        df = df[keep].copy()
        df["symbol"] = symbol
        return df.reset_index(drop=True)

    def get_quote(self, symbol: str) -> TickData:
        import yfinance as yf
        info = yf.Ticker(self._yf_symbol(symbol)).fast_info
        ltp = float(info.last_price) if info.last_price else 0.0
        prev = float(info.previous_close) if info.previous_close else ltp
        chg = ((ltp - prev) / prev * 100) if prev > 0 else 0.0
        return TickData(
            symbol=symbol, timestamp=datetime.now(IST),
            ltp=ltp, bid=ltp, ask=ltp,
            volume=int(info.three_month_average_volume or 0),
            change_pct=round(chg, 2),
        )

    def get_index_data(self, index_name: str) -> dict:
        import yfinance as yf
        sym = INDEX_TICKER_MAP.get(index_name, "^NSEI")
        hist = yf.Ticker(sym).history(period="5d", interval="1d", auto_adjust=True)
        if len(hist) < 2:
            return {"name": index_name, "close": 0, "change_pct": 0}
        today, yesterday = hist.iloc[-1], hist.iloc[-2]
        chg = ((today["Close"] - yesterday["Close"]) / yesterday["Close"]) * 100
        return {
            "name": index_name,
            "close": float(today["Close"]),
            "change_pct": round(float(chg), 2),
            "high": float(today["High"]),
            "low": float(today["Low"]),
            "volume": int(today["Volume"]),
            "prev_close": float(yesterday["Close"]),
        }

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        import yfinance as yf
        try:
            info = yf.Ticker(self._yf_symbol(symbol)).info
        except Exception:
            info = {}
        return SymbolInfo(
            symbol=symbol, isin=info.get("isin", ""),
            exchange="NSE", sector=info.get("sector", "Unknown"),
            industry=info.get("industry", "Unknown"),
            market_cap=info.get("marketCap"),
        )

    def get_market_status(self) -> dict:
        from utils.time_utils import market_status, is_market_open
        return {
            "status": market_status(),
            "is_open": is_market_open(),
            "timestamp": datetime.now(IST).isoformat(),
        }
