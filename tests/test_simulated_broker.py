import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from brokers.simulated_broker import SimulatedBroker
from brokers.base import OrderRequest, OrderSide, OrderType, ProductType, OrderStatus


@pytest.fixture
def broker():
    return SimulatedBroker(initial_capital=20000)


def make_buy(symbol="RELIANCE", price=2800.0, qty=3):
    return OrderRequest(
        symbol=symbol, side=OrderSide.BUY, quantity=qty,
        order_type=OrderType.LIMIT, product=ProductType.MIS,
        price=price, tag="test_buy",
    )


def make_sell(symbol="RELIANCE", price=2850.0, qty=3):
    return OrderRequest(
        symbol=symbol, side=OrderSide.SELL, quantity=qty,
        order_type=OrderType.LIMIT, product=ProductType.MIS,
        price=price, tag="test_sell",
    )


def test_buy_creates_position(broker):
    resp = broker.place_order(make_buy())
    assert resp.status == OrderStatus.COMPLETE
    positions = broker.get_positions()
    assert len(positions) == 1
    assert positions[0].symbol == "RELIANCE"
    assert positions[0].qty == 3


def test_sell_closes_position(broker):
    broker.place_order(make_buy())
    resp = broker.place_order(make_sell())
    assert resp.status == OrderStatus.COMPLETE
    assert len(broker.get_positions()) == 0


def test_capital_decreases_on_buy(broker):
    initial = broker.capital
    broker.place_order(make_buy(qty=1, price=1000))
    assert broker.capital < initial


def test_capital_increases_on_profitable_sell(broker):
    broker.place_order(make_buy(price=2800, qty=1))
    capital_after_buy = broker.capital
    broker.place_order(make_sell(price=2900, qty=1))
    assert broker.capital > capital_after_buy


def test_insufficient_capital_rejected(broker):
    req = OrderRequest(
        symbol="RELIANCE", side=OrderSide.BUY, quantity=10000,
        order_type=OrderType.LIMIT, product=ProductType.MIS,
        price=100000, tag="too_big",
    )
    resp = broker.place_order(req)
    assert resp.status == OrderStatus.REJECTED


def test_sell_without_position_rejected(broker):
    resp = broker.place_order(make_sell())
    assert resp.status == OrderStatus.REJECTED


def test_charges_tracked(broker):
    broker.place_order(make_buy())
    broker.place_order(make_sell())
    assert broker.total_charges > 0


def test_exit_all_positions(broker):
    broker.place_order(make_buy("RELIANCE", 2800, 2))
    broker.place_order(make_buy("TCS", 3500, 1))
    assert len(broker.get_positions()) == 2
    broker.exit_all_positions()
    assert len(broker.get_positions()) == 0
