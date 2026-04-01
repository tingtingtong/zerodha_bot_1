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

# Full universe — 200 symbols tested via 5yr backtest (2021-2026).
# Ordered roughly by backtest rank (best performers first).
# Hard-avoid list excluded: HDFCBANK, BIOCON, IGL, CONCOR, ABFRL, SYNGENE,
#   AMBUJACEM, AUROPHARMA, MASFIN, HDFCLIFE, METROPOLIS, PGHH, TANLA, IPCALAB, HFCL
NIFTY_200 = [
    # Tier 1 — top performers (Sharpe > 5, WR > 55%)
    "TITAGARH", "RVNL", "NBCC", "ONGC", "RAYMOND",
    "BAJAJHLDNG", "WELCORP", "CUMMINSIND", "SBIN", "PRESTIGE",
    "VEDL", "SCHAEFFLER", "BHEL", "HUDCO", "SRF",
    "DIXON", "INDUSTOWER", "KPRMILL", "COFORGE", "CRISIL",
    # Tier 2 — strong (Sharpe 3-5, WR > 50%)
    "POLYMED", "ASHOKLEY", "JINDALSTEL", "SKFINDIA", "SAIL",
    "TRITURBINE", "M&M", "YESBANK", "PERSISTENT", "HINDALCO",
    "GLAXO", "FINEORG", "PNB", "TATAELXSI", "LINDEINDIA",
    "JKLAKSHMI", "BLUESTARCO", "ACC", "ADANIPORTS", "PFC",
    "SUNPHARMA", "GAIL", "IRB", "POWERGRID", "CARBORUNIV",
    "BRIGADE", "TASTYBITE", "LALPATHLAB", "UPL", "TIINDIA",
    "HCLTECH", "PIIND", "MMTC", "BAJAJ-AUTO", "AAVAS",
    "TITAN", "TRENT", "GSFC", "BHARTIARTL", "RADICO",
    "MARUTI", "ABB", "AXISBANK", "NATIONALUM", "RAILTEL",
    "JUBLFOOD", "ZENSARTECH", "KOTAKBANK", "SAFARI", "TCIEXP",
    "INFY", "LUPIN", "COLPAL", "FORTIS", "ATUL",
    "SOBHA", "TORNTPOWER", "MAHABANK", "BANKBARODA", "CGPOWER",
    "LUXIND", "BAJFINANCE", "WIPRO", "TIMKEN", "ALKEM",
    "BERGEPAINT", "KEI", "HEROMOTOCO", "CHOLAFIN", "TATACOMM",
    "RALLIS", "KPITTECH", "NESTLEIND", "ABCAPITAL", "INDIGO",
    "CEATLTD", "POLYCAB", "GODREJPROP", "NTPC", "USHAMART",
    "CASTROLIND", "IDBI", "MOTHERSON", "IOC", "FEDERALBNK",
    "ICICIBANK", "CANBK", "ZENTEC", "SANOFI", "OBEROIRLTY",
    "BRITANNIA", "DELHIVERY", "PRAJIND", "PAGEIND", "EMAMILTD",
    "EICHERMOT", "PHOENIXLTD", "UNITDSPR", "KALYANKJIL", "COALINDIA",
    "UNIONBANK", "HINDUNILVR", "UCOBANK", "GODREJCP", "TATASTEEL",
    "SUPREMEIND", "LTTS", "RITES", "TATAINVEST", "THYROCARE",
    "ITC", "JSWSTEEL", "GNFC", "GODREJIND", "BANDHANBNK",
    "LICHSGFIN", "OFSS", "ADANIGREEN", "GRINDWELL", "DABUR",
    "SIEMENS", "DIVISLAB", "IDFCFIRSTB", "TECHM", "STAR",
    "KAJARIACER", "INDUSINDBK",
    # Tier 3 — marginal/flat (include for diversity, screener will filter)
    "RELIANCE", "TCS", "LT", "BAJAJFINSV", "ADANIENT",
    "TATACHEM", "TVSMOTOR", "ZYDUSLIFE", "NAUKRI", "IRCTC",
    "MPHASIS", "NMDC", "MUTHOOTFIN", "RECLTD",
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
        base_min_score = config.get("min_score_for_watchlist", 60)
        universe_size = config.get("universe_size", 50)

        regime = self.regime_detector.detect(nifty_daily, vix=vix)
        logger.info(f"Regime: {regime.regime.value} | Rec: {regime.recommendation} | VIX: {vix:.1f}")

        # Lower scoring threshold in bear conditions — stocks below EMAs with low RSI
        # can never reach the bullish-market threshold of 60
        regime_str = regime.regime.value if hasattr(regime.regime, "value") else str(regime.regime)
        if "strong_bear" in regime_str or regime_str == "bear":
            min_score = min(base_min_score, 40)
        elif "weak_bear" in regime_str:
            min_score = min(base_min_score, 48)
        elif "sideways" in regime_str:
            min_score = min(base_min_score, 54)
        else:
            min_score = base_min_score

        # Always build watchlist — mean_reversion strategy needs candidates even in bear/sideways
        # regime_bullish flag is passed per-strategy so each strategy decides for itself

        from_date = datetime.now(IST) - timedelta(days=90)
        to_date = datetime.now(IST)
        scores: List[StockScore] = []

        # Preferred symbols — always scored first, guaranteed consideration
        preferred = config.get("preferred_symbols", [])
        scanned = set()
        for sym in preferred:
            if sym in scanned:
                continue
            scanned.add(sym)
            try:
                df = data_registry.get_historical(sym, "1d", from_date, to_date)
                s = self.screener.score(sym, df, upcoming_event=(sym in event_set))
                scores.append(s)  # include regardless of tradeable flag — let score decide
            except Exception as e:
                logger.debug(f"Preferred {sym}: {e}")

        for sym in NIFTY_200[:universe_size]:
            if sym in scanned:
                continue
            scanned.add(sym)
            try:
                df = data_registry.get_historical(sym, "1d", from_date, to_date)
                s = self.screener.score(sym, df, upcoming_event=(sym in event_set))
                if s.tradeable:
                    scores.append(s)
            except Exception as e:
                logger.debug(f"{sym}: {e}")

        qualified = sorted([s for s in scores if s.total_score >= min_score],
                           key=lambda x: x.total_score, reverse=True)[:max_size]

        # Always log top candidates for diagnostics, even if none qualify
        all_scored = sorted([s for s in scores if s.total_score > 0],
                            key=lambda x: x.total_score, reverse=True)
        if all_scored:
            top = all_scored[:5]
            top_str = ", ".join(
                f"{s.symbol}:{s.total_score:.0f}(rsi{s.rsi_14:.0f})" for s in top
            )
            logger.info(f"Top scores [threshold={min_score}]: {top_str}")
        else:
            rejected = [s for s in scores if s.rejection_reason]
            reasons = {}
            for s in rejected:
                reasons[s.rejection_reason] = reasons.get(s.rejection_reason, 0) + 1
            logger.info(f"All {len(rejected)} stocks rejected before scoring. Reasons: {reasons}")

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
