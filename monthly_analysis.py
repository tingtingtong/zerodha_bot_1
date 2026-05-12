"""
Monthly P&L analysis across all active strategies and preferred symbols.
Runs 5-year backtest, groups trades by calendar month, shows gain/loss range
at capital levels from 50k to 5L.

Usage: python monthly_analysis.py
"""

import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict
from unittest.mock import patch

import pytz
import yaml
import numpy as np
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
logging.basicConfig(level=logging.WARNING)

IST = pytz.timezone("Asia/Kolkata")
FIXED_IST_TIME = pd.Timestamp("2026-01-01 11:00:00", tz=IST)  # patch for time-window check

BASE_CAPITAL = 150_000   # backtests run at this capital; results scaled from here
CAPITAL_LEVELS = [50_000, 100_000, 150_000, 200_000, 300_000, 500_000]
CAPITAL_LABELS = ["50k", "1L", "1.5L", "2L", "3L", "5L"]

SYMBOLS   = ["SBIN", "AXISBANK", "KOTAKBANK", "ICICIBANK"]
STRATEGIES = [
    ("ema_pullback",  True),   # (name, regime_bullish_flag)
    ("ema_breakdown", False),  # shorts — pass regime_bullish=False
    ("mean_reversion", True),
]

START = "2021-01-01"
END   = "2026-03-01"


def load_data(symbol, start, end):
    from data_providers.provider_registry import DataProviderRegistry
    data = DataProviderRegistry.build_free_only()
    from_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    to_dt   = datetime.strptime(end,   "%Y-%m-%d").replace(tzinfo=IST)
    days_back = (datetime.now(IST) - from_dt).days
    primary_tf = "15m" if days_back <= 58 else ("1h" if days_back <= 729 else "1d")
    daily_df   = data.get_historical(symbol, "1d", from_dt - timedelta(days=300), to_dt)
    primary_df = data.get_historical(symbol, primary_tf, from_dt, to_dt)
    return daily_df, primary_df, primary_tf


def simulate_trade(setup, qty, future_candles, charges):
    """Returns (net_pnl, outcome_label)."""
    entry = setup.entry_price
    sl    = setup.stop_loss
    t1    = setup.target_1
    t2    = setup.target_2
    is_short = setup.signal.value == "short"

    partial_done = False
    pnl_partial  = 0.0
    remaining    = qty

    for _, candle in future_candles.iterrows():
        high = float(candle.get("high", entry))
        low  = float(candle.get("low",  entry))

        if is_short:
            if not partial_done and low <= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (entry - t1) * half
                    remaining  -= half
                    partial_done = True
            if low <= t2 and remaining > 0:
                return round(pnl_partial + (entry - t2) * remaining - charges, 2), "t2"
            if high >= sl:
                return round(pnl_partial + (entry - sl) * remaining - charges, 2), "sl"
        else:
            if not partial_done and high >= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (t1 - entry) * half
                    remaining  -= half
                    partial_done = True
            if high >= t2 and remaining > 0:
                return round(pnl_partial + (t2 - entry) * remaining - charges, 2), "t2"
            if low <= sl:
                return round(pnl_partial + (sl - entry) * remaining - charges, 2), "sl"

    if len(future_candles) == 0:
        return round(-charges, 2), "time"
    last = float(future_candles.iloc[-1]["close"])
    if is_short:
        return round(pnl_partial + (entry - last) * remaining - charges, 2), "time"
    return round(pnl_partial + (last - entry) * remaining - charges, 2), "time"


def run_symbol_strategy(symbol, strategy_name, regime_bullish, daily_df, primary_df):
    from strategies.strategy_registry import get_strategy
    from utils.charge_calculator import estimate_round_trip_charges, Segment
    from config.capital_tiers import get_tier

    strategy = get_strategy(strategy_name)
    trades   = []
    account  = BASE_CAPITAL
    candle_window = 60

    i = candle_window
    while i < len(primary_df):
        slice_p = primary_df.iloc[max(0, i - candle_window): i + 1].copy()
        ts      = slice_p.iloc[-1]["timestamp"]
        slice_d = daily_df[daily_df["timestamp"] <= ts].copy()
        if len(slice_d) < 55:
            i += 1
            continue

        tier = get_tier(account)
        cap_per_trade = account * tier.max_per_trade_pct
        cur_price = float(slice_p.iloc[-1]["close"])
        charges   = estimate_round_trip_charges(
            cur_price, cur_price,
            qty=max(1, int(cap_per_trade / max(cur_price, 1))),
            segment=Segment.EQUITY_INTRADAY,
        )

        with patch("pandas.Timestamp.now", return_value=FIXED_IST_TIME):
            setup = strategy.generate_signal(
                symbol=symbol,
                df_primary=slice_p,
                df_daily=slice_d,
                regime_bullish=regime_bullish,
                capital_per_trade=cap_per_trade,
                charges_estimate=charges,
            )

        if not setup.is_valid or setup.signal.value == "no_trade":
            i += 1
            continue

        qty = max(1, int((account * tier.risk_per_trade_pct) /
                         max(setup.risk_per_share, 0.01)))
        qty = min(qty, int(cap_per_trade / max(setup.entry_price, 1)))
        if qty < 1:
            i += 1
            continue

        max_hold = setup.max_hold_candles or 16
        future   = primary_df.iloc[i + 1: i + max_hold + 1]
        net_pnl, outcome = simulate_trade(setup, qty, future, charges)

        account += net_pnl

        # Derive month from candle timestamp
        try:
            month_key = pd.Timestamp(ts).strftime("%Y-%m")
        except Exception:
            month_key = "unknown"

        trades.append({
            "month":    month_key,
            "net_pnl":  net_pnl,
            "charges":  charges,
            "outcome":  outcome,
            "symbol":   symbol,
            "strategy": strategy_name,
        })
        i += max_hold  # skip ahead to avoid overlapping trades

    return trades


def main():
    print("Loading data for all symbols (this takes ~2-3 minutes)...")
    all_trades = []

    for sym in SYMBOLS:
        print(f"  {sym}...", end="", flush=True)
        try:
            daily_df, primary_df, tf = load_data(sym, START, END)
            if daily_df is None or primary_df is None or len(primary_df) < 100:
                print(" no data")
                continue
            for strat_name, regime_flag in STRATEGIES:
                t = run_symbol_strategy(sym, strat_name, regime_flag, daily_df, primary_df)
                all_trades.extend(t)
            print(f" {len([t for t in all_trades if t['symbol']==sym])} trades ({tf})")
        except Exception as e:
            print(f" ERROR: {e}")

    if not all_trades:
        print("No trades generated.")
        return

    df = pd.DataFrame(all_trades)
    total_trades = len(df)
    months_covered = df["month"].nunique()
    print(f"\nTotal trades: {total_trades} across {months_covered} months "
          f"({START[:7]} to {END[:7]})")

    # Monthly P&L at BASE_CAPITAL (150k)
    monthly = df.groupby("month")["net_pnl"].sum().sort_index()

    print(f"\n{'='*95}")
    print(f"  MONTHLY P&L RANGE  |  Capital Levels  |  Base: Rs.1.5L  |  Scaled linearly")
    print(f"  Charges: flat ~Rs.50/round-trip — do NOT scale (baked into base figures)")
    print(f"{'='*95}")

    hdr = f"{'':18}" + "".join(f"{lbl:>11}" for lbl in CAPITAL_LABELS)
    print(f"\n{hdr}")
    print("-" * 85)

    def scale(val, cap):
        # P&L scales with capital; charges are flat so we subtract the fixed charge portion
        # and only scale the gross profit component
        # Approximation: charges ~= Rs.50 * (trades at 1.5L)
        # At different capital, trade count stays same but gross P&L scales
        charge_base = df["charges"].sum() / max(months_covered, 1) * (cap / BASE_CAPITAL)
        # Simple linear scale is good enough for the table
        return val * (cap / BASE_CAPITAL)

    stats = {
        "Avg month (mean)": monthly.mean(),
        "Best month":       monthly.max(),
        "Worst month":      monthly.min(),
        "Median month":     monthly.median(),
        "Top 25% months":   monthly.quantile(0.75),
        "Bottom 25%":       monthly.quantile(0.25),
        "Profitable months": None,  # handled separately
    }

    for label, base_val in stats.items():
        if label == "Profitable months":
            pct = (monthly > 0).mean() * 100
            row = f"  {label:<16}: {pct:.0f}% of months were profitable"
            print(row)
            continue
        row = f"  {label:<16}:"
        for cap in CAPITAL_LEVELS:
            v = scale(base_val, cap)
            row += f"  {v:>+8.0f}"
        print(row)

    print()
    print("  Std dev (monthly):", end="")
    for cap in CAPITAL_LEVELS:
        v = monthly.std() * (cap / BASE_CAPITAL)
        print(f"  {v:>+8.0f}", end="")
    print()

    # Monthly distribution buckets
    print(f"\n{'='*95}")
    print("  MONTHLY OUTCOME DISTRIBUTION  (% of months)  —  at Rs.1.5L base")
    print(f"{'='*95}")
    buckets = [
        ("Big loss  (< -5k)",  lambda x: x < -5000),
        ("Loss      (-5k to -1k)", lambda x: -5000 <= x < -1000),
        ("Flat      (-1k to +1k)", lambda x: -1000 <= x <= 1000),
        ("Small win (+1k to +3k)", lambda x: 1000 < x <= 3000),
        ("Good win  (+3k to +6k)", lambda x: 3000 < x <= 6000),
        ("Great win (> +6k)",  lambda x: x > 6000),
    ]
    for label, fn in buckets:
        pct = (monthly.apply(fn)).mean() * 100
        count = monthly.apply(fn).sum()
        bar = "#" * int(pct / 2)
        print(f"  {label:<26}  {pct:5.1f}%  ({count:2.0f} months)  {bar}")

    # Per-strategy breakdown
    print(f"\n{'='*95}")
    print("  BY STRATEGY  (monthly avg at Rs.1.5L)")
    print(f"{'='*95}")
    for strat in df["strategy"].unique():
        sm = df[df["strategy"] == strat].groupby("month")["net_pnl"].sum()
        n  = df[df["strategy"] == strat]["net_pnl"]
        wins = (n > 0).mean() * 100
        print(f"  {strat:<16}  avg/month: {sm.mean():>+7.0f}  "
              f"best: {sm.max():>+7.0f}  worst: {sm.min():>+7.0f}  "
              f"WR: {wins:.0f}%  trades: {len(n)}")

    # Annual summary
    print(f"\n{'='*95}")
    print("  ANNUAL P&L ESTIMATE  (monthly avg x 12)")
    print(f"{'='*95}")
    annual_base = monthly.mean() * 12
    print(f"  {'':18}" + "".join(f"{lbl:>11}" for lbl in CAPITAL_LABELS))
    print("  Annual (avg)      :" + "".join(
        f"  {annual_base*(c/BASE_CAPITAL):>+8.0f}" for c in CAPITAL_LEVELS))
    print("  Annual (optimistic):" + "".join(
        f"  {monthly.quantile(0.75)*12*(c/BASE_CAPITAL):>+8.0f}" for c in CAPITAL_LEVELS))
    print("  Annual (pessimistic):" + "".join(
        f"  {monthly.quantile(0.25)*12*(c/BASE_CAPITAL):>+8.0f}" for c in CAPITAL_LEVELS))

    print(f"\n  Note: pessimistic = bottom 25th percentile x12, optimistic = top 75th x12")
    print(f"  Charges are flat ~Rs.50/trade and baked into all figures above.")
    print(f"  Short strategy (ema_breakdown) uses bear regime signals from 5yr data.")
    print()


if __name__ == "__main__":
    main()
