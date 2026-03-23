import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional
import pytz

from config.capital_tiers import get_tier
from .position_sizer import PositionSizer

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class RiskDecision(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    KILL_SWITCH = "kill_switch"


@dataclass
class RiskCheckResult:
    decision: RiskDecision
    reason: str
    adjusted_qty: int = 0
    adjusted_capital: float = 0.0
    size_multiplier: float = 1.0


class RiskEngine:

    def __init__(self, account_value: float, config: dict = None):
        self.config = config or {}
        self.sizer = PositionSizer(account_value)

        self.daily_pnl: float = 0.0
        self.weekly_pnl: float = 0.0
        self.monthly_pnl: float = 0.0
        self.trade_count_today: int = 0
        self.consecutive_losses: int = 0
        self.kill_switch_active: bool = False
        self.last_loss_time: Optional[datetime] = None
        self.open_positions_count: int = 0

        self.min_rr = self.config.get("min_rr_ratio", 1.8)
        self.cooldown_min = self.config.get("cooldown_minutes_after_loss", 30)
        self.consec_halt = self.config.get("consecutive_loss_halt", 3)
        self.min_trade_value = self.config.get("min_trade_value", 2000)
        self.size_reduction = self.config.get("size_reduction_after_losses", 0.50)
        self.quality_min = self.config.get("setup_quality_min", "B")

    @property
    def account_value(self) -> float:
        return self.sizer.account_value

    def check_trade(
        self, symbol: str, entry_price: float, stop_loss: float,
        proposed_qty: int, open_positions_value: float,
        setup_quality: str, charges_estimate: float,
        avg_daily_volume: int = 0,
    ) -> RiskCheckResult:

        if self.kill_switch_active:
            return RiskCheckResult(RiskDecision.KILL_SWITCH, "kill_switch_active")

        if self.daily_pnl < 0 and abs(self.daily_pnl) >= self.sizer.max_daily_loss():
            self.kill_switch_active = True
            logger.critical(f"DAILY LOSS LIMIT: ₹{self.daily_pnl:.2f}")
            return RiskCheckResult(RiskDecision.KILL_SWITCH, "daily_loss_limit")

        if self.weekly_pnl < 0 and abs(self.weekly_pnl) >= self.sizer.max_weekly_drawdown():
            return RiskCheckResult(RiskDecision.REJECTED, "weekly_drawdown_limit")

        tier = get_tier(self.account_value)
        if self.trade_count_today >= tier.max_trades_per_day:
            return RiskCheckResult(RiskDecision.REJECTED, f"max_trades_{tier.max_trades_per_day}")

        if self.open_positions_count >= tier.max_open_positions:
            return RiskCheckResult(RiskDecision.REJECTED, f"max_positions_{tier.max_open_positions}")

        if self.consecutive_losses >= self.consec_halt:
            return RiskCheckResult(RiskDecision.REJECTED,
                                   f"{self.consec_halt}_consec_losses_halt")

        size_mult = 1.0
        if self.consecutive_losses >= 2:
            size_mult = self.size_reduction
            logger.warning(f"Consecutive losses {self.consecutive_losses} → size at {size_mult*100:.0f}%")

        if self.last_loss_time is not None:
            elapsed = (datetime.now(IST) - self.last_loss_time).total_seconds() / 60
            if elapsed < self.cooldown_min:
                return RiskCheckResult(RiskDecision.REJECTED,
                                       f"cooldown_{self.cooldown_min - elapsed:.0f}min_remaining")

        quality_order = {"A": 3, "B": 2, "C": 1}
        if quality_order.get(setup_quality, 0) < quality_order.get(self.quality_min, 2):
            return RiskCheckResult(RiskDecision.REJECTED,
                                   f"quality_{setup_quality}_below_{self.quality_min}")

        rps = entry_price - stop_loss
        if rps <= 0:
            return RiskCheckResult(RiskDecision.REJECTED, "sl_above_entry")

        qty = self.sizer.calculate_qty(entry_price, stop_loss, size_mult)
        if avg_daily_volume > 0:
            qty = self.sizer.liquidity_guard(qty, avg_daily_volume)
        if qty < 1:
            return RiskCheckResult(RiskDecision.REJECTED,
                                   f"qty_0_rps_{rps:.2f}_budget_{self.sizer.max_risk_per_trade()*size_mult:.0f}")

        trade_val = qty * entry_price
        if (open_positions_value + trade_val) > self.sizer.max_deployable():
            available = self.sizer.max_deployable() - open_positions_value
            qty_cap = int(available / entry_price)
            if qty_cap < 1:
                return RiskCheckResult(RiskDecision.REJECTED, "deployment_cap_reached")
            qty = min(qty, qty_cap)

        if qty * entry_price < self.min_trade_value:
            return RiskCheckResult(RiskDecision.REJECTED,
                                   f"trade_too_small_{qty*entry_price:.0f}")

        net_reward = qty * (rps * 2.5) - charges_estimate
        net_rr = net_reward / max(qty * rps, 0.01)
        if net_rr < self.min_rr:
            return RiskCheckResult(RiskDecision.REJECTED,
                                   f"rr_{net_rr:.2f}_below_{self.min_rr}")

        return RiskCheckResult(
            decision=RiskDecision.APPROVED, reason="all_checks_passed",
            adjusted_qty=qty, adjusted_capital=round(qty * entry_price, 2),
            size_multiplier=size_mult,
        )

    def record_result(self, pnl: float, symbol: str = ""):
        old = self.sizer.account_value
        self.daily_pnl += pnl
        self.weekly_pnl += pnl
        self.monthly_pnl += pnl
        self.trade_count_today += 1
        self.sizer.update(old + pnl)

        if pnl < 0:
            self.consecutive_losses += 1
            self.last_loss_time = datetime.now(IST)
            logger.warning(f"LOSS  {symbol}: ₹{pnl:.2f} | consec={self.consecutive_losses} | day=₹{self.daily_pnl:.2f}")
        else:
            self.consecutive_losses = 0
            logger.info(f"WIN   {symbol}: ₹{pnl:.2f} | day=₹{self.daily_pnl:.2f}")

        if self.daily_pnl < 0 and abs(self.daily_pnl) >= self.sizer.max_daily_loss():
            self.kill_switch_active = True
            logger.critical("KILL SWITCH: daily loss limit breached")

    def check_market_kill_switch(self, nifty_chg_pct: float, vix: float,
                                  api_errors: int, data_stale_min: int) -> tuple:
        if vix >= 30:
            return True, f"extreme_vix_{vix:.1f}"
        if nifty_chg_pct < -2.5:
            return True, f"nifty_circuit_{nifty_chg_pct:.1f}pct"
        if api_errors >= 3:
            return True, f"api_errors_{api_errors}"
        if data_stale_min > 5:
            return True, f"data_stale_{data_stale_min}min"
        return False, "ok"

    def reset_daily(self):
        self.daily_pnl = 0.0
        self.trade_count_today = 0
        self.consecutive_losses = 0
        self.kill_switch_active = False
        self.last_loss_time = None
        tier = get_tier(self.account_value)
        logger.info(f"Risk reset. Account:₹{self.account_value:,.0f} | Tier:{tier.name} | "
                    f"MaxRisk:₹{self.sizer.max_risk_per_trade():.0f}/trade | "
                    f"DailyLoss:₹{self.sizer.max_daily_loss():.0f}")

    def reset_weekly(self):
        self.weekly_pnl = 0.0

    def reset_monthly(self):
        self.monthly_pnl = 0.0

    def summary(self) -> dict:
        tier = get_tier(self.account_value)
        return {
            "account_value": round(self.account_value, 2),
            "tier": tier.name,
            "daily_pnl": round(self.daily_pnl, 2),
            "weekly_pnl": round(self.weekly_pnl, 2),
            "trade_count_today": self.trade_count_today,
            "consecutive_losses": self.consecutive_losses,
            "kill_switch": self.kill_switch_active,
            "max_risk_per_trade": round(self.sizer.max_risk_per_trade(), 2),
            "max_daily_loss": round(self.sizer.max_daily_loss(), 2),
            "max_deployable": round(self.sizer.max_deployable(), 2),
            "open_positions": self.open_positions_count,
        }
