from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
from datetime import datetime
import pandas as pd


@dataclass
class TickData:
    symbol: str
    timestamp: datetime
    ltp: float
    bid: float
    ask: float
    volume: int
    oi: Optional[int] = None
    change_pct: float = 0.0


@dataclass
class SymbolInfo:
    symbol: str
    isin: str
    exchange: str
    sector: str
    industry: str
    market_cap: Optional[float] = None
    lot_size: int = 1
    face_value: float = 1.0


class DataUnavailableError(Exception):
    pass


class DataProviderBase(ABC):

    @abstractmethod
    def get_historical(
        self, symbol: str, interval: str,
        from_date: datetime, to_date: datetime,
    ) -> pd.DataFrame:
        """Return DataFrame with columns: timestamp, open, high, low, close, volume"""

    @abstractmethod
    def get_quote(self, symbol: str) -> TickData:
        pass

    @abstractmethod
    def get_symbol_info(self, symbol: str) -> SymbolInfo:
        pass

    @abstractmethod
    def get_index_data(self, index_name: str) -> dict:
        pass

    @abstractmethod
    def get_market_status(self) -> dict:
        pass

    def is_available(self) -> bool:
        return True

    @property
    @abstractmethod
    def provider_name(self) -> str:
        pass

    @property
    @abstractmethod
    def supports_realtime(self) -> bool:
        pass

    @property
    @abstractmethod
    def supports_intraday_history(self) -> bool:
        pass
