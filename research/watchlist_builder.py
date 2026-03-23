import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
import pandas as pd
import pytz

from .stock_screener import StockScreener, StockScore
from .market_regime import MarketRegimeDetector, RegimeResult

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

NIFTY_200 = [
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS",
    "HINDUNILVR", "SBIN", "BAJFINANCE", "BHARTIARTL", "KOTAKBANK",
    "ITC", "LT", "HCLTECH", "AXISBANK", "ASIANPAINT",
    "MARUTI", "SUNPHARMA", "TITAN", "ULTRACEMCO", "NESTLEIND",
    "WIPRO", "POWERGRID", "TECHM", "TATAMOTORS", "ADANIENT",
    "BAJAJ-AUTO", "NTPC", "ONGC", "JSWSTEEL", "TATASTEEL",
    "INDUSINDBK", "M&M", "COALINDIA", "HDFCLIFE", "SBILIFE",
    "BRITANNIA", "DIVISLAB", "CIPLA", "DRREDDY", "APOLLOHOSP",
    "EICHERMOT", "HEROMOTOCO", "BPCL", "IOC", "GRASIM",
    "SHREECEM", "HINDALCO", "VEDL", "TATACONSUM", "PIDILITIND",
]

ETF_UNIVERSE = ["NIFTYBEES", "BANKBEES", "GOLDBEES", "JUNIORBEES", "ITBEES"]


@dataclass
class WatchlistEntry:
    symbol: str
    score: float
    price: float
    atr_pct: float
    rsi: float
    volume_spike: float
    sector: str
    is_etf: bool
    rank: int
    added_at: datetime
    notes: str = ""


class WatchlistBuilder:

    def __init__(self, screener: Optional[StockScreener] = None):
        self.screener = screener or StockScreener()
        self.regime_detector = MarketRegimeDetector()
        self._last_watchlist: List[WatchlistEntry] = []
        self._last_refresh: Optional[datetime] = None

    def build(self, data_registry, nifty_daily: pd.DataFrame, vix: float = 15.0,
              config: dict = None, event_symbols: List[str] = None) -> Tuple[RegimeResult, List[WatchlistEntry]]:

        config = config or {}
        event_set = set(event_symbols or [])
        max_size = config.get("max_watchlist_size", 10)
        min_score = config.get("min_score_for_watchlist", 60)
        universe_size = config.get("universe_size", 50)

        regime = self.regime_detector.detect(nifty_daily, vix=vix)
        logger.info(f"Regime: {regime.regime.value} | Rec: {regime.recommendation} | VIX: {vix:.1f}")

        if regime.recommendation == "stay_flat":
            self._last_watchlist = []
            self._last_refresh = datetime.now(IST)
            return regime, []

        from_date = datetime.now(IST) - timedelta(days=90)
        to_date = datetime.now(IST)
        scores: List[StockScore] = []

        for sym in ETF_UNIVERSE:
            try:
                df = data_registry.get_historical(sym, "1d", from_date, to_date)
                s = self.screener.score(sym, df, sector="ETF")
                if s.tradeable:
                    scores.append(s)
            except Exception as e:
                logger.debug(f"ETF {sym}: {e}")

        for sym in NIFTY_200[:universe_size]:
            if sym in event_set:
                continue
            try:
                df = data_registry.get_historical(sym, "1d", from_date, to_date)
                s = self.screener.score(sym, df, upcoming_event=(sym in event_set))
                if s.tradeable:
                    scores.append(s)
            except Exception as e:
                logger.debug(f"{sym}: {e}")

        qualified = sorted([s for s in scores if s.total_score >= min_score],
                           key=lambda x: x.total_score, reverse=True)[:max_size]

        watchlist = [
            WatchlistEntry(
                symbol=s.symbol, score=s.total_score, price=s.price,
                atr_pct=s.atr_pct, rsi=s.rsi_14, volume_spike=s.volume_spike,
                sector=s.sector, is_etf=s.sector == "ETF", rank=rank,
                added_at=datetime.now(IST),
                notes=f"rsi{s.rsi_14:.0f}_vol{s.volume_spike:.1f}x",
            )
            for rank, s in enumerate(qualified, 1)
        ]

        self._last_watchlist = watchlist
        self._last_refresh = datetime.now(IST)
        logger.info(f"Watchlist: {len(watchlist)} symbols | Top5: {[w.symbol for w in watchlist[:5]]}")
        return regime, watchlist

    @property
    def active_candidates(self) -> List[WatchlistEntry]:
        return self._last_watchlist[:5]

    @property
    def last_refresh(self) -> Optional[datetime]:
        return self._last_refresh
