from typing import Dict, List
from .base_strategy import BaseStrategy
from .ema_pullback import EMAPullbackStrategy
from .ema_breakdown import EMABreakdownStrategy
from .etf_momentum import ETFMomentumStrategy
from .mean_reversion import MeanReversionStrategy
from .orb_strategy import ORBStrategy
from .vwap_strategy import VWAPStrategy

_REGISTRY: Dict[str, BaseStrategy] = {
    "ema_pullback": EMAPullbackStrategy(),
    "ema_breakdown": EMABreakdownStrategy(),
    "etf_momentum": ETFMomentumStrategy(),
    "mean_reversion": MeanReversionStrategy(),
    "orb": ORBStrategy(),
    "vwap": VWAPStrategy(),
}


def get_strategy(name: str) -> BaseStrategy:
    if name not in _REGISTRY:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(_REGISTRY.keys())}")
    return _REGISTRY[name]


def get_active_strategies(names: List[str]) -> List[BaseStrategy]:
    return [get_strategy(n) for n in names]


def list_strategies() -> List[str]:
    return list(_REGISTRY.keys())
