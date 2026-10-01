"""
Monthly momentum rebalancer — PAPER ONLY, separate from the intraday bot.

Strategy (backtested 2017-2026, see config swing_momentum):
  - Universe: NIFTY-200 list, restricted at runtime to the top-N most liquid names
    (126-day average traded value) so stocks that were tiny at the time aren't picked.
  - Score: 12-1 month momentum (return from t-252 to t-21 trading days).
  - Eligible: close > 200-day average, score > 0.
  - Hold the top `top_n`, equal weight (1/top_n of sleeve equity each).
  - Regime filter: if NIFTY is below its 200-day average, hold cash.
  - Rebalance on the last trading day of each month, after the close.

State lives in journaling/swing_state.json; every paper fill is logged to journaling/swing/.
It never touches a broker. Run daily after close (Task Scheduler); it only trades on
rebalance day, on first run (bootstrap), or with --force.

Usage:
  python -m swing.momentum_rebalancer --dry-run     # show target portfolio + orders, change nothing
  python -m swing.momentum_rebalancer               # daily run (marks to market; rebalances if due)
  python -m swing.momentum_rebalancer --force       # rebalance now regardless of the calendar
"""

import argparse
import json
import logging
import math
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import pytz
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

IST = pytz.timezone("Asia/Kolkata")
STATE_PATH = ROOT / "journaling" / "swing_state.json"
TRADE_DIR = ROOT / "journaling" / "swing"
logger = logging.getLogger("SwingMomentum")

DEFAULTS = {
    "enabled": True,
    "capital": 2_000_000,       # separate paper sleeve
    "top_n": 25,
    "liquid_top": 100,          # only the N most liquid names are eligible
    "regime_filter": True,      # NIFTY below 200-DMA -> cash
    "slippage_pct": 0.001,      # 0.1% per side, same as the backtest
    "min_trade_value": 5000,
    "drift_tolerance": 0.25,    # don't trade a holding unless it is >25% off its target weight
}


def load_cfg(path: str = "config/config.yaml") -> dict:
    cfg = dict(DEFAULTS)
    try:
        with open(ROOT / path) as f:
            cfg.update((yaml.safe_load(f) or {}).get("swing_momentum", {}) or {})
    except FileNotFoundError:
        pass
    return cfg


# ── pure logic (unit-tested) ─────────────────────────────────────────────────

def select_targets(close: pd.DataFrame, vol: pd.DataFrame, nifty: pd.Series, cfg: dict) -> dict:
    """Decide the target portfolio as of the last bar. Pure function of the data."""
    if len(close) < 253:
        raise ValueError(f"need >= 253 daily bars, have {len(close)}")
    last = close.iloc[-1]
    traded = last.notna() & (vol.iloc[-1].fillna(0) > 0)
    tv = (close * vol).where(close.notna() & (vol.fillna(0) > 0)).rolling(126, min_periods=60).mean().iloc[-1]
    liquid = tv.where(traded).nlargest(cfg["liquid_top"]).index
    score = close.iloc[-22] / close.iloc[-253] - 1
    sma200 = close.rolling(200).mean().iloc[-1]
    ok = pd.Series(close.columns.isin(liquid), index=close.columns) & (last > sma200) & (score > 0)
    ranked = score.where(ok).dropna().sort_values(ascending=False)

    regime_ok = True
    if cfg["regime_filter"]:
        regime_ok = bool(nifty.iloc[-1] > nifty.rolling(200).mean().iloc[-1])
    targets = list(ranked.index[: cfg["top_n"]]) if regime_ok else []
    return {"asof": close.index[-1], "regime_ok": regime_ok, "targets": targets,
            "scores": ranked.head(cfg["top_n"]).round(3).to_dict(), "eligible": int(ok.sum())}


def equity_of(state: dict, prices: Dict[str, float]) -> float:
    return state["cash"] + sum(h["qty"] * prices.get(s, h["avg_price"]) for s, h in state["holdings"].items())


def plan_orders(state: dict, prices: Dict[str, float], targets: List[str], cfg: dict) -> List[dict]:
    """Sells first (dropouts and big overweights), then buys limited by available cash."""
    equity = equity_of(state, prices)
    tgt_val = equity / cfg["top_n"]
    tol = cfg["drift_tolerance"]
    orders, cash = [], state["cash"]

    for s, h in state["holdings"].items():
        px = prices.get(s)
        if not px:
            continue
        if s not in targets:
            orders.append({"symbol": s, "side": "SELL", "qty": h["qty"], "price": px, "why": "dropped out"})
        elif h["qty"] * px > tgt_val * (1 + tol):
            qty = int((h["qty"] * px - tgt_val) / px)
            if qty >= 1 and qty * px >= cfg["min_trade_value"]:
                orders.append({"symbol": s, "side": "SELL", "qty": qty, "price": px, "why": "trim overweight"})
    cash += sum(o["qty"] * o["price"] for o in orders)

    buys = []
    for s in targets:
        px = prices.get(s)
        if not px:
            continue
        held = state["holdings"].get(s, {}).get("qty", 0) * px
        if held < tgt_val * (1 - tol):
            qty = int((tgt_val - held) / px)
            if qty >= 1 and qty * px >= cfg["min_trade_value"]:
                buys.append({"symbol": s, "side": "BUY", "qty": qty, "price": px,
                             "why": "new entry" if held == 0 else "top up"})
    need = sum(o["qty"] * o["price"] for o in buys)
    scale = min(1.0, cash * 0.995 / need) if need > 0 else 1.0   # keep a sliver for costs/slippage
    for o in buys:
        o["qty"] = int(o["qty"] * scale)
    orders += [o for o in buys if o["qty"] >= 1 and o["qty"] * o["price"] >= cfg["min_trade_value"]]
    return orders


def apply_orders(state: dict, orders: List[dict], cfg: dict, today: date) -> List[dict]:
    """Paper-fill at close +/- slippage, delivery charges, update state. Returns fill records."""
    from utils.charge_calculator import calculate_charges, Segment
    fills = []
    for o in sorted(orders, key=lambda x: x["side"] != "SELL"):  # sells first
        slip = cfg["slippage_pct"]
        px = o["price"] * (1 + slip if o["side"] == "BUY" else 1 - slip)
        val = px * o["qty"]
        ch = calculate_charges(val if o["side"] == "BUY" else 0.0, val if o["side"] == "SELL" else 0.0,
                               Segment.EQUITY_DELIVERY).total
        h = state["holdings"].get(o["symbol"])
        pnl = None
        if o["side"] == "BUY":
            if val + ch > state["cash"]:
                continue
            state["cash"] -= val + ch
            if h:
                tot = h["qty"] + o["qty"]
                h["avg_price"] = (h["avg_price"] * h["qty"] + px * o["qty"]) / tot
                h["qty"] = tot
            else:
                state["holdings"][o["symbol"]] = {"qty": o["qty"], "avg_price": px, "entry_date": today.isoformat()}
        else:
            state["cash"] += val - ch
            pnl = (px - h["avg_price"]) * o["qty"] - ch
            state["realized_pnl"] += pnl
            h["qty"] -= o["qty"]
            if h["qty"] <= 0:
                del state["holdings"][o["symbol"]]
        state["total_charges"] += ch
        fills.append({"date": today.isoformat(), "symbol": o["symbol"], "side": o["side"], "qty": o["qty"],
                      "fill_price": round(px, 2), "charges": round(ch, 2),
                      "realized_pnl": None if pnl is None else round(pnl, 2), "why": o["why"]})
    return fills


def is_rebalance_day(today: date) -> bool:
    from utils.time_utils import is_trading_day, next_trading_day
    return is_trading_day(today) and next_trading_day(today).month != today.month


# ── state + data (I/O) ───────────────────────────────────────────────────────

def new_state(capital: float) -> dict:
    return {"start_capital": capital, "cash": float(capital), "holdings": {}, "last_rebalance": None,
            "realized_pnl": 0.0, "total_charges": 0.0, "equity_history": []}


def load_state() -> Optional[dict]:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return None


def save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=float))


def log_fills(fills: List[dict], today: date):
    if not fills:
        return
    TRADE_DIR.mkdir(parents=True, exist_ok=True)
    fp = TRADE_DIR / f"swing_trades_{today.isoformat()}.json"
    prev = json.loads(fp.read_text()) if fp.exists() else []
    fp.write_text(json.dumps(prev + fills, indent=2))


def fetch(symbols: List[str], days: int = 460):
    import yfinance as yf
    start = (datetime.now(IST) - timedelta(days=days)).strftime("%Y-%m-%d")
    tick = [s + ".NS" for s in symbols] + ["^NSEI"]
    d = yf.download(tick, start=start, interval="1d", progress=False, auto_adjust=True,
                    group_by="column", threads=True)
    close, vol = d["Close"], d["Volume"]
    nifty = close["^NSEI"].dropna()
    cols = [c for c in close.columns if c != "^NSEI"]
    close, vol = close[cols], vol[cols]
    close.columns = [c[:-3] for c in cols]
    vol.columns = close.columns
    return close, vol, nifty.reindex(close.index).ffill()


def run(dry_run: bool = False, force: bool = False, cfg_path: str = "config/config.yaml") -> dict:
    from utils.time_utils import is_trading_day, now_ist
    cfg = load_cfg(cfg_path)
    now = now_ist()
    today = now.date()
    if not cfg["enabled"]:
        return {"status": "disabled"}
    if not is_trading_day(today) and not (force or dry_run):
        return {"status": "not a trading day"}

    state = load_state()
    bootstrap = state is None
    if bootstrap:
        state = new_state(cfg["capital"])
    due = force or bootstrap or (is_rebalance_day(today) and state["last_rebalance"] != today.isoformat())
    market_open = now.hour * 60 + now.minute < 15 * 60 + 35
    if due and market_open and not (force or dry_run):
        return {"status": "rebalance is due but market is still open — run after 15:35 IST"}

    if due or dry_run:
        from research.watchlist_builder import NIFTY_200
        close, vol, nifty = fetch(list(dict.fromkeys(NIFTY_200)))
        sel = select_targets(close, vol, nifty, cfg)
        prices = {s: float(close[s].dropna().iloc[-1]) for s in set(sel["targets"]) | set(state["holdings"])
                  if s in close.columns and close[s].notna().any()}
        orders = plan_orders(state, prices, sel["targets"], cfg)
        result = {"status": "dry-run" if dry_run else "rebalanced", "asof": str(sel["asof"].date()),
                  "regime_ok": sel["regime_ok"], "eligible": sel["eligible"], "targets": sel["targets"],
                  "orders": orders, "equity_before": round(equity_of(state, prices), 2)}
        if not dry_run:
            fills = apply_orders(state, orders, cfg, today)
            log_fills(fills, today)
            state["last_rebalance"] = today.isoformat()
            result["fills"] = len(fills)
    else:
        held = list(state["holdings"])
        prices = {}
        if held:
            close, _, _ = fetch(held)
            prices = {s: float(close[s].dropna().iloc[-1]) for s in held if close[s].notna().any()}
        result = {"status": "marked to market (no rebalance due)"}

    eq = equity_of(state, prices)
    result.update(equity=round(eq, 2), cash=round(state["cash"], 2), positions=len(state["holdings"]),
                  return_pct=round((eq / state["start_capital"] - 1) * 100, 2))
    if not dry_run:
        hist = [h for h in state["equity_history"] if h["date"] != today.isoformat()]
        hist.append({"date": today.isoformat(), "equity": round(eq, 2)})
        state["equity_history"] = hist
        save_state(state)
        _notify(result)
    return result


def _notify(result: dict):
    if not result["status"].startswith("rebalanced"):
        return
    try:
        from utils.notification import TelegramNotifier
        TelegramNotifier().send(
            f"📈 Swing momentum rebalance (paper)\nRegime {'OK' if result['regime_ok'] else 'BEAR → cash'} | "
            f"{result['positions']} positions | equity Rs.{result['equity']:,.0f} ({result['return_pct']:+.1f}%)\n"
            f"{result['fills']} fills")
    except Exception as e:
        logger.debug(f"notify skipped: {e}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--config", default="config/config.yaml")
    a = ap.parse_args()
    res = run(a.dry_run, a.force, a.config)
    orders = res.pop("orders", [])
    print(json.dumps(res, indent=2, default=str))
    if orders:
        print(f"\n{len(orders)} orders:")
        for o in orders:
            print(f"  {o['side']:<4} {o['symbol']:<12} {o['qty']:>6} @ {o['price']:>9.2f}  ({o['why']})")
