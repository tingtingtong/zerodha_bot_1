import math
import logging
from config.capital_tiers import get_tier

logger = logging.getLogger(__name__)


class PositionSizer:

    def __init__(self, account_value: float):
        self.account_value = account_value

    def update(self, new_value: float):
        self.account_value = new_value

    @property
    def tier(self):
        return get_tier(self.account_value)

    def max_risk_per_trade(self) -> float:
        return self.account_value * self.tier.risk_per_trade_pct

    def max_daily_loss(self) -> float:
        return self.account_value * self.tier.daily_loss_pct

    def max_weekly_drawdown(self) -> float:
        return self.account_value * self.tier.weekly_dd_pct

    def max_monthly_drawdown(self) -> float:
        return self.account_value * self.tier.monthly_dd_pct

    def max_deployable(self) -> float:
        return self.account_value * self.tier.deployment_pct

    def max_per_trade(self) -> float:
        return self.account_value * self.tier.max_per_trade_pct

    def calculate_qty(self, entry_price: float, stop_loss: float,
                      size_multiplier: float = 1.0) -> int:
        rps = entry_price - stop_loss
        if rps <= 0 or entry_price <= 0:
            return 0
        allowed_risk = self.max_risk_per_trade() * size_multiplier
        by_risk = math.floor(allowed_risk / rps)
        by_capital = math.floor(self.max_per_trade() / entry_price)
        qty = min(by_risk, by_capital)
        if qty < 1:
            logger.debug(f"qty=0: rps={rps:.2f} budget={allowed_risk:.0f} "
                         f"by_risk={by_risk} by_capital={by_capital}")
        return max(0, qty)

    def liquidity_guard(self, qty: int, avg_daily_volume: int,
                        max_participation: float = 0.01) -> int:
        if avg_daily_volume <= 0:
            return qty
        max_qty = math.floor(avg_daily_volume * max_participation)
        if qty > max_qty:
            logger.warning(f"Qty {qty} > liquidity guard {max_qty} — capping")
            return max_qty
        return qty
