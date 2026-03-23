import logging
from typing import List
from datetime import datetime
import pandas as pd

from .base import DataProviderBase, DataUnavailableError

logger = logging.getLogger(__name__)


class DataProviderRegistry:

    def __init__(self, providers: List[DataProviderBase]):
        if not providers:
            raise ValueError("At least one data provider required")
        self.providers = providers

    def get_historical(self, symbol: str, interval: str,
                       from_date: datetime, to_date: datetime) -> pd.DataFrame:
        last_err = None
        for p in self.providers:
            try:
                df = p.get_historical(symbol, interval, from_date, to_date)
                if df is not None and len(df) >= 2:
                    logger.debug(f"[{p.provider_name}] {symbol}/{interval} OK ({len(df)} rows)")
                    return df
            except Exception as e:
                last_err = e
                logger.warning(f"[{p.provider_name}] {symbol}/{interval} failed: {e}")
        raise DataUnavailableError(f"All providers failed for {symbol}/{interval}. Last: {last_err}")

    def get_quote(self, symbol: str):
        for p in self.providers:
            try:
                return p.get_quote(symbol)
            except Exception as e:
                logger.warning(f"Quote {symbol} failed from {p.provider_name}: {e}")
        raise DataUnavailableError(f"All providers failed for quote {symbol}")

    def get_index_data(self, index_name: str) -> dict:
        for p in self.providers:
            try:
                data = p.get_index_data(index_name)
                if data:
                    return data
            except Exception as e:
                logger.warning(f"Index {index_name} from {p.provider_name}: {e}")
        return {"name": index_name, "close": 0, "change_pct": 0}

    def get_market_status(self) -> dict:
        return self.providers[0].get_market_status()

    @classmethod
    def build_free_only(cls) -> "DataProviderRegistry":
        from .free_nse_provider import FreeNSEProvider
        return cls([FreeNSEProvider()])

    @classmethod
    def build_zerodha_primary(cls, api_key: str, access_token: str) -> "DataProviderRegistry":
        from .free_nse_provider import FreeNSEProvider
        from .zerodha_provider import ZerodhaMarketDataProvider
        return cls([ZerodhaMarketDataProvider(api_key, access_token), FreeNSEProvider()])

    @classmethod
    def from_config(cls, config: dict, api_key: str = "", access_token: str = "") -> "DataProviderRegistry":
        mode = config.get("data", {}).get("provider_mode", "free_only")
        if mode == "free_only":
            return cls.build_free_only()
        if mode in ("zerodha", "zerodha_with_fallback"):
            return cls.build_zerodha_primary(api_key, access_token)
        return cls.build_free_only()
