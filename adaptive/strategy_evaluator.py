"""
Adaptive Strategy Evaluator — self-improving layer for ZerodhaBot.

Runs weekly (scheduled or manual). Reads past N weeks of trade logs,
evaluates each strategy's performance, and writes adaptive decisions:
  - Disable strategies with negative rolling Sharpe
  - Allocate more capital to better-performing strategies
  - Re-enable disabled strategies after a cooldown if regime changes
  - Log every decision for transparency

Usage:
  python -m adaptive.strategy_evaluator              # default 4-week lookback
  python -m adaptive.strategy_evaluator --weeks 6    # custom lookback
"""

import json
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pytz

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("AdaptiveEvaluator")

ADAPTIVE_STATE_PATH = ROOT / "adaptive" / "adaptive_state.json"
ADAPTIVE_LOG_DIR = ROOT / "adaptive" / "logs"
TRADE_LOG_DIR = ROOT / "journaling" / "logs"

# Thresholds for disabling a strategy
MIN_TRADES_TO_EVALUATE = 10     # need at least N trades to judge (5 is just noise)
MIN_WIN_RATE = 0.30             # below 30% WR -> disable
MIN_PROFIT_FACTOR = 0.80        # below 0.8 PF (after estimated costs) -> disable
MAX_CONSEC_LOSSES = 6           # 6+ losses in a row -> disable
BOOST_MIN_TRADES = 30           # a strategy must have this many trades before it can be sized up
BOOST_MAX_WEIGHT = 1.25         # never size up more than this — thin samples lie
TRIAL_WEIGHT = 0.5              # weight on re-entry after a cooldown
COOLDOWN_WEEKS = 2              # disabled strategies stay off this long, then get a trial
DEFAULT_WEIGHT = 1.0            # baseline capital weight


@dataclass
class StrategyMetrics:
    strategy: str
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    gross_profit: float = 0.0
    gross_loss: float = 0.0
    net_pnl: float = 0.0
    profit_factor: float = 0.0
    sharpe: float = 0.0
    avg_pnl: float = 0.0
    max_consecutive_losses: int = 0
    regimes: Dict[str, int] = field(default_factory=dict)


@dataclass
class AdaptiveDecision:
    strategy: str
    action: str           # "enable" | "disable" | "maintain"
    reason: str
    weight: float         # capital allocation weight (0.0 = disabled, 1.0 = normal, 2.0 = boosted)
    metrics: dict
    timestamp: str


@dataclass
class AdaptiveState:
    last_evaluation: str
    lookback_weeks: int
    strategy_weights: Dict[str, float]       # strategy -> capital weight multiplier
    disabled_strategies: Dict[str, str]      # strategy -> disable_date
    decisions: List[dict]
    total_trades_evaluated: int
    overall_net_pnl: float
    trial_since: Dict[str, str] = field(default_factory=dict)  # strategy -> trial start (only post-trial trades count)


def load_trades(weeks: int) -> List[dict]:
    """Load all trades from the past N weeks of trade log files."""
    cutoff = datetime.now(IST) - timedelta(weeks=weeks)
    all_trades = []

    for fp in sorted(TRADE_LOG_DIR.glob("trades_*.json")):
        try:
            date_str = fp.stem.replace("trades_", "")
            file_date = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=IST)
            if file_date < cutoff:
                continue
            with open(fp) as f:
                trades = json.load(f)
            for t in trades:
                t["_date"] = date_str
            all_trades.extend(trades)
        except Exception as e:
            logger.warning(f"Skipping {fp.name}: {e}")

    return all_trades


def _cost_adjusted_pnl(t: dict) -> float:
    """Journaled net_pnl is gross when charges were never recorded (charges == 0).
    In that case subtract an estimate so the evaluator judges strategies after costs."""
    pnl = float(t.get("net_pnl", 0) or 0)
    if float(t.get("charges") or 0) > 0:
        return pnl
    try:
        from utils.charge_calculator import estimate_round_trip_charges
        entry = float(t.get("entry_price") or 0)
        exit_ = float(t.get("exit_price") or entry)
        qty = int(t.get("entry_qty") or 0)
        if entry > 0 and qty > 0:
            return pnl - estimate_round_trip_charges(entry, exit_, qty)
    except Exception:
        pass
    return pnl


def compute_strategy_metrics(trades: List[dict]) -> Dict[str, StrategyMetrics]:
    """Compute per-strategy performance metrics from trade list."""
    by_strategy = defaultdict(list)
    for t in trades:
        strategy = t.get("strategy", "unknown")
        by_strategy[strategy].append(t)

    results = {}
    for strategy, strades in by_strategy.items():
        m = StrategyMetrics(strategy=strategy)
        pnls = []

        for t in strades:
            if t.get("net_pnl") is None:
                continue  # still open — not a result yet
            pnl = _cost_adjusted_pnl(t)
            pnls.append(pnl)
            m.total_trades += 1
            if pnl > 0:
                m.wins += 1
                m.gross_profit += pnl
            else:
                m.losses += 1
                m.gross_loss += abs(pnl)

            regime = t.get("regime_at_entry", "unknown")
            m.regimes[regime] = m.regimes.get(regime, 0) + 1

        m.net_pnl = round(sum(pnls), 2)
        m.win_rate = m.wins / max(m.total_trades, 1)
        m.profit_factor = m.gross_profit / max(m.gross_loss, 0.01)
        m.avg_pnl = m.net_pnl / max(m.total_trades, 1)

        # Trade-level mean/std ratio — informational only (not annualised, not used to decide)
        if len(pnls) > 2:
            arr = np.array(pnls)
            std = arr.std()
            m.sharpe = round(float(arr.mean() / max(std, 0.01)), 2)
        else:
            m.sharpe = 0.0

        # Max consecutive losses
        cl = max_cl = 0
        for p in pnls:
            if p <= 0:
                cl += 1
                max_cl = max(max_cl, cl)
            else:
                cl = 0
        m.max_consecutive_losses = max_cl

        results[strategy] = m

    return results


def _weeks_since(iso: str) -> float:
    try:
        return (datetime.now(IST) - datetime.fromisoformat(iso)).total_seconds() / (7 * 86400)
    except Exception:
        return 0.0


def evaluate(weeks: int = 4) -> AdaptiveState:
    """Main evaluation: load trades, compute metrics, make decisions.

    Disabled / trial status lives in the saved state, not in this week's trades:
    a disabled strategy stops trading, so it would otherwise drop out of the
    metrics and quietly come back at full size.
    """
    trades = load_trades(weeks)
    logger.info(f"Loaded {len(trades)} trades from past {weeks} weeks")
    now_str = datetime.now(IST).isoformat()

    prev_state = _load_existing_state()
    if not trades:
        logger.warning("No trades found — keeping current state unchanged")
        return prev_state or AdaptiveState(now_str, weeks, {}, {}, [], 0, 0.0)

    prev_disabled = dict(prev_state.disabled_strategies) if prev_state else {}
    prev_trial = dict(prev_state.trial_since) if prev_state else {}

    decisions: List[AdaptiveDecision] = []
    weights: Dict[str, float] = {}
    disabled: Dict[str, str] = {}
    trial_since: Dict[str, str] = {}

    names = sorted({t.get("strategy", "unknown") for t in trades} | set(prev_disabled) | set(prev_trial))

    for strategy in names:
        since = prev_trial.get(strategy)
        # On trial, only trades taken since the trial began count against it
        s_trades = [t for t in trades if t.get("strategy", "unknown") == strategy
                    and (not since or t["_date"] >= since[:10])]
        m = compute_strategy_metrics(s_trades).get(strategy) or StrategyMetrics(strategy=strategy)

        reason_parts = []
        action = "maintain"
        weight = DEFAULT_WEIGHT

        if strategy in prev_disabled:
            waited = _weeks_since(prev_disabled[strategy])
            if waited < COOLDOWN_WEEKS:
                disabled[strategy] = prev_disabled[strategy]  # keep the ORIGINAL disable date
                action, weight = "disable", 0.0
                reason_parts.append(f"cooling down {waited:.1f}/{COOLDOWN_WEEKS}w")
            else:
                trial_since[strategy] = now_str
                action, weight = "enable", TRIAL_WEIGHT
                reason_parts.append(
                    f"cooldown over ({waited:.1f}w) — trial at {TRIAL_WEIGHT}x, judged on new trades only")
        elif m.total_trades < MIN_TRADES_TO_EVALUATE:
            reason_parts.append(f"only {m.total_trades} trades (need {MIN_TRADES_TO_EVALUATE})")
            if since:  # still proving itself after a cooldown
                trial_since[strategy] = since
                weight = TRIAL_WEIGHT
        else:
            fails = []
            if m.win_rate < MIN_WIN_RATE:
                fails.append(f"WR {m.win_rate:.0%} < {MIN_WIN_RATE:.0%}")
            if m.profit_factor < MIN_PROFIT_FACTOR:
                fails.append(f"PF {m.profit_factor:.2f} < {MIN_PROFIT_FACTOR}")
            if m.max_consecutive_losses >= MAX_CONSEC_LOSSES:
                fails.append(f"{m.max_consecutive_losses} losses in a row")

            if fails:
                action, weight = "disable", 0.0
                disabled[strategy] = now_str
                reason_parts.extend(fails)
            else:
                action = "enable"
                if m.profit_factor >= 1.5 and m.win_rate >= 0.5 and m.total_trades >= BOOST_MIN_TRADES:
                    weight = BOOST_MAX_WEIGHT
                    reason_parts.append(f"proven: PF {m.profit_factor:.2f}, WR {m.win_rate:.0%} over {m.total_trades} trades")
                elif m.profit_factor >= 1.0:
                    weight = 1.0
                    reason_parts.append(f"profitable after costs (PF {m.profit_factor:.2f})")
                else:
                    weight = 0.5
                    reason_parts.append(f"losing after costs (PF {m.profit_factor:.2f}) — half size")

        weights[strategy] = round(weight, 2)

        decision = AdaptiveDecision(
            strategy=strategy,
            action=action,
            reason="; ".join(reason_parts) if reason_parts else "default",
            weight=weight,
            metrics={
                "trades": m.total_trades,
                "win_rate": round(m.win_rate, 3),
                "pf": round(m.profit_factor, 2),
                "trade_ratio": m.sharpe,
                "net_pnl_after_costs": m.net_pnl,
                "avg_pnl": round(m.avg_pnl, 2),
                "max_consec_losses": m.max_consecutive_losses,
                "regimes": m.regimes,
            },
            timestamp=now_str,
        )
        decisions.append(decision)

        emoji = {"enable": "+", "disable": "X", "maintain": "="}[action]
        logger.info(
            f"[{emoji}] {strategy:20s} | weight={weight:.2f}x | "
            f"trades={m.total_trades} WR={m.win_rate:.0%} PF={m.profit_factor:.2f} "
            f"P&L(after costs)=Rs.{m.net_pnl:,.0f} | {'; '.join(reason_parts)}"
        )

    state = AdaptiveState(
        last_evaluation=now_str,
        lookback_weeks=weeks,
        strategy_weights=weights,
        disabled_strategies=disabled,
        decisions=[asdict(d) for d in decisions],
        total_trades_evaluated=len(trades),
        overall_net_pnl=round(sum(_cost_adjusted_pnl(t) for t in trades if t.get("net_pnl") is not None), 2),
        trial_since=trial_since,
    )

    _save_state(state)
    _save_evaluation_log(state, decisions)

    return state


def _load_existing_state() -> Optional[AdaptiveState]:
    """Load previously saved adaptive state."""
    if not ADAPTIVE_STATE_PATH.exists():
        return None
    try:
        with open(ADAPTIVE_STATE_PATH) as f:
            data = json.load(f)
        return AdaptiveState(**data)
    except Exception:
        return None


def _save_state(state: AdaptiveState):
    """Persist adaptive state to JSON."""
    ADAPTIVE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ADAPTIVE_STATE_PATH, "w") as f:
        json.dump(asdict(state), f, indent=2)
    logger.info(f"Adaptive state saved: {ADAPTIVE_STATE_PATH}")


def _save_evaluation_log(state: AdaptiveState, decisions: List[AdaptiveDecision]):
    """Save timestamped evaluation log for audit trail."""
    ADAPTIVE_LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(IST).strftime("%Y-%m-%d_%H%M")
    log_file = ADAPTIVE_LOG_DIR / f"eval_{ts}.json"
    with open(log_file, "w") as f:
        json.dump(asdict(state), f, indent=2)
    logger.info(f"Evaluation log: {log_file}")


def get_strategy_weight(strategy_name: str) -> float:
    """Read the current adaptive weight for a strategy. Used by main.py."""
    state = _load_existing_state()
    if state is None:
        return DEFAULT_WEIGHT
    return state.strategy_weights.get(strategy_name, DEFAULT_WEIGHT)


def get_active_strategies_adaptive(configured_strategies: List[str]) -> tuple:
    """Filter and weight strategies based on adaptive state.

    Returns:
        (active_strategies, weights_dict, disabled_list)

    Handles name normalization between config names (e.g. "orb", "ema_pullback")
    and trade log names (e.g. "ORB", "EMAPullback").
    """
    state = _load_existing_state()
    if state is None:
        return configured_strategies, {s: 1.0 for s in configured_strategies}, []

    # Build a case-insensitive lookup from state keys to weights
    # Trade logs store class names (ORB, EMAPullback), config uses snake_case (orb, ema_pullback)
    weight_lookup = {}
    disabled_lookup = set()
    for k, w in state.strategy_weights.items():
        weight_lookup[k.lower()] = w
        weight_lookup[k.lower().replace("_", "")] = w
    for k in state.disabled_strategies:
        disabled_lookup.add(k.lower())
        disabled_lookup.add(k.lower().replace("_", ""))

    active = []
    disabled = []
    weights = {}

    for s in configured_strategies:
        normalized = s.lower().replace("_", "")
        w = weight_lookup.get(s.lower(), weight_lookup.get(normalized, DEFAULT_WEIGHT))
        is_disabled = s.lower() in disabled_lookup or normalized in disabled_lookup
        if w <= 0 or is_disabled:
            disabled.append(s)
        else:
            active.append(s)
            weights[s] = w

    return active, weights, disabled


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Adaptive Strategy Evaluator")
    parser.add_argument("--weeks", type=int, default=4, help="Lookback period in weeks")
    args = parser.parse_args()

    state = evaluate(weeks=args.weeks)

    print(f"\n{'='*60}")
    print(f"ADAPTIVE EVALUATION COMPLETE")
    print(f"{'='*60}")
    print(f"Trades evaluated: {state.total_trades_evaluated}")
    print(f"Overall P&L:      Rs.{state.overall_net_pnl:,.2f}")
    print(f"\nStrategy Weights:")
    for s, w in state.strategy_weights.items():
        status = "DISABLED" if w <= 0 else f"{w:.1f}x capital"
        print(f"  {s:20s} -> {status}")
    print(f"\nDisabled strategies: {list(state.disabled_strategies.keys()) or 'none'}")
    print(f"{'='*60}")
