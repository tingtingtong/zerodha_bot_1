from datetime import date

import numpy as np
import pandas as pd
import pytest

from swing import momentum_rebalancer as m

CFG = dict(m.DEFAULTS, top_n=3, liquid_top=10)


def _data(n=300, bull=True):
    idx = pd.bdate_range(end="2026-09-30", periods=n)
    rng = np.random.default_rng(0)
    base = 100 * np.ones(n)
    cols = {}
    for name, drift in [("WIN1", 0.0035), ("WIN2", 0.0025), ("WIN3", 0.0015), ("FLAT", 0.0), ("LOSER", -0.002)]:
        cols[name] = 100 * np.cumprod(1 + drift + rng.normal(0, 0.002, n))
    close = pd.DataFrame(cols, index=idx)
    vol = pd.DataFrame(1_000_000, index=idx, columns=close.columns)
    d = 0.001 if bull else -0.002
    nifty = pd.Series(100 * np.cumprod(1 + d + rng.normal(0, 0.001, n)), index=idx)
    return close, vol, nifty


def test_picks_top_momentum_and_skips_losers():
    close, vol, nifty = _data()
    sel = m.select_targets(close, vol, nifty, CFG)
    assert sel["regime_ok"] and sel["targets"] == ["WIN1", "WIN2", "WIN3"]
    assert "LOSER" not in sel["targets"] and "FLAT" not in sel["targets"]


def test_bear_regime_means_cash():
    close, vol, nifty = _data(bull=False)
    assert m.select_targets(close, vol, nifty, CFG)["targets"] == []


def test_needs_enough_history():
    close, vol, nifty = _data(n=200)
    with pytest.raises(ValueError):
        m.select_targets(close, vol, nifty, CFG)


def test_plan_and_apply_equal_weight_then_rotate():
    state = m.new_state(300_000)
    prices = {"A": 100.0, "B": 200.0, "C": 50.0, "D": 400.0}
    orders = m.plan_orders(state, prices, ["A", "B", "C"], CFG)
    assert {o["symbol"] for o in orders} == {"A", "B", "C"} and all(o["side"] == "BUY" for o in orders)
    fills = m.apply_orders(state, orders, CFG, date(2026, 9, 30))
    assert len(fills) == 3 and state["cash"] >= 0 and state["total_charges"] > 0
    for s in "ABC":  # roughly equal weight (~100k each)
        assert state["holdings"][s]["qty"] * prices[s] == pytest.approx(100_000, rel=0.03)
    # next month: A drops out, D enters
    orders = m.plan_orders(state, prices, ["B", "C", "D"], CFG)
    sides = {(o["symbol"], o["side"]) for o in orders}
    assert ("A", "SELL") in sides and ("D", "BUY") in sides
    prices["A"] = 110.0
    m.apply_orders(state, orders, CFG, date(2026, 10, 30))
    assert "A" not in state["holdings"] and "D" in state["holdings"]
    assert state["cash"] >= 0 and state["realized_pnl"] != 0


def test_buys_never_exceed_cash():
    state = m.new_state(100_000)
    orders = m.plan_orders(state, {"A": 100.0, "B": 100.0}, ["A", "B"], dict(CFG, top_n=2))
    m.apply_orders(state, orders, CFG, date(2026, 9, 30))
    assert state["cash"] >= 0


def test_rebalance_day_is_last_trading_day_of_month():
    assert m.is_rebalance_day(date(2026, 9, 30))
    assert not m.is_rebalance_day(date(2026, 9, 29))
    assert not m.is_rebalance_day(date(2026, 10, 3))  # weekend
