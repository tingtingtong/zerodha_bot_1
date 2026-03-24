from dataclasses import dataclass
from typing import List
import logging

logger = logging.getLogger(__name__)


@dataclass
class CapitalTier:
    name: str
    min_capital: float
    max_capital: float
    risk_per_trade_pct: float
    daily_loss_pct: float
    weekly_dd_pct: float
    monthly_dd_pct: float
    deployment_pct: float
    max_per_trade_pct: float
    max_trades_per_day: int
    max_open_positions: int
    segments: List[str]
    description: str


CAPITAL_TIERS = [
    CapitalTier(
        name="Nano", min_capital=10_000, max_capital=49_999,
        risk_per_trade_pct=0.010, daily_loss_pct=0.025, weekly_dd_pct=0.050,
        monthly_dd_pct=0.080, deployment_pct=0.70, max_per_trade_pct=0.40,
        max_trades_per_day=3, max_open_positions=2,
        segments=["cash_equity_intraday", "etf"],
        description="Survival mode. ETFs preferred. A/B setups only.",
    ),
    CapitalTier(
        name="Micro", min_capital=50_000, max_capital=1_99_999,
        risk_per_trade_pct=0.010, daily_loss_pct=0.020, weekly_dd_pct=0.040,
        monthly_dd_pct=0.070, deployment_pct=0.75, max_per_trade_pct=0.35,
        max_trades_per_day=4, max_open_positions=3,
        segments=["cash_equity_intraday", "cash_equity_swing", "etf"],
        description="Swing trading viable. Larger universe.",
    ),
    CapitalTier(
        name="Small", min_capital=2_00_000, max_capital=4_99_999,
        risk_per_trade_pct=0.008, daily_loss_pct=0.018, weekly_dd_pct=0.035,
        monthly_dd_pct=0.060, deployment_pct=0.75, max_per_trade_pct=0.25,
        max_trades_per_day=5, max_open_positions=4,
        segments=["cash_equity_intraday", "cash_equity_swing", "etf", "btst", "options_buying"],
        description="Options buying viable. Multi-sector diversification.",
    ),
    CapitalTier(
        name="Medium", min_capital=5_00_000, max_capital=19_99_999,
        risk_per_trade_pct=0.007, daily_loss_pct=0.015, weekly_dd_pct=0.030,
        monthly_dd_pct=0.050, deployment_pct=0.80, max_per_trade_pct=0.20,
        max_trades_per_day=6, max_open_positions=6,
        segments=["cash_equity_intraday", "cash_equity_swing", "etf",
                  "btst", "options_buying", "options_selling_covered"],
        description="Multi-strategy. Covered calls viable.",
    ),
    CapitalTier(
        name="Large", min_capital=20_00_000, max_capital=float("inf"),
        risk_per_trade_pct=0.005, daily_loss_pct=0.012, weekly_dd_pct=0.025,
        monthly_dd_pct=0.040, deployment_pct=0.80, max_per_trade_pct=0.15,
        max_trades_per_day=10, max_open_positions=10,
        segments=["cash_equity_intraday", "cash_equity_swing", "etf",
                  "btst", "options_buying", "options_selling_full", "futures_hedging"],
        description="Full multi-strategy portfolio. Futures hedging.",
    ),
]


def get_tier(account_value: float, current_tier_name: str = None) -> CapitalTier:
    for tier in CAPITAL_TIERS:
        if tier.min_capital <= account_value <= tier.max_capital:
            # Hysteresis: if currently in this tier, stay unless clearly past boundary
            if current_tier_name and current_tier_name == tier.name:
                return tier
            # Add 2% buffer to prevent boundary oscillation
            if account_value >= tier.min_capital * 1.02:
                return tier
    if account_value < CAPITAL_TIERS[0].min_capital:
        logger.debug(f"Capital 20b9{account_value:,.0f} below Nano minimum. Using Nano tier.")
        return CAPITAL_TIERS[0]
    return CAPITAL_TIERS[-1]


def get_tier_summary(account_value: float) -> dict:
    tier = get_tier(account_value)
    return {
        "tier_name": tier.name,
        "account_value": account_value,
        "max_risk_per_trade_inr": round(account_value * tier.risk_per_trade_pct, 2),
        "max_daily_loss_inr": round(account_value * tier.daily_loss_pct, 2),
        "max_deployment_inr": round(account_value * tier.deployment_pct, 2),
        "max_trades_per_day": tier.max_trades_per_day,
        "max_open_positions": tier.max_open_positions,
        "segments": tier.segments,
        "description": tier.description,
    }
