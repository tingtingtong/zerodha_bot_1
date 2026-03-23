import logging
from datetime import datetime
from typing import Optional
import pandas as pd
import pytz

from .base import DataProviderBase, TickData, SymbolInfo, DataUnavailableError

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

KITE_INTERVAL_MAP = {
    "1m": "minute", "3m": "3minute", "5m": "5minute",
    "15m": "15minute", "30m": "30minute", "1h": "60minute", "1d": "day",
}


class ZerodhaMarketDataProvider(DataProviderBase):

    def __init__(self, api_key: str, access_token: str):
        from kiteconnect import KiteConnect
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        self._instruments_cache: Optional[list] = None

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
        records = self.kite.historical_data(
            instrument_token=token, from_date=from_date, to_date=to_date,
            interval=KITE_INTERVAL_MAP.get(interval, "day"),
        )
        if not records:
            raise DataUnavailableError(f"No data from Zerodha for {symbol}")
        df = pd.DataFrame(records)
        df = df.rename(columns={"date": "timestamp"})
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["symbol"] = symbol
        return df[["timestamp", "open", "high", "low", "close", "volume", "symbol"]]

    def get_quote(self, symbol: str) -> TickData:
        q = self.kite.quote([f"NSE:{symbol}"])[f"NSE:{symbol}"]
        db = q.get("depth", {}).get("buy", [{}])
        ds = q.get("depth", {}).get("sell", [{}])
        return TickData(
            symbol=symbol, timestamp=datetime.now(IST),
            ltp=q["last_price"],
            bid=db[0].get("price", q["last_price"]) if db else q["last_price"],
            ask=ds[0].get("price", q["last_price"]) if ds else q["last_price"],
            volume=q.get("volume_traded", 0), oi=q.get("oi", 0),
            change_pct=q.get("change", 0.0),
        )

    def get_index_data(self, index_name: str) -> dict:
        index_map = {"NIFTY50": "NSE:NIFTY 50", "BANKNIFTY": "NSE:NIFTY BANK",
                     "INDIA_VIX": "NSE:INDIA VIX"}
        kite_sym = index_map.get(index_name)
        if not kite_sym:
            return {}
        q = self.kite.quote([kite_sym])[kite_sym]
        return {"name": index_name, "close": q["last_price"],
                "change_pct": q.get("change", 0.0),
                "high": q["ohlc"]["high"], "low": q["ohlc"]["low"],
                "prev_close": q["ohlc"]["close"], "volume": q.get("volume", 0)}

    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        return SymbolInfo(symbol=symbol, isin="", exchange="NSE", sector="", industry="")

    def get_market_status(self) -> dict:
        from utils.time_utils import market_status, is_market_open
        return {"status": market_status(), "is_open": is_market_open()}

    def _get_instrument_token(self, symbol: str) -> int:
        if self._instruments_cache is None:
            self._instruments_cache = self.kite.instruments("NSE")
        for inst in self._instruments_cache:
            if inst["tradingsymbol"] == symbol:
                return inst["instrument_token"]
        raise DataUnavailableError(f"Instrument token not found: {symbol}")
