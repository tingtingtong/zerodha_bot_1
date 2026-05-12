"""
Batch backtest — runs all strategies against all NIFTY_200 stocks.
Auto-selects interval: 15m (≤58 days), 1h (≤729 days), 1d (older).

Usage:
    python batch_backtest.py
    python batch_backtest.py --days 90
    python batch_backtest.py --start 2026-01-23 --end 2026-04-23
    python batch_backtest.py --strategies ema_pullback ema_breakdown
    python batch_backtest.py --workers 4
"""
import argparse
import json
import logging
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pytz
import pandas as pd

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "backtesting/results/batch_backtest.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("BatchBacktest")

IST = pytz.timezone("Asia/Kolkata")
ALL_STRATEGIES = ["ema_pullback", "ema_breakdown", "mean_reversion", "orb"]


def _pick_interval(start: str) -> str:
    """Auto-select yfinance interval based on how far back start date is."""
    days_ago = (datetime.now(IST) - datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)).days
    if days_ago <= 58:
        return "15m"
    elif days_ago <= 729:
        return "1h"
    return "1d"


def backtest_one(symbol: str, strategy_name: str, start: str, end: str,
                 capital: float = 200000) -> dict:
    """Run a single backtest. Returns result dict."""
    from data_providers.provider_registry import DataProviderRegistry
    from strategies.strategy_registry import get_strategy
    from backtesting.performance_metrics import PerformanceCalculator
    from utils.charge_calculator import estimate_round_trip_charges, Segment
    from config.capital_tiers import get_tier

    interval = _pick_interval(start)
    result = {
        "symbol": symbol, "strategy": strategy_name,
        "start": start, "end": end, "interval": interval,
        "trades": 0, "win_rate": 0.0, "profit_factor": 0.0,
        "expectancy": 0.0, "net_pnl": 0.0, "sharpe": 0.0,
        "max_drawdown_pct": 0.0, "passed": False, "error": None,
    }

    try:
        data = DataProviderRegistry.build_free_only()
        strategy = get_strategy(strategy_name)

        from_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
        to_dt = datetime.strptime(end, "%Y-%m-%d").replace(tzinfo=IST)

        daily_df = data.get_historical(symbol, "1d", from_dt - timedelta(days=300), to_dt)
        intraday_df = data.get_historical(symbol, interval, from_dt, to_dt)

        if daily_df is None or len(daily_df) < 30:
            result["error"] = "insufficient_daily_data"
            return result
        if intraday_df is None or len(intraday_df) < 60:
            result["error"] = f"insufficient_{interval}_data_{len(intraday_df) if intraday_df is not None else 0}"
            return result

        account = capital
        trades = []
        candle_window = 60

        i = candle_window
        while i < len(intraday_df):
            slice_15m = intraday_df.iloc[max(0, i - candle_window): i + 1].copy()
            ts_col = "timestamp" if "timestamp" in slice_15m.columns else None
            if ts_col:
                last_ts = slice_15m.iloc[-1][ts_col]
                slice_daily = daily_df[daily_df["timestamp"] <= last_ts].copy()
            else:
                slice_daily = daily_df.copy()

            if len(slice_daily) < 30:
                i += 1
                continue

            tier = get_tier(account)
            capital_per_trade = account * tier.max_per_trade_pct
            cur_price = float(slice_15m.iloc[-1]["close"])
            charges = estimate_round_trip_charges(
                cur_price, cur_price,
                qty=max(1, int(capital_per_trade / max(cur_price, 1))),
                segment=Segment.EQUITY_INTRADAY,
            )

            setup = strategy.generate_signal(
                symbol=symbol,
                df_primary=slice_15m,
                df_daily=slice_daily,
                regime_bullish=True,
                capital_per_trade=capital_per_trade,
                charges_estimate=charges,
            )

            if not setup.is_valid:
                i += 1
                continue

            qty = max(1, int((account * tier.risk_per_trade_pct) / max(
                abs(setup.entry_price - setup.stop_loss), 0.01)))
            qty = min(qty, int(capital_per_trade / max(setup.entry_price, 1)))
            if qty < 1:
                i += 16
                continue

            future = intraday_df.iloc[i + 1: i + 17]
            is_short = setup.signal.value == "short"
            net_pnl = _simulate(setup, qty, future, charges, is_short)

            account += net_pnl
            trades.append({
                "symbol": symbol, "strategy": strategy_name,
                "quality": setup.setup_quality,
                "entry_price": setup.entry_price,
                "stop_loss": setup.stop_loss,
                "target_1": setup.target_1,
                "qty": qty,
                "net_pnl": round(net_pnl, 2),
                "charges": round(charges, 2),
            })
            i += 16  # skip hold window

        if not trades:
            result["error"] = "no_trades"
            return result

        calc = PerformanceCalculator()
        report = calc.calculate(trades, capital)
        if report:
            result.update({
                "trades": report.total_trades,
                "win_rate": round(report.win_rate, 3),
                "profit_factor": round(report.profit_factor, 2),
                "expectancy": round(report.expectancy, 2),
                "net_pnl": round(report.net_pnl, 2),
                "sharpe": round(report.sharpe_ratio, 2),
                "max_drawdown_pct": round(report.max_drawdown_pct, 1),
                "passed": report.passed,
            })
    except Exception as e:
        result["error"] = str(e)[:100]

    return result


def _simulate(setup, qty, future_candles, charges, is_short):
    entry, sl, t1, t2 = setup.entry_price, setup.stop_loss, setup.target_1, setup.target_2
    partial_done = False
    pnl_partial = 0.0
    remaining = qty

    for _, candle in future_candles.iterrows():
        high = float(candle.get("high", entry))
        low = float(candle.get("low", entry))

        if is_short:
            if not partial_done and low <= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (entry - t1) * half
                    remaining -= half
                    partial_done = True
            if low <= t2 and remaining > 0:
                return round(pnl_partial + (entry - t2) * remaining - charges, 2)
            if high >= sl:
                return round(pnl_partial + (entry - sl) * remaining - charges, 2)
        else:
            if not partial_done and high >= t1:
                half = remaining // 2
                if half > 0:
                    pnl_partial = (t1 - entry) * half
                    remaining -= half
                    partial_done = True
            if high >= t2 and remaining > 0:
                return round(pnl_partial + (t2 - entry) * remaining - charges, 2)
            if low <= sl:
                return round(pnl_partial + (sl - entry) * remaining - charges, 2)

    # Time exit
    close = float(future_candles.iloc[-1]["close"]) if len(future_candles) > 0 else entry
    if is_short:
        return round(pnl_partial + (entry - close) * remaining - charges, 2)
    else:
        return round(pnl_partial + (close - entry) * remaining - charges, 2)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--strategies", nargs="+", default=ALL_STRATEGIES)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--capital", type=float, default=200000)
    parser.add_argument("--days", type=int, default=None,
                        help="Lookback days from today (e.g. 90 for 3 months). Overrides --start/--end.")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD")
    args = parser.parse_args()

    from research.watchlist_builder import NIFTY_200

    today = datetime.now(IST)
    end = args.end or today.strftime("%Y-%m-%d")
    if args.days:
        start = (today - timedelta(days=args.days)).strftime("%Y-%m-%d")
    elif args.start:
        start = args.start
    else:
        start = (today - timedelta(days=57)).strftime("%Y-%m-%d")

    interval = _pick_interval(start)

    total = len(NIFTY_200) * len(args.strategies)
    logger.info(f"Batch backtest: {len(NIFTY_200)} stocks × {len(args.strategies)} strategies = {total} runs")
    logger.info(f"Period: {start} to {end} | Interval: {interval} | Workers: {args.workers}")

    out_dir = ROOT / "backtesting/results"
    out_dir.mkdir(parents=True, exist_ok=True)

    tasks = [(sym, strat) for sym in NIFTY_200 for strat in args.strategies]
    results = []
    done = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(backtest_one, sym, strat, start, end, args.capital): (sym, strat)
                   for sym, strat in tasks}
        for fut in as_completed(futures):
            sym, strat = futures[fut]
            done += 1
            try:
                r = fut.result()
                results.append(r)
                status = f"PASS" if r["passed"] else (r["error"] or f"WR={r['win_rate']:.0%}")
                logger.info(f"[{done}/{total}] {sym} {strat}: {r['trades']}T WR={r['win_rate']:.0%} PnL=Rs.{r['net_pnl']:+.0f} | {status}")
            except Exception as e:
                logger.error(f"[{done}/{total}] {sym} {strat} CRASHED: {e}")
                results.append({"symbol": sym, "strategy": strat, "error": str(e), "trades": 0})

    # Save full results
    results_file = out_dir / f"batch_{end}.json"
    with open(results_file, "w") as f:
        json.dump(results, f, indent=2)

    # Summary table
    df = pd.DataFrame(results)
    df = df[df["trades"] > 0].copy()

    if df.empty:
        logger.warning("No trades generated across all backtests!")
        return

    print("\n" + "="*80)
    print(f"BATCH BACKTEST SUMMARY — {start} to {end} ({interval} candles)")
    print("="*80)

    # By strategy
    print("\n--- BY STRATEGY ---")
    for strat in args.strategies:
        s = df[df["strategy"] == strat]
        if s.empty:
            print(f"{strat}: no data")
            continue
        passed = s["passed"].sum()
        avg_wr = s["win_rate"].mean()
        avg_pnl = s["net_pnl"].mean()
        avg_pf = s["profit_factor"].mean()
        total_t = s["trades"].sum()
        print(f"{strat:20s} | {len(s):3d} stocks | {total_t:4d} trades | WR={avg_wr:.0%} | "
              f"AvgPnL=Rs.{avg_pnl:+.0f} | PF={avg_pf:.2f} | Passed={passed}/{len(s)}")

    # Top 20 stocks overall
    print("\n--- TOP 20 STOCKS (by net PnL, any strategy) ---")
    top = df.nlargest(20, "net_pnl")[["symbol","strategy","trades","win_rate","net_pnl","sharpe","passed"]]
    for _, row in top.iterrows():
        print(f"  {row['symbol']:15s} [{row['strategy']:15s}] "
              f"{row['trades']:2d}T WR={row['win_rate']:.0%} "
              f"PnL=Rs.{row['net_pnl']:+.0f} Sharpe={row['sharpe']:.1f} "
              f"{'PASS' if row['passed'] else 'fail'}")

    # Worst strategies (negative expectancy)
    print("\n--- STRATEGIES TO AVOID (negative avg PnL) ---")
    neg = df[df["net_pnl"] < 0].groupby("strategy")["net_pnl"].mean().sort_values()
    for strat, avg in neg.items():
        print(f"  {strat}: avg PnL Rs.{avg:+.0f}")

    # Save CSV summary
    csv_file = out_dir / f"batch_{end}.csv"
    df.to_csv(csv_file, index=False)
    print(f"\nFull results saved: {results_file}")
    print(f"CSV: {csv_file}")
    print("="*80)


if __name__ == "__main__":
    main()
