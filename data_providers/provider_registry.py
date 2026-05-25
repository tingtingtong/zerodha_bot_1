import logging
import time
from typing import List
from datetime import datetime
import pandas as pd

from .base import DataProviderBase, DataUnavailableError

logger = logging.getLogger(__name__)

# Candle cache TTL (seconds) — re-fetch only after the current candle period expires
_INTERVAL_TTL = {
    "1m": 60, "5m": 300, "15m": 900, "30m": 1800,
    "1h": 3600, "60m": 3600, "1d": 1800,
}


class DataProviderRegistry:

    # After this many consecutive auth failures, skip the provider for the session
    _AUTH_FAILURE_THRESHOLD = 3

    def __init__(self, providers: List[DataProviderBase]):
        if not providers:
            raise ValueError("At least one data provider required")
        self.providers = providers
        self._auth_failures: dict = {}   # provider_name -> consecutive auth fail count
        self._skip_providers: set = set()
        self._candle_cache: dict = {}    # (symbol, interval) -> (df, fetched_at)

    def _is_auth_error(self, err: Exception) -> bool:
        msg = str(err).lower()
        return "api_key" in msg or "access_token" in msg or "incorrect" in msg or "permission" in msg

    def get_historical(self, symbol: str, interval: str,
                       from_date: datetime, to_date: datetime) -> pd.DataFrame:
        ttl = _INTERVAL_TTL.get(interval, 900)
        cache_key = (symbol, interval)
        cached_df, cached_at = self._candle_cache.get(cache_key, (None, 0))
        if cached_df is not None and (time.time() - cached_at) < ttl:
            logger.debug(f"[cache] {symbol}/{interval} hit (age {int(time.time()-cached_at)}s)")
            return cached_df

        last_err = None
        for p in self.providers:
            if p.provider_name in self._skip_providers:
                continue
            try:
                df = p.get_historical(symbol, interval, from_date, to_date)
                if df is not None and len(df) >= 2:
                    logger.debug(f"[{p.provider_name}] {symbol}/{interval} OK ({len(df)} rows)")
                    self._auth_failures[p.provider_name] = 0
                    self._candle_cache[cache_key] = (df, time.time())
                    return df
            except Exception as e:
                last_err = e
                if self._is_auth_error(e):
                    self._auth_failures[p.provider_name] = self._auth_failures.get(p.provider_name, 0) + 1
                    if self._auth_failures[p.provider_name] >= self._AUTH_FAILURE_THRESHOLD:
                        self._skip_providers.add(p.provider_name)
                        logger.warning(f"[{p.provider_name}] auth failures >= {self._AUTH_FAILURE_THRESHOLD} — skipping for this session")
                else:
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
