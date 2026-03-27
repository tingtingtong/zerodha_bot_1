import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List
import pytz

from brokers.base import (BrokerBase, OrderRequest, OrderResponse,
                           OrderType, OrderSide, ProductType, OrderStatus)
from .trade_state_machine import TradeRecord, TradeState, PartialExit

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")


class OrderManager:

    ORDER_WAIT_SEC = 25
    MAX_RETRIES = 2

    def __init__(self, broker: BrokerBase, risk_engine):
        self.broker = broker
        self.risk = risk_engine
        self.active_trades: Dict[str, TradeRecord] = {}
        self.completed_trades: List[TradeRecord] = []
        self._placed_keys_file = Path("journaling") / "placed_keys.json"
        self._placed_keys: set = self._load_placed_keys()

    def _load_placed_keys(self) -> set:
        try:
            if self._placed_keys_file.exists():
                data = json.loads(self._placed_keys_file.read_text())
                # Only use today's keys
                today = datetime.now().strftime("%Y-%m-%d")
                return set(data.get(today, []))
        except Exception:
            pass
        return set()

    def _save_placed_keys(self):
        try:
            today = datetime.now().strftime("%Y-%m-%d")
            self._placed_keys_file.parent.mkdir(parents=True, exist_ok=True)
            self._placed_keys_file.write_text(json.dumps({today: list(self._placed_keys)}, indent=2))
        except Exception:
            pass

    def execute_entry(self, trade: TradeRecord, current_price: float) -> bool:
        key = f"{trade.symbol}_{trade.entry_price}_{trade.entry_qty}"
        if key in self._placed_keys:
            logger.warning(f"Duplicate entry blocked: {trade.symbol}")
            return False

        # Round to 0.10 tick — satisfies both 0.05 and 0.10 tick stocks on NSE
        raw = current_price * 1.002
        limit_price = round(round(raw / 0.10) * 0.10, 2)
        req = OrderRequest(
            symbol=trade.symbol, side=OrderSide.BUY,
            quantity=trade.entry_qty, order_type=OrderType.LIMIT,
            product=ProductType.MIS, price=limit_price,
            tag=f"E_{trade.trade_id[:8]}",
        )
        trade.transition(TradeState.ENTRY_ORDERED)

        # Place order ONCE — no retry loop (prevents duplicate orders on exchange)
        resp = self.broker.place_order(req)

        if resp.status == OrderStatus.REJECTED:
            logger.error(f"Entry rejected {trade.symbol}: {resp.message}")
            trade.transition(TradeState.ERROR, error_message=resp.message)
            return False

        if resp.status == OrderStatus.COMPLETE:
            self._placed_keys.add(key)
            self._save_placed_keys()
            self._on_entry_filled(trade, resp.order_id, resp.avg_fill_price)
            return True

        # Order is OPEN/PENDING — poll for fill up to ORDER_WAIT_SEC
        order_id = resp.order_id
        deadline = time.time() + self.ORDER_WAIT_SEC
        while time.time() < deadline:
            time.sleep(5)
            status = self.broker.get_order_status(order_id)
            if status.status == OrderStatus.COMPLETE:
                self._placed_keys.add(key)
                self._save_placed_keys()
                self._on_entry_filled(trade, order_id, status.avg_fill_price)
                return True
            if status.status == OrderStatus.REJECTED:
                logger.error(f"Entry rejected after polling {trade.symbol}: {status.message}")
                trade.transition(TradeState.ERROR, error_message=status.message)
                return False

        # Order did not fill within wait window — cancel it (do NOT save placed_key)
        self.broker.cancel_order(order_id)
        logger.warning(f"Entry unfilled after {self.ORDER_WAIT_SEC}s — cancelled: {trade.symbol}")
        trade.transition(TradeState.ERROR, error_message="entry_timeout_cancelled")
        return False

    def _on_entry_filled(self, trade: TradeRecord, order_id: str, fill_price: float):
        # If fill price is below the signal price, SL may now be above fill — fix it
        if trade.stop_loss >= fill_price:
            adjusted = round(fill_price * 0.99, 2)  # 1% below fill as fallback
            logger.warning(f"SL {trade.stop_loss} >= fill {fill_price} — adjusting to {adjusted}")
            trade.stop_loss = adjusted
        trade.transition(
            TradeState.ENTRY_FILLED,
            entry_order_id=order_id,
            entry_price=fill_price,
            entry_time=datetime.now(IST),
        )
        self.active_trades[trade.trade_id] = trade
        self.risk.open_positions_count += 1
        logger.info(f"Entry filled: {trade.symbol} @Rs.{fill_price:.2f} x{trade.entry_qty}")
        self._place_sl(trade)

    def _place_sl(self, trade: TradeRecord):
        # Trigger must be strictly below fill price — guard against SL computed
        # too close to entry (e.g. tiny ATR, fast-moving fill vs signal price)
        safe_sl = min(trade.stop_loss, trade.entry_price - 0.20)
        trigger = round(round(safe_sl / 0.10) * 0.10, 2)
        # SL-M order: only trigger_price needed, no limit price
        req = OrderRequest(
            symbol=trade.symbol, side=OrderSide.SELL,
            quantity=trade.remaining_qty, order_type=OrderType.SL,
            product=ProductType.MIS,
            price=None,
            trigger_price=trigger,
            tag=f"SL_{trade.trade_id[:8]}",
        )
        resp = self.broker.place_order(req)
        if resp.status in (OrderStatus.OPEN, OrderStatus.COMPLETE):
            trade.transition(TradeState.SL_PLACED, sl_order_id=resp.order_id)
            logger.info(f"SL placed: {trade.symbol} trigger@₹{trigger:.2f}")
        else:
            logger.critical(f"SL FAILED {trade.symbol}: {resp.message} — MANUAL EXIT REQUIRED")

    def update_trailing_stop(self, trade_id: str, new_sl: float):
        trade = self.active_trades.get(trade_id)
        if not trade or new_sl <= trade.stop_loss:
            return
        if trade.sl_order_id:
            self.broker.cancel_order(trade.sl_order_id)
        trade.stop_loss = round(new_sl, 2)
        self._place_sl(trade)
        trade.transition(TradeState.TRAILING_ACTIVE)
        logger.info(f"Trailing SL: {trade.symbol} → ₹{new_sl:.2f}")

    def partial_exit(self, trade_id: str, qty: int, exit_price: float, reason: str):
        trade = self.active_trades.get(trade_id)
        if not trade or qty <= 0 or qty > trade.remaining_qty:
            return
        req = OrderRequest(
            symbol=trade.symbol, side=OrderSide.SELL, quantity=qty,
            order_type=OrderType.LIMIT, product=ProductType.MIS,
            price=round(round(exit_price * 0.999 / 0.10) * 0.10, 2), tag=f"P_{trade.trade_id[:8]}",
        )
        resp = self.broker.place_order(req)
        if resp.status == OrderStatus.COMPLETE:
            pnl = (resp.avg_fill_price - trade.entry_price) * qty
            trade.partial_exits.append(
                PartialExit(datetime.now(IST), qty, resp.avg_fill_price, reason, round(pnl, 2))
            )
            trade.remaining_qty -= qty
            if reason == "target_1_hit":
                self.update_trailing_stop(trade_id, trade.entry_price)
                trade.transition(TradeState.BREAKEVEN_MOVED)
            logger.info(f"Partial exit: {trade.symbol} {qty}@₹{resp.avg_fill_price:.2f} P&L:₹{pnl:.2f}")

    def close_trade(self, trade_id: str, exit_price: float, reason: str, charges: float = 0.0):
        trade = self.active_trades.get(trade_id)
        if not trade:
            return
        if trade.remaining_qty > 0:
            req = OrderRequest(
                symbol=trade.symbol, side=OrderSide.SELL,
                quantity=trade.remaining_qty, order_type=OrderType.MARKET,
                product=ProductType.MIS, price=exit_price,
                tag=f"X_{trade.trade_id[:8]}",
            )
            resp = self.broker.place_order(req)
            if resp.status == OrderStatus.COMPLETE:
                exit_price = resp.avg_fill_price

        partial_pnl = sum(pe.pnl for pe in trade.partial_exits)
        remaining_pnl = (exit_price - trade.entry_price) * trade.remaining_qty
        gross_pnl = partial_pnl + remaining_pnl
        net_pnl = gross_pnl - charges

        state_map = {
            "time_exit": TradeState.CLOSED_TIME,
            "emergency": TradeState.CLOSED_EMERGENCY,
        }
        final_state = state_map.get(reason,
            TradeState.CLOSED_PROFIT if net_pnl >= 0 else TradeState.CLOSED_LOSS)

        trade.transition(
            final_state, exit_price=exit_price, exit_time=datetime.now(IST),
            exit_qty=trade.entry_qty, realized_pnl=round(gross_pnl, 2),
            charges=round(charges, 2), net_pnl=round(net_pnl, 2),
        )
        self.risk.record_result(net_pnl, trade.symbol)
        self.risk.open_positions_count = max(0, self.risk.open_positions_count - 1)
        self.completed_trades.append(trade)
        del self.active_trades[trade_id]
        sign = "+" if net_pnl >= 0 else ""
        logger.info(f"Trade closed: {trade.symbol} [{reason}] P&L:{sign}₹{net_pnl:.2f}")

    def emergency_exit_all(self, reason: str = "emergency"):
        logger.critical(f"EMERGENCY EXIT ALL: {reason}")
        for tid in list(self.active_trades.keys()):
            self.close_trade(tid, 0.0, reason)
        self.broker.exit_all_positions()

    def tick(self, symbol: str, current_price: float):
        """Call on every price/candle update per active trade."""
        for tid, trade in list(self.active_trades.items()):
            if trade.symbol != symbol:
                continue
            trade.candles_held += 1

            # Time-based exit
            max_hold = getattr(trade, 'max_hold_candles', 16)
            if trade.candles_held >= max_hold:
                self.close_trade(tid, current_price, "time_exit")
                continue

            # Target 2 — full exit
            if current_price >= trade.target_2 and trade.is_open():
                self.close_trade(tid, current_price, "target_2_hit")
                continue

            # Target 1 — partial exit
            if (current_price >= trade.target_1
                    and trade.state not in (TradeState.TARGET_1_HIT,
                                            TradeState.BREAKEVEN_MOVED,
                                            TradeState.TRAILING_ACTIVE)):
                half = max(1, trade.entry_qty // 2)
                if half >= trade.entry_qty:
                    half = trade.entry_qty  # exit full position if qty too small to split
                self.partial_exit(tid, half, current_price, "target_1_hit")
                trade.state = TradeState.TARGET_1_HIT
                continue

            # Trailing SL update
            if trade.state == TradeState.TRAILING_ACTIVE and current_price > trade.entry_price:
                new_sl = round(current_price - trade.trailing_step, 2)
                if new_sl > trade.stop_loss:
                    self.update_trailing_stop(tid, new_sl)
