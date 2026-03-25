import uuid
import json
import logging
from datetime import datetime
from typing import List, Dict
from pathlib import Path
import pytz

from .base import (BrokerBase, OrderRequest, OrderResponse, Position,
                   OrderStatus, OrderSide, OrderType, ProductType)
from utils.charge_calculator import calculate_charges, Segment

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
SLIPPAGE_PCT = 0.05


class SimulatedBroker(BrokerBase):

    def __init__(self, initial_capital: float):
        self.capital = initial_capital
        self.positions: Dict[str, Position] = {}
        self.orders: Dict[str, OrderResponse] = {}
        # Pending SL/SL-M orders waiting for price to hit trigger
        # { oid: (OrderRequest, trigger_price, seg) }
        self._pending_sl: Dict[str, tuple] = {}
        self.trade_log: List[dict] = []
        self.total_charges = 0.0
        self.total_gross_pnl = 0.0

    @property
    def broker_name(self) -> str:
        return "SimulatedBroker"

    def place_order(self, order: OrderRequest) -> OrderResponse:
        oid = f"SIM_{uuid.uuid4().hex[:8].upper()}"
        if order.quantity <= 0:
            return self._rejected(oid, "invalid_quantity")

        seg = Segment.EQUITY_DELIVERY if order.product == ProductType.CNC else Segment.EQUITY_INTRADAY

        # SL / SL-LIMIT orders: store as pending, do NOT fill immediately
        if order.order_type in (OrderType.SL, OrderType.SL_LIMIT):
            trigger = order.trigger_price or order.price or 0.0
            if trigger <= 0:
                return self._rejected(oid, "no_trigger_price")
            resp = OrderResponse(oid, OrderStatus.OPEN, order.quantity, 0.0,
                                 datetime.now(IST), "pending_sl", True)
            self.orders[oid] = resp
            self._pending_sl[oid] = (order, trigger, seg)
            logger.info(f"[PAPER SL  ] {order.quantity} {order.symbol} trigger@₹{trigger:.2f} — pending")
            return resp

        fp = self._fill_price(order)
        if fp <= 0:
            return self._rejected(oid, "no_price")

        if order.side == OrderSide.BUY:
            return self._buy(order, oid, fp, seg)
        return self._sell(order, oid, fp, seg)

    def _fill_price(self, order: OrderRequest) -> float:
        base = order.price or 0.0
        if base <= 0:
            return 0.0
        if order.order_type == OrderType.MARKET:
            slip = SLIPPAGE_PCT / 100
            return round(base * (1 + slip) if order.side == OrderSide.BUY else base * (1 - slip), 2)
        return base

    def _buy(self, order, oid, fp, seg):
        tv = fp * order.quantity
        charges = calculate_charges(tv, tv, seg).total
        cost = tv + charges
        if cost > self.capital:
            return self._rejected(oid, f"insufficient_capital_{self.capital:.0f}")
        self.capital -= cost
        self.total_charges += charges
        sym = order.symbol
        if sym in self.positions:
            pos = self.positions[sym]
            nq = pos.qty + order.quantity
            self.positions[sym].avg_price = round(
                (pos.avg_price * pos.qty + fp * order.quantity) / nq, 4)
            self.positions[sym].qty = nq
        else:
            self.positions[sym] = Position(
                symbol=sym, qty=order.quantity, avg_price=fp,
                current_price=fp, unrealized_pnl=0.0,
                product=order.product, side="long",
            )
        resp = OrderResponse(oid, OrderStatus.COMPLETE, order.quantity, fp,
                             datetime.now(IST), "sim_buy", True)
        self.orders[oid] = resp
        logger.info(f"[PAPER BUY ] {order.quantity} {sym} @₹{fp:.2f} | charges:₹{charges:.2f} | cap:₹{self.capital:,.2f}")
        return resp

    def _sell(self, order, oid, fp, seg):
        sym = order.symbol
        if sym not in self.positions or self.positions[sym].qty < order.quantity:
            return self._rejected(oid, "no_position")
        pos = self.positions[sym]
        buy_val = pos.avg_price * order.quantity
        sell_val = fp * order.quantity
        charges = calculate_charges(buy_val, sell_val, seg).total
        pnl = sell_val - buy_val - charges
        self.capital += sell_val - charges
        self.total_charges += charges
        self.total_gross_pnl += pnl
        pos.qty -= order.quantity
        if pos.qty == 0:
            del self.positions[sym]
        self._log(order, fp, pnl, charges, oid)
        resp = OrderResponse(oid, OrderStatus.COMPLETE, order.quantity, fp,
                             datetime.now(IST), "sim_sell", True)
        self.orders[oid] = resp
        sign = "+" if pnl >= 0 else ""
        logger.info(f"[PAPER SELL] {order.quantity} {sym} @₹{fp:.2f} | P&L:{sign}₹{pnl:.2f} | cap:₹{self.capital:,.2f}")
        return resp

    def _log(self, order, fp, pnl, charges, oid):
        self.trade_log.append({
            "timestamp": datetime.now(IST).isoformat(),
            "order_id": oid, "symbol": order.symbol,
            "side": order.side.value, "qty": order.quantity,
            "fill_price": fp, "realized_pnl": round(pnl, 2),
            "charges": round(charges, 2), "net_pnl": round(pnl, 2),
            "product": order.product.value,
        })

    def _rejected(self, oid, reason):
        resp = OrderResponse(oid, OrderStatus.REJECTED, 0, 0.0,
                             datetime.now(IST), reason, True)
        self.orders[oid] = resp
        logger.warning(f"[PAPER REJECT] {reason}")
        return resp

    def cancel_order(self, oid: str) -> bool:
        self._pending_sl.pop(oid, None)
        if oid in self.orders:
            self.orders[oid].status = OrderStatus.CANCELLED
            return True
        return False

    def get_order_status(self, oid: str) -> OrderResponse:
        return self.orders.get(oid, self._rejected(oid, "not_found"))

    def get_positions(self) -> List[Position]:
        return list(self.positions.values())

    def get_available_margin(self) -> float:
        return self.capital

    def update_position_price(self, symbol: str, price: float):
        if symbol in self.positions:
            p = self.positions[symbol]
            p.current_price = price
            p.unrealized_pnl = (price - p.avg_price) * p.qty

        # Check if any pending SL for this symbol should trigger
        for oid, (order, trigger, seg) in list(self._pending_sl.items()):
            if order.symbol != symbol:
                continue
            if order.side == OrderSide.SELL and price <= trigger:
                # Trigger hit — fill the SL order at trigger price (with slippage)
                fill_price = round(trigger * (1 - SLIPPAGE_PCT / 100), 2)
                resp = self._sell(order, oid, fill_price, seg)
                if resp.status == OrderStatus.COMPLETE:
                    self.orders[oid] = resp
                    del self._pending_sl[oid]
                    logger.info(f"[PAPER SL HIT] {order.symbol} trigger@₹{trigger:.2f} filled@₹{fill_price:.2f}")

    def exit_all_positions(self) -> List[OrderResponse]:
        # Cancel all pending SLs first
        self._pending_sl.clear()
        responses = []
        for sym, pos in list(self.positions.items()):
            req = OrderRequest(sym, OrderSide.SELL, pos.qty, OrderType.MARKET,
                               pos.product, pos.current_price, tag="emergency_exit")
            responses.append(self.place_order(req))
        return responses

    def is_connected(self) -> bool:
        return True

    def save_log(self, filepath: str):
        Path(filepath).parent.mkdir(parents=True, exist_ok=True)
        with open(filepath, "w") as f:
            json.dump(self.trade_log, f, indent=2)

    def get_summary(self) -> dict:
        return {
            "capital": round(self.capital, 2),
            "open_positions": len(self.positions),
            "total_trades": len(self.trade_log),
            "total_charges": round(self.total_charges, 2),
            "total_gross_pnl": round(self.total_gross_pnl, 2),
        }
