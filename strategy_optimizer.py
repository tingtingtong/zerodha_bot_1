"""
Strategy Parameter Optimizer — Walk-Forward + Optuna (Bayesian Search)

How it works:
  1. Walk-forward folds: train on N months, test on next month (out-of-sample)
  2. Optuna finds best params on the TRAIN folds
  3. Final score = Sharpe on the held-out TEST fold (never seen during optimization)
  4. Best params saved to backtesting/results/best_params.json
  5. Strategies auto-load these on next startup

Usage:
    python strategy_optimizer.py                        # all 3 strategies, runs until Ctrl+C
    python strategy_optimizer.py --strategy ema_pullback
    python strategy_optimizer.py --trials 100           # Optuna trials per strategy per fold
    python strategy_optimizer.py --folds 4              # walk-forward folds
    python strategy_optimizer.py --workers 4            # parallel symbol evaluation
    python strategy_optimizer.py --capital 200000
"""

import argparse
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import optuna
import pytz

optuna.logging.set_verbosity(optuna.logging.WARNING)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
Path("backtesting/results").mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "backtesting/results/optimizer.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("Optimizer")

IST = pytz.timezone("Asia/Kolkata")
BEST_PARAMS_FILE = ROOT / "backtesting/results/best_params.json"

WATCHLIST = [
    "TITAGARH", "RVNL", "ONGC", "NBCC", "SBIN", "BHEL", "RAYMOND",
    "PRESTIGE", "BAJAJHLDNG", "VEDL", "CUMMINSIND", "DIXON", "SCHAEFFLER",
    "SRF", "INDUSTOWER", "WELCORP", "HUDCO", "COFORGE", "KPRMILL", "CRISIL",
]

# Correct regime per strategy
STRATEGY_REGIME = {
    "ema_pullback":  True,
    "ema_breakdown": False,
    "mean_reversion": False,
}

DEFAULT_PARAMS = {
    "ema_pullback": {
        "EMA_FAST": 9, "EMA_SLOW": 21, "MIN_RR": 1.5,
        "MIN_VOL_MULT": 1.0, "T1_MULT": 1.5, "T2_MULT": 2.5,
        "MAX_HOLD_CANDLES": 16,
    },
    "ema_breakdown": {
        "EMA_FAST": 9, "EMA_SLOW": 21, "MIN_RR": 1.5,
        "MIN_VOL_MULT": 1.0, "T1_MULT": 1.5, "T2_MULT": 2.5,
        "MAX_HOLD_CANDLES": 16,
    },
    "mean_reversion": {
        "RSI_OVERSOLD": 40, "RSI_MAX": 50, "MIN_RR": 1.3,
        "MAX_HOLD_CANDLES": 8, "VOLUME_MIN": 1.0,
        "T1_MULT": 1.2, "T2_MULT": 1.8,
    },
}


# ── Walk-forward fold generation ─────────────────────────────────────────────

def build_folds(n_folds: int, train_months: int = 2, test_months: int = 1):
    """
    Build walk-forward folds from today backwards.
    Each fold: (train_start, train_end, test_start, test_end)
    train_end == test_start, test_end = test_start + test_months
    """
    today = datetime.now(IST).replace(hour=0, minute=0, second=0, microsecond=0)
    folds = []
    for i in range(n_folds):
        test_end   = today - timedelta(days=i * 30 * test_months)
        test_start = test_end - timedelta(days=30 * test_months)
        train_end  = test_start
        train_start = train_end - timedelta(days=30 * train_months)
        folds.append((
            train_start.strftime("%Y-%m-%d"),
            train_end.strftime("%Y-%m-%d"),
            test_start.strftime("%Y-%m-%d"),
            test_end.strftime("%Y-%m-%d"),
        ))
    return folds  # most recent fold first


def _pick_interval(start: str) -> str:
    days_ago = (datetime.now(IST) - datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)).days
    if days_ago <= 58:
        return "15m"
    elif days_ago <= 729:
        return "1h"
    return "1d"


# ── Strategy class access ─────────────────────────────────────────────────────

def _get_cls(strategy_name):
    if strategy_name == "ema_pullback":
        from strategies.ema_pullback import EMAPullbackStrategy
        return EMAPullbackStrategy
    if strategy_name == "ema_breakdown":
        from strategies.ema_breakdown import EMABreakdownStrategy
        return EMABreakdownStrategy
    if strategy_name == "mean_reversion":
        from strategies.mean_reversion import MeanReversionStrategy
        return MeanReversionStrategy
    raise ValueError(f"Unknown: {strategy_name}")


def _apply(strategy_name, params):
    cls = _get_cls(strategy_name)
    orig = {}
    for k, v in params.items():
        orig[k] = getattr(cls, k, None)
        setattr(cls, k, v)
    return orig


def _restore(strategy_name, orig):
    cls = _get_cls(strategy_name)
    for k, v in orig.items():
        if v is None:
            try:
                delattr(cls, k)
            except AttributeError:
                pass
        else:
            setattr(cls, k, v)


# ── Backtest simulation ───────────────────────────────────────────────────────

def _simulate_trade(setup, qty, future_candles, charges, is_short):
    entry, sl, t1, t2 = setup.entry_price, setup.stop_loss, setup.target_1, setup.target_2
    partial_done = False
    pnl_partial = 0.0
    remaining = qty

    for _, candle in future_candles.iterrows():
        high = float(candle.get("high", entry))
        low  = float(candle.get("low",  entry))
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

    close = float(future_candles.iloc[-1]["close"]) if len(future_candles) > 0 else entry
    if is_short:
        return round(pnl_partial + (entry - close) * remaining - charges, 2)
    return round(pnl_partial + (close - entry) * remaining - charges, 2)


def _backtest_symbol(symbol, strategy_name, start, end, capital):
    """Run one symbol × window. Returns list of trade pnls, or []."""
    try:
        from data_providers.provider_registry import DataProviderRegistry
        from strategies.strategy_registry import get_strategy
        from utils.charge_calculator import estimate_round_trip_charges, Segment
        from config.capital_tiers import get_tier

        interval = _pick_interval(start)
        data = DataProviderRegistry.build_free_only()
        strategy = get_strategy(strategy_name)
        regime_bullish = STRATEGY_REGIME[strategy_name]

        from_dt = datetime.strptime(start, "%Y-%m-%d").replace(tzinfo=IST)
        to_dt   = datetime.strptime(end,   "%Y-%m-%d").replace(tzinfo=IST)

        daily_df    = data.get_historical(symbol, "1d", from_dt - timedelta(days=300), to_dt)
        intraday_df = data.get_historical(symbol, interval, from_dt, to_dt)

        if daily_df is None or len(daily_df) < 30:
            return []
        if intraday_df is None or len(intraday_df) < 60:
            return []

        account = capital
        pnls = []
        candle_window = 60
        i = candle_window

        while i < len(intraday_df):
            slice_in = intraday_df.iloc[max(0, i - candle_window): i + 1].copy()
            ts_col = "timestamp" if "timestamp" in slice_in.columns else None
            if ts_col:
                slice_daily = daily_df[daily_df["timestamp"] <= slice_in.iloc[-1][ts_col]].copy()
            else:
                slice_daily = daily_df.copy()

            if len(slice_daily) < 30:
                i += 1
                continue

            tier = get_tier(account)
            cap_trade = account * tier.max_per_trade_pct
            price = float(slice_in.iloc[-1]["close"])
            charges = estimate_round_trip_charges(
                price, price,
                qty=max(1, int(cap_trade / max(price, 1))),
                segment=Segment.EQUITY_INTRADAY,
            )

            setup = strategy.generate_signal(
                symbol=symbol, df_primary=slice_in, df_daily=slice_daily,
                regime_bullish=regime_bullish,
                capital_per_trade=cap_trade, charges_estimate=charges,
            )

            if not setup.is_valid:
                i += 1
                continue

            qty = max(1, int((account * tier.risk_per_trade_pct) /
                             max(abs(setup.entry_price - setup.stop_loss), 0.01)))
            qty = min(qty, int(cap_trade / max(setup.entry_price, 1)))
            if qty < 1:
                i += 16
                continue

            future  = intraday_df.iloc[i + 1: i + 17]
            net_pnl = _simulate_trade(setup, qty, future, charges,
                                      setup.signal.value == "short")
            account += net_pnl
            pnls.append(net_pnl)
            i += 16

        return pnls
    except Exception:
        return []


# ── Scoring ───────────────────────────────────────────────────────────────────

def _compute_score(all_pnls: list[float]) -> float:
    """
    Composite score from all trade P&Ls across all symbols in a window.
    Returns Sharpe-based score. Returns -1.0 if insufficient trades.
    """
    import numpy as np

    if len(all_pnls) < 5:
        return -1.0

    arr = np.array(all_pnls, dtype=float)
    win_rate = float(np.mean(arr > 0))
    profit_factor = (
        float(arr[arr > 0].sum()) / max(abs(float(arr[arr < 0].sum())), 1e-6)
    )
    mean_pnl = float(arr.mean())
    std_pnl  = float(arr.std()) if arr.std() > 0 else 1e-6
    sharpe   = mean_pnl / std_pnl * (252 ** 0.5)  # annualised

    # Reject if fundamentally broken
    if win_rate < 0.30:
        return -1.0
    if profit_factor < 0.8:
        return -1.0

    # Composite: Sharpe weighted by WR and trade count
    trade_bonus = min(len(all_pnls) / 20.0, 2.0)  # caps at 2x for 40+ trades
    return sharpe * win_rate * trade_bonus


def _eval_params(strategy_name, params, start, end, capital, workers):
    """Evaluate params on a date window. Returns score."""
    orig = _apply(strategy_name, params)
    try:
        all_pnls = []
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_backtest_symbol, sym, strategy_name, start, end, capital): sym
                       for sym in WATCHLIST}
            for fut in as_completed(futures):
                all_pnls.extend(fut.result())
        return _compute_score(all_pnls)
    finally:
        _restore(strategy_name, orig)


# ── Optuna objective ──────────────────────────────────────────────────────────

def _suggest_params(trial, strategy_name):
    """Let Optuna suggest param values using its Bayesian sampler."""
    if strategy_name in ("ema_pullback", "ema_breakdown"):
        ema_fast = trial.suggest_categorical("EMA_FAST", [5, 8, 9, 10, 13])
        ema_slow_choices = [x for x in [18, 20, 21, 26, 34] if x > ema_fast]
        ema_slow = trial.suggest_categorical("EMA_SLOW", ema_slow_choices or [21, 26])
        t1 = trial.suggest_categorical("T1_MULT", [1.2, 1.5, 1.8, 2.0])
        t2_choices = [x for x in [2.0, 2.5, 3.0, 3.5] if x > t1]
        t2 = trial.suggest_categorical("T2_MULT", t2_choices or [2.5, 3.0])
        return {
            "EMA_FAST":         ema_fast,
            "EMA_SLOW":         ema_slow,
            "MIN_RR":           trial.suggest_categorical("MIN_RR",        [1.2, 1.3, 1.5, 1.8, 2.0]),
            "MIN_VOL_MULT":     trial.suggest_categorical("MIN_VOL_MULT",  [0.8, 1.0, 1.2, 1.5, 2.0]),
            "T1_MULT":          t1,
            "T2_MULT":          t2,
            "MAX_HOLD_CANDLES": trial.suggest_categorical("MAX_HOLD_CANDLES", [12, 16, 20, 24]),
        }
    # mean_reversion
    rsi_os = trial.suggest_categorical("RSI_OVERSOLD", [28, 30, 35, 40, 45])
    rsi_max_choices = [x for x in [45, 50, 55] if x > rsi_os]
    rsi_max = trial.suggest_categorical("RSI_MAX", rsi_max_choices or [50, 55])
    t1 = trial.suggest_categorical("T1_MULT", [1.0, 1.2, 1.5, 1.8])
    t2_choices = [x for x in [1.5, 1.8, 2.0, 2.5, 3.0] if x > t1]
    t2 = trial.suggest_categorical("T2_MULT", t2_choices or [2.0, 2.5])
    return {
        "RSI_OVERSOLD":     rsi_os,
        "RSI_MAX":          rsi_max,
        "MIN_RR":           trial.suggest_categorical("MIN_RR",           [1.0, 1.2, 1.3, 1.5, 1.8]),
        "MAX_HOLD_CANDLES": trial.suggest_categorical("MAX_HOLD_CANDLES", [6, 8, 10, 12, 16]),
        "VOLUME_MIN":       trial.suggest_categorical("VOLUME_MIN",       [0.8, 1.0, 1.2, 1.5, 2.0]),
        "T1_MULT":          t1,
        "T2_MULT":          t2,
    }


def _make_objective(strategy_name, train_windows, capital, workers):
    """Returns an Optuna objective function that scores on TRAIN windows."""
    def objective(trial):
        params = _suggest_params(trial, strategy_name)

        # Score across all train windows — must be positive on majority
        scores = []
        for start, end in train_windows:
            s = _eval_params(strategy_name, params, start, end, capital, workers)
            scores.append(s)

        # Penalise if any train window is terrible
        bad = sum(1 for s in scores if s < 0)
        if bad > len(scores) // 2:
            return -2.0

        valid = [s for s in scores if s >= 0]
        return sum(valid) / len(valid) if valid else -1.0

    return objective


# ── Walk-forward optimization ─────────────────────────────────────────────────

def walk_forward_optimize(strategy_name, folds, trials_per_fold, capital, workers):
    """
    For each fold:
      - Optimize on train windows using Optuna (Bayesian)
      - Evaluate best params on the held-out test window (never seen during opt)
    Returns best params overall (highest out-of-sample score).
    """
    fold_results = []

    for fold_idx, (train_start, train_end, test_start, test_end) in enumerate(folds):
        logger.info(
            f"[{strategy_name}] Fold {fold_idx+1}/{len(folds)} | "
            f"TRAIN {train_start}→{train_end} | TEST {test_start}→{test_end}"
        )

        # Split train into two sub-windows for Optuna objective
        mid = datetime.strptime(train_start, "%Y-%m-%d") + (
            datetime.strptime(train_end, "%Y-%m-%d") -
            datetime.strptime(train_start, "%Y-%m-%d")
        ) / 2
        train_windows = [
            (train_start, mid.strftime("%Y-%m-%d")),
            (mid.strftime("%Y-%m-%d"), train_end),
        ]

        study = optuna.create_study(
            direction="maximize",
            sampler=optuna.samplers.TPESampler(seed=fold_idx),
            pruner=optuna.pruners.MedianPruner(n_startup_trials=10),
        )

        # Seed with current best params (warm start)
        existing = _load_best().get(strategy_name, DEFAULT_PARAMS[strategy_name])
        try:
            study.enqueue_trial(existing)
        except Exception:
            pass

        objective = _make_objective(strategy_name, train_windows, capital, workers)

        study.optimize(
            objective,
            n_trials=trials_per_fold,
            show_progress_bar=False,
        )

        best_trial = study.best_trial
        best_params = best_trial.params
        train_score = best_trial.value

        # ── Out-of-sample test ────────────────────────────────────────────
        oos_score = _eval_params(strategy_name, best_params, test_start, test_end, capital, workers)

        logger.info(
            f"[{strategy_name}] Fold {fold_idx+1} | "
            f"train={train_score:+.3f} | OOS={oos_score:+.3f} | "
            f"params={best_params}"
        )

        fold_results.append({
            "fold": fold_idx + 1,
            "train_start": train_start, "train_end": train_end,
            "test_start": test_start,   "test_end": test_end,
            "train_score": train_score,
            "oos_score": oos_score,
            "params": best_params,
        })

    return fold_results


# ── Persistence ───────────────────────────────────────────────────────────────

def _load_best():
    if BEST_PARAMS_FILE.exists():
        try:
            return json.loads(BEST_PARAMS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_best(best):
    BEST_PARAMS_FILE.write_text(json.dumps(best, indent=2))


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Walk-Forward + Optuna Strategy Optimizer")
    parser.add_argument("--strategy", nargs="+",
                        default=["ema_pullback", "ema_breakdown", "mean_reversion"],
                        choices=["ema_pullback", "ema_breakdown", "mean_reversion"])
    parser.add_argument("--folds",   type=int, default=3,
                        help="Walk-forward folds (default 3)")
    parser.add_argument("--trials",  type=int, default=50,
                        help="Optuna trials per fold (default 50)")
    parser.add_argument("--workers", type=int, default=4,
                        help="Parallel symbol workers (default 4)")
    parser.add_argument("--capital", type=float, default=200000)
    parser.add_argument("--loop",    action="store_true",
                        help="Loop continuously (re-run with rolling folds)")
    args = parser.parse_args()

    logger.info("=" * 65)
    logger.info("ZerodhaBot Strategy Optimizer — Walk-Forward + Optuna")
    logger.info(f"Strategies : {args.strategy}")
    logger.info(f"Folds      : {args.folds}  (train 2 months → test 1 month each)")
    logger.info(f"Trials     : {args.trials} Optuna trials per fold")
    logger.info(f"Workers    : {args.workers} parallel threads")
    logger.info(f"Capital    : Rs.{args.capital:,.0f}")
    logger.info(f"Loop       : {'yes (Ctrl+C to stop)' if args.loop else 'single pass'}")
    logger.info("=" * 65)

    round_num = 0
    all_best = _load_best()

    try:
        while True:
            round_num += 1
            if args.loop:
                logger.info(f"\n{'='*65}\nROUND {round_num}\n{'='*65}")

            folds = build_folds(args.folds)
            logger.info("\nWalk-forward schedule:")
            for i, (ts, te, vs, ve) in enumerate(folds):
                logger.info(f"  Fold {i+1}: TRAIN {ts}→{te}  |  TEST {vs}→{ve}")

            for strategy_name in args.strategy:
                logger.info(f"\n{'─'*65}")
                logger.info(f"Optimizing: {strategy_name}")
                logger.info(f"{'─'*65}")

                fold_results = walk_forward_optimize(
                    strategy_name, folds, args.trials, args.capital, args.workers
                )

                # Pick best params = fold with highest OOS score
                valid_folds = [r for r in fold_results if r["oos_score"] > 0]
                if not valid_folds:
                    logger.warning(f"[{strategy_name}] No fold produced positive OOS score — keeping current params")
                    continue

                # Use params from fold with best OOS score
                best_fold = max(valid_folds, key=lambda r: r["oos_score"])
                best_params = best_fold["params"]

                # Compare to current saved params
                old_params = all_best.get(strategy_name, DEFAULT_PARAMS[strategy_name])
                old_score  = _eval_params(strategy_name, old_params,
                                          best_fold["test_start"], best_fold["test_end"],
                                          args.capital, args.workers)

                logger.info(f"\n[{strategy_name}] RESULTS:")
                logger.info(f"  OOS scores across folds: "
                            f"{[round(r['oos_score'],3) for r in fold_results]}")
                logger.info(f"  Best fold #{best_fold['fold']}: OOS={best_fold['oos_score']:+.3f}")
                logger.info(f"  Current params OOS score on same window: {old_score:+.3f}")

                if best_fold["oos_score"] > old_score:
                    all_best[strategy_name] = best_params
                    _save_best(all_best)
                    logger.info(f"  *** IMPROVED — new params saved ***")
                else:
                    logger.info(f"  No improvement over current params — not updating")

                # Print param comparison
                print(f"\n  {strategy_name} param comparison:")
                print(f"  {'Param':<22} {'Current':>10} {'New':>10} {'Changed':>8}")
                print(f"  {'-'*54}")
                for k, new_v in best_params.items():
                    old_v = old_params.get(k, "—")
                    changed = "YES ◄" if new_v != old_v else ""
                    print(f"  {k:<22} {str(old_v):>10} {str(new_v):>10} {changed:>8}")

            # Save fold analysis
            results_path = ROOT / "backtesting/results/optimizer_walkforward.json"
            try:
                history = json.loads(results_path.read_text()) if results_path.exists() else []
            except Exception:
                history = []
            history.append({
                "round": round_num,
                "timestamp": datetime.now(IST).isoformat(),
                "strategies": {s: all_best.get(s, DEFAULT_PARAMS[s]) for s in args.strategy},
            })
            results_path.write_text(json.dumps(history, indent=2))

            if not args.loop:
                break

    except KeyboardInterrupt:
        logger.info("\nStopped by user.")

    # Final summary
    print("\n" + "=" * 65)
    print("FINAL BEST PARAMS")
    print("=" * 65)
    for sname in args.strategy:
        params = all_best.get(sname, DEFAULT_PARAMS[sname])
        default = DEFAULT_PARAMS[sname]
        print(f"\n{sname}:")
        for k, v in params.items():
            d = default.get(k)
            marker = f"  ← was {d}" if v != d else ""
            print(f"  {k:<22} {v}{marker}")

    print(f"\nSaved: {BEST_PARAMS_FILE}")
    print("Restart the bot to apply optimized params.")


if __name__ == "__main__":
    main()
