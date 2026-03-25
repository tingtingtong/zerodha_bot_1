import logging
from datetime import datetime
from typing import List
import pytz

from .base import (BrokerBase, OrderRequest, OrderResponse, Position,
                   OrderStatus, OrderSide, OrderType, ProductType)

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

ORDER_TYPE_MAP = {OrderType.MARKET: "MARKET", OrderType.LIMIT: "LIMIT",
                  OrderType.SL: "SL-M", OrderType.SL_LIMIT: "SL"}
PRODUCT_MAP = {ProductType.MIS: "MIS", ProductType.CNC: "CNC", ProductType.NRML: "NRML"}
STATUS_MAP = {"COMPLETE": OrderStatus.COMPLETE, "OPEN": OrderStatus.OPEN,
              "REJECTED": OrderStatus.REJECTED, "CANCELLED": OrderStatus.CANCELLED,
              "TRIGGER PENDING": OrderStatus.OPEN}


class ZerodhaExecutionAdapter(BrokerBase):

    def __init__(self, kite):
        self.kite = kite

    @property
    def broker_name(self) -> str:
        return "ZerodhaKite"

    def place_order(self, order: OrderRequest) -> OrderResponse:
        try:
            from kiteconnect import KiteConnect
            params = dict(
                tradingsymbol=order.symbol, exchange=order.exchange,
                transaction_type="BUY" if order.side == OrderSide.BUY else "SELL",
                quantity=order.quantity, order_type=ORDER_TYPE_MAP[order.order_type],
                product=PRODUCT_MAP[order.product], validity="DAY",
                tag=(order.tag or "bot")[:20],
            )
            if order.price:
                params["price"] = order.price
            if order.trigger_price:
                params["trigger_price"] = order.trigger_price
            oid = self.kite.place_order(variety=KiteConnect.VARIETY_REGULAR, **params)
            logger.info(f"[LIVE] {order.side.value} {order.quantity} {order.symbol} | ID:{oid}")
            return OrderResponse(str(oid), OrderStatus.OPEN, 0, 0.0, datetime.now(IST), "accepted")
        except Exception as e:
            logger.error(f"Order failed {order.symbol}: {e}")
            return OrderResponse("", OrderStatus.REJECTED, 0, 0.0, datetime.now(IST), str(e))

    def cancel_order(self, oid: str) -> bool:
        try:
            from kiteconnect import KiteConnect
            self.kite.cancel_order(variety=KiteConnect.VARIETY_REGULAR, order_id=oid)
            return True
        except Exception as e:
            logger.error(f"Cancel failed {oid}: {e}")
            return False

    def get_order_status(self, oid: str) -> OrderResponse:
        try:
            for o in self.kite.orders():
                if str(o["order_id"]) == oid:
                    return OrderResponse(
                        oid, STATUS_MAP.get(o["status"], OrderStatus.PENDING),
                        o["filled_quantity"], o.get("average_price", 0.0),
                        datetime.now(IST), o.get("status_message", ""),
                    )
        except Exception as e:
            logger.error(f"Order status failed {oid}: {e}")
        return OrderResponse(oid, OrderStatus.REJECTED, 0, 0.0, datetime.now(IST), "not_found")

    def get_positions(self) -> List[Position]:
        try:
            return [
                Position(p["tradingsymbol"], abs(p["quantity"]), p["average_price"],
                         p["last_price"], p["unrealised"], ProductType.MIS,
                         "long" if p["quantity"] > 0 else "short")
                for p in self.kite.positions().get("day", []) if p["quantity"] != 0
            ]
        except Exception as e:
            logger.error(f"Positions failed: {e}")
            return []

    def get_available_margin(self) -> float:
        try:
            m = self.kite.margins("equity")
            # Use net (total funds including intraday top-up) not just cash
            return float(m.get("net", 0) or m.get("available", {}).get("live_balance", 0))
        except Exception:
            return 0.0

    def exit_all_positions(self) -> List[OrderResponse]:
        responses = []
        for pos in self.get_positions():
            req = OrderRequest(pos.symbol, OrderSide.SELL, pos.qty,
                               OrderType.MARKET, pos.product, tag="emergency_exit")
            responses.append(self.place_order(req))
        return responses

    def is_connected(self) -> bool:
        try:
            self.kite.profile()
            return True
        except Exception:
            return False
