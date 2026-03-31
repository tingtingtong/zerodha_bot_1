"""
Tests for SimulatedBroker — including SL pending order fix.
"""
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from brokers.simulated_broker import SimulatedBroker
from brokers.base import OrderRequest, OrderSide, OrderType, ProductType, OrderStatus


@pytest.fixture
def broker():
    return SimulatedBroker(initial_capital=20000)


def buy(symbol="RELIANCE", price=2800.0, qty=3):
    return OrderRequest(symbol=symbol, side=OrderSide.BUY, quantity=qty,
                        order_type=OrderType.LIMIT, product=ProductType.MIS,
                        price=price, tag="test_buy")


def sell(symbol="RELIANCE", price=2850.0, qty=3):
    return OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=qty,
                        order_type=OrderType.LIMIT, product=ProductType.MIS,
                        price=price, tag="test_sell")


def sl_order(symbol="RELIANCE", trigger=2750.0, qty=3):
    return OrderRequest(symbol=symbol, side=OrderSide.SELL, quantity=qty,
                        order_type=OrderType.SL, product=ProductType.MIS,
                        price=round(trigger * 0.995, 2),
                        trigger_price=trigger, tag="test_sl")


# ── Basic buy/sell ─────────────────────────────────────────────────────────

def test_buy_creates_position(broker):
    resp = broker.place_order(buy())
    assert resp.status == OrderStatus.COMPLETE
    assert len(broker.get_positions()) == 1
    assert broker.get_positions()[0].symbol == "RELIANCE"
    assert broker.get_positions()[0].qty == 3


def test_sell_closes_position(broker):
    broker.place_order(buy())
    resp = broker.place_order(sell())
    assert resp.status == OrderStatus.COMPLETE
    assert len(broker.get_positions()) == 0


def test_capital_decreases_on_buy(broker):
    initial = broker.capital
    broker.place_order(buy(qty=1, price=1000))
    assert broker.capital < initial


def test_capital_increases_on_profitable_sell(broker):
    broker.place_order(buy(price=2800, qty=1))
    cap_after_buy = broker.capital
    broker.place_order(sell(price=2900, qty=1))
    assert broker.capital > cap_after_buy


def test_insufficient_capital_rejected(broker):
    req = OrderRequest(symbol="RELIANCE", side=OrderSide.BUY, quantity=10000,
                       order_type=OrderType.LIMIT, product=ProductType.MIS,
                       price=100000, tag="too_big")
    assert broker.place_order(req).status == OrderStatus.REJECTED


def test_sell_without_position_opens_short(broker):
    # SELL without an existing position now opens a short (paper short selling)
    resp = broker.place_order(sell())
    assert resp.status == OrderStatus.COMPLETE
    assert broker.positions["RELIANCE"].side == "short"


def test_charges_tracked(broker):
    broker.place_order(buy())
    broker.place_order(sell())
    assert broker.total_charges > 0


def test_zero_quantity_rejected(broker):
    req = OrderRequest(symbol="RELIANCE", side=OrderSide.BUY, quantity=0,
                       order_type=OrderType.LIMIT, product=ProductType.MIS,
                       price=2800, tag="zero_qty")
    assert broker.place_order(req).status == OrderStatus.REJECTED


def test_exit_all_positions(broker):
    broker.place_order(buy("RELIANCE", 2800, 2))
    broker.place_order(buy("TCS", 3500, 1))
    assert len(broker.get_positions()) == 2
    broker.exit_all_positions()
    assert len(broker.get_positions()) == 0


def test_market_order_includes_slippage(broker):
    broker.place_order(buy("RELIANCE", 2800, 1))
    req = OrderRequest(symbol="RELIANCE", side=OrderSide.SELL, quantity=1,
                       order_type=OrderType.MARKET, product=ProductType.MIS,
                       price=2850, tag="mkt_sell")
    resp = broker.place_order(req)
    assert resp.status == OrderStatus.COMPLETE
    assert resp.avg_fill_price < 2850  # slippage applied on sell


def test_update_position_price(broker):
    broker.place_order(buy(price=2800, qty=1))
    broker.update_position_price("RELIANCE", 2900)
    pos = broker.get_positions()[0]
    assert pos.current_price == 2900
    assert pos.unrealized_pnl == pytest.approx(100.0, abs=1)


# ── SL pending order behaviour ─────────────────────────────────────────────

def test_sl_order_stored_as_pending_not_filled(broker):
    """SL must NOT immediately sell the position — must wait for trigger."""
    broker.place_order(buy())
    resp = broker.place_order(sl_order(trigger=2750))
    assert resp.status == OrderStatus.OPEN
    assert len(broker.get_positions()) == 1  # position still alive


def test_sl_does_not_trigger_above_trigger_price(broker):
    broker.place_order(buy())
    broker.place_order(sl_order(trigger=2750))
    broker.update_position_price("RELIANCE", 2800)  # still above trigger
    assert len(broker.get_positions()) == 1


def test_sl_triggers_when_price_hits_trigger(broker):
    broker.place_order(buy())
    broker.place_order(sl_order(trigger=2750))
    broker.update_position_price("RELIANCE", 2740)  # below trigger
    assert len(broker.get_positions()) == 0


def test_sl_trigger_capital_restored(broker):
    broker.place_order(buy(price=2800, qty=1))
    cap_after_buy = broker.capital
    broker.place_order(sl_order(trigger=2750, qty=1))
    broker.update_position_price("RELIANCE", 2740)
    # Capital restored (minus the loss + charges)
    assert broker.capital > cap_after_buy  # got money back (less than entry but > 0)


def test_cancel_sl_removes_pending(broker):
    broker.place_order(buy())
    resp = broker.place_order(sl_order(trigger=2750))
    broker.cancel_order(resp.order_id)
    broker.update_position_price("RELIANCE", 2740)
    assert len(broker.get_positions()) == 1  # NOT triggered after cancel


def test_sl_limit_order_also_pending(broker):
    broker.place_order(buy())
    req = OrderRequest(symbol="RELIANCE", side=OrderSide.SELL, quantity=3,
                       order_type=OrderType.SL_LIMIT, product=ProductType.MIS,
                       price=2748, trigger_price=2750, tag="sl_limit")
    resp = broker.place_order(req)
    assert resp.status == OrderStatus.OPEN
    assert len(broker.get_positions()) == 1


def test_exit_all_clears_pending_sl(broker):
    broker.place_order(buy())
    broker.place_order(sl_order(trigger=2750))
    broker.exit_all_positions()
    assert len(broker.get_positions()) == 0
    assert len(broker._pending_sl) == 0


def test_sl_only_triggers_matching_symbol(broker):
    """Price drop on RELIANCE should not trigger TCS SL."""
    broker.place_order(buy("RELIANCE", 2800, 2))
    broker.place_order(buy("TCS", 3500, 1))
    broker.place_order(sl_order("RELIANCE", trigger=2750, qty=2))
    tcs_sl = OrderRequest(symbol="TCS", side=OrderSide.SELL, quantity=1,
                          order_type=OrderType.SL, product=ProductType.MIS,
                          price=3465, trigger_price=3470, tag="tcs_sl")
    broker.place_order(tcs_sl)
    broker.update_position_price("RELIANCE", 2740)  # only RELIANCE drops
    broker.update_position_price("TCS", 3510)
    positions = {p.symbol for p in broker.get_positions()}
    assert "RELIANCE" not in positions
    assert "TCS" in positions


def test_get_summary(broker):
    broker.place_order(buy(qty=1, price=1000))
    s = broker.get_summary()
    assert "capital" in s
    assert "open_positions" in s
    assert s["open_positions"] == 1
