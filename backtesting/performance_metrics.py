import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class PerformanceReport:
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    gross_profit: float
    gross_loss: float
    net_pnl: float
    total_charges: float
    profit_factor: float
    expectancy: float
    avg_win: float
    avg_loss: float
    largest_win: float
    largest_loss: float
    max_drawdown: float
    max_drawdown_pct: float
    max_consecutive_losses: int
    max_consecutive_wins: int
    avg_trade_duration_min: float
    sharpe_ratio: float
    pnl_without_charges: float
    charge_drag_pct: float
    passed: bool
    failure_reasons: List[str]

    def summary(self) -> str:
        verdict = "PASS" if self.passed else f"FAIL ({', '.join(self.failure_reasons)})"
        return (
            f"Trades:{self.total_trades} WR:{self.win_rate:.1%} "
            f"PF:{self.profit_factor:.2f} Expect:₹{self.expectancy:.0f} "
            f"MaxDD:{self.max_drawdown_pct:.1f}% Sharpe:{self.sharpe_ratio:.2f} | {verdict}"
        )


class PerformanceCalculator:

    MIN_TRADES = 30
    MIN_WIN_RATE = 0.42
    MIN_PF = 1.25
    MIN_EXPECTANCY = 30
    MAX_DD_PCT = 18.0
    MAX_CONSEC_LOSSES = 7
    MIN_SHARPE = 0.4

    def calculate(self, trades: List[dict], initial_capital: float) -> Optional[PerformanceReport]:
        if not trades:
            return None
        df = pd.DataFrame(trades)
        if "net_pnl" not in df.columns:
            return None

        df["net_pnl"] = pd.to_numeric(df["net_pnl"], errors="coerce").fillna(0)
        df["charges"] = pd.to_numeric(
            df.get("charges", pd.Series([0] * len(df))), errors="coerce"
        ).fillna(0)

        wins = df[df["net_pnl"] > 0]
        losses = df[df["net_pnl"] <= 0]
        gp = float(wins["net_pnl"].sum())
        gl = float(abs(losses["net_pnl"].sum()))
        net = float(df["net_pnl"].sum())
        charges = float(df["charges"].sum())
        pf = gp / max(gl, 0.01)
        expectancy = net / max(len(df), 1)

        # Drawdown
        cum = df["net_pnl"].cumsum() + initial_capital
        roll_max = cum.cummax()
        dd = roll_max - cum
        max_dd = float(dd.max())
        dd_idx = dd.idxmax() if len(dd) > 0 else 0
        peak_at_dd = float(roll_max.iloc[dd_idx]) if len(roll_max) > 0 else initial_capital
        max_dd_pct = (max_dd / max(peak_at_dd, 1)) * 100

        # Consecutive
        max_cl = max_cw = cl = cw = 0
        for p in df["net_pnl"]:
            if p < 0:
                cl += 1; cw = 0; max_cl = max(max_cl, cl)
            else:
                cw += 1; cl = 0; max_cw = max(max_cw, cw)

        # Sharpe (trade-level approximation)
        sharpe = 0.0
        if len(df) > 2:
            std = df["net_pnl"].std()
            sharpe = float((df["net_pnl"].mean() / max(std, 0.01)) * np.sqrt(252))

        dur = float(df["duration_min"].mean()) if "duration_min" in df.columns else 0.0
        win_rate = len(wins) / max(len(df), 1)

        fails = []
        if len(df) < self.MIN_TRADES:
            fails.append(f"trades_{len(df)}<{self.MIN_TRADES}")
        if win_rate < self.MIN_WIN_RATE:
            fails.append(f"wr_{win_rate:.2f}<{self.MIN_WIN_RATE}")
        if pf < self.MIN_PF:
            fails.append(f"pf_{pf:.2f}<{self.MIN_PF}")
        if expectancy < self.MIN_EXPECTANCY:
            fails.append(f"expect_{expectancy:.0f}<{self.MIN_EXPECTANCY}")
        if max_dd_pct > self.MAX_DD_PCT:
            fails.append(f"dd_{max_dd_pct:.1f}%>{self.MAX_DD_PCT}%")
        if max_cl > self.MAX_CONSEC_LOSSES:
            fails.append(f"consec_loss_{max_cl}>{self.MAX_CONSEC_LOSSES}")

        return PerformanceReport(
            total_trades=len(df),
            winning_trades=len(wins),
            losing_trades=len(losses),
            win_rate=round(win_rate, 4),
            gross_profit=round(gp, 2),
            gross_loss=round(gl, 2),
            net_pnl=round(net, 2),
            total_charges=round(charges, 2),
            profit_factor=round(pf, 3),
            expectancy=round(expectancy, 2),
            avg_win=round(float(wins["net_pnl"].mean()) if len(wins) else 0, 2),
            avg_loss=round(float(losses["net_pnl"].mean()) if len(losses) else 0, 2),
            largest_win=round(float(wins["net_pnl"].max()) if len(wins) else 0, 2),
            largest_loss=round(float(losses["net_pnl"].min()) if len(losses) else 0, 2),
            max_drawdown=round(max_dd, 2),
            max_drawdown_pct=round(max_dd_pct, 2),
            max_consecutive_losses=max_cl,
            max_consecutive_wins=max_cw,
            avg_trade_duration_min=round(dur, 1),
            sharpe_ratio=round(sharpe, 3),
            pnl_without_charges=round(net + charges, 2),
            charge_drag_pct=round((charges / max(gp, 0.01)) * 100, 1),
            passed=len(fails) == 0,
            failure_reasons=fails,
        )
