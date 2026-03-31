"""
Backtest runner — runs strategy against historical data.

Usage:
  python backtest_runner.py
  python backtest_runner.py --symbol RELIANCE --start 2022-01-01 --end 2024-01-01
  python backtest_runner.py --strategy etf_momentum --symbol NIFTYBEES
"""

import argparse
import sys
import logging
from pathlib import Path
from datetime import datetime, timedelta

import pytz
import yaml

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("BacktestRunner")


def run_backtest(
    symbol: str,
    strategy_name: str,
    start: str,
    end: str,
    initial_capital: float,
    config: dict,
):
    from data_providers.provider_registry import DataProviderRegistry
    from strategies.strategy_registry import get_strategy
    from backtesting.performance_metrics import PerformanceCalculator
    from utils.charge_calculator import estimate_round_trip_charges, Segment
    from config.capital_tiers import get_tier

    data = DataProviderRegistry.build_free_only()
    strategy = get_strategy(strategy_name)

    from_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    to_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST)

    # yfinance intraday limits are measured from TODAY:
    #   15m: start must be within last 60 days
    #   1h:  start must be within last 730 days
    #   1d:  no limit
    start_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
    end_dt   = datetime.strptime(end,   "%Y-%m-%d").replace(tzinfo=IST)
    now_dt   = datetime.now(IST)
    days_from_today = (now_dt - start_dt).days
    if days_from_today <= 58:
        primary_interval = "15m"
    elif days_from_today <= 729:
        primary_interval = "1h"
    else:
        primary_interval = "1d"

    logger.info(f"Fetching historical data: {symbol} {start} to {end} (primary: {primary_interval})")
    daily_df = data.get_historical(symbol, "1d", from_dt - timedelta(days=300), to_dt)
    intraday_df = data.get_historical(symbol, primary_interval, from_dt, to_dt)

    if daily_df is None or len(daily_df) < 60:
        logger.error("Insufficient daily data for backtest")
        return

    if intraday_df is None or len(intraday_df) < 30:
        logger.error(f"Insufficient {primary_interval} data for backtest")
        return

    logger.info(f"Daily candles: {len(daily_df)} | Primary ({primary_interval}) candles: {len(intraday_df)}")

    account = initial_capital
    trades = []
    candle_window = 60  # lookback for EMA convergence (60+ candles needed)

    for i in range(candle_window, len(intraday_df)):
        slice_15m = intraday_df.iloc[max(0, i - candle_window): i + 1].copy()
        slice_daily = daily_df[daily_df["timestamp"] <= slice_15m.iloc[-1]["timestamp"]].copy()

        if len(slice_daily) < 55:
            continue

        tier = get_tier(account)
        capital_per_trade = account * tier.max_per_trade_pct
        cur_price = float(slice_15m.iloc[-1]["close"])
        charges = estimate_round_trip_charges(cur_price, cur_price,
                                               qty=max(1, int(capital_per_trade / max(cur_price, 1))),
                                               segment=Segment.EQUITY_INTRADAY)

        setup = strategy.generate_signal(
            symbol=symbol,
            df_primary=slice_15m,
            df_daily=slice_daily,
            regime_bullish=True,  # backtest passes True; strategies gate themselves via regime_bullish
            capital_per_trade=capital_per_trade,
            charges_estimate=charges,
        )

        if not setup.is_valid or setup.signal.value == "no_trade":
            continue

        # Simulate trade outcome
        qty = max(1, int((account * tier.risk_per_trade_pct) / max(setup.entry_price - setup.stop_loss, 0.01)))
        qty = min(qty, int(capital_per_trade / setup.entry_price))
        if qty < 1:
            continue

        # Look ahead for outcome (next 16 candles)
        outcome_candles = intraday_df.iloc[i + 1: i + 17]
        net_pnl = simulate_outcome(setup, qty, outcome_candles, charges, is_short=(setup.signal.value == "short"))

        account += net_pnl
        trades.append({
            "symbol": symbol,
            "strategy": strategy_name,
            "quality": setup.setup_quality,
            "entry_price": setup.entry_price,
            "stop_loss": setup.stop_loss,
            "target_1": setup.target_1,
            "qty": qty,
            "net_pnl": round(net_pnl, 2),
            "charges": round(charges, 2),
            "candle_idx": i,
        })

        # Advance past hold window to avoid overlapping trades
        i += 16

    if not trades:
        logger.warning("No trades generated in backtest period")
        return

    calc = PerformanceCalculator()
    report = calc.calculate(trades, initial_capital)
    if report:
        print(f"\n{'='*60}")
        print(f"BACKTEST RESULTS: {symbol} | {strategy_name}")
        print(f"{start} to {end} | Capital: Rs.{initial_capital:,.0f}")
        print(f"{'='*60}")
        print(f"Total Trades:    {report.total_trades}")
        print(f"Win Rate:        {report.win_rate:.1%}")
        print(f"Profit Factor:   {report.profit_factor:.2f}")
        print(f"Expectancy:      Rs.{report.expectancy:.2f}/trade")
        print(f"Net P&L:         Rs.{report.net_pnl:,.2f}")
        print(f"Charges Paid:    Rs.{report.total_charges:,.2f}  ({report.charge_drag_pct:.1f}% drag)")
        print(f"Max Drawdown:    {report.max_drawdown_pct:.1f}%  (Rs.{report.max_drawdown:,.0f})")
        print(f"Max Consec Loss: {report.max_consecutive_losses}")
        print(f"Sharpe Ratio:    {report.sharpe_ratio:.2f}")
        print(f"{'='*60}")
        print(f"RESULT: {'PASS' if report.passed else 'FAIL'}")
        if not report.passed:
            print(f"Failures: {', '.join(report.failure_reasons)}")
        print(f"{'='*60}\n")

        # Save results
        import json
        out_dir = Path(config.get("backtesting", {}).get("results_dir", "backtesting/results"))
        out_dir.mkdir(parents=True, exist_ok=True)
        out_file = out_dir / f"backtest_{symbol}_{strategy_name}_{start}_{end}.json"
        with open(out_file, "w") as f:
            import dataclasses
            json.dump(dataclasses.asdict(report), f, indent=2)
        logger.info(f"Results saved: {out_file}")


def simulate_outcome(setup, qty: int, future_candles, charges: float,
                     is_short: bool = False) -> float:
    """Look-ahead simulation — checks if SL or target hit in next candles."""
    entry = setup.entry_price
    sl = setup.stop_loss
    t1 = setup.target_1
    t2 = setup.target_2

    partial_exit_done = False
    pnl_partial = 0.0
    remaining = qty

    for _, candle in future_candles.iterrows():
        high = float(candle.get("high", entry))
        low = float(candle.get("low", entry))

        if is_short:
            # Short: profit when price falls; T1/T2 hit when LOW reaches target; SL hit when HIGH crosses SL
            if not partial_exit_done and low <= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (entry - t1) * half
                    remaining -= half
                    partial_exit_done = True

            if low <= t2 and remaining > 0:
                pnl_t2 = (entry - t2) * remaining
                return round(pnl_partial + pnl_t2 - charges, 2)

            if high >= sl:
                sl_pnl = (entry - sl) * remaining  # negative (loss)
                return round(pnl_partial + sl_pnl - charges, 2)
        else:
            # Long: profit when price rises
            if not partial_exit_done and high >= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (t1 - entry) * half
                    remaining -= half
                    partial_exit_done = True

            if high >= t2 and remaining > 0:
                pnl_t2 = (t2 - entry) * remaining
                return round(pnl_partial + pnl_t2 - charges, 2)

            if low <= sl:
                sl_pnl = (sl - entry) * remaining
                return round(pnl_partial + sl_pnl - charges, 2)

    # Time exit at last candle close
    if len(future_candles) == 0:
        return round(-charges, 2)
    last_close = float(future_candles.iloc[-1]["close"])
    if is_short:
        time_pnl = (entry - last_close) * remaining
    else:
        time_pnl = (last_close - entry) * remaining
    return round(pnl_partial + time_pnl - charges, 2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="NIFTYBEES")
    parser.add_argument("--strategy", default="etf_momentum")
    parser.add_argument("--start", default="2022-06-01")
    parser.add_argument("--end", default="2024-01-01")
    parser.add_argument("--capital", type=float, default=20000)
    parser.add_argument("--config", default="config/config.yaml")
    args = parser.parse_args()

    with open(args.config) as f:
        cfg = yaml.safe_load(f)

    run_backtest(
        symbol=args.symbol,
        strategy_name=args.strategy,
        start=args.start,
        end=args.end,
        initial_capital=args.capital,
        config=cfg,
    )
