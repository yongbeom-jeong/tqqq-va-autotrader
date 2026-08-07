from __future__ import annotations

import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Callable

from config import Settings
from persistence.state import StateStore
from toss.orders import OrdersAPI
from va.calculator import OrderLeg, RebalancePlan


TERMINAL = {"FILLED", "CANCELED", "REJECTED", "REPLACED"}


class ExecutionEngine:
    """Executes at most two limit orders, never concurrently."""

    def __init__(
        self, settings: Settings, orders: OrdersAPI, account_seq: str,
        state: StateStore, refresh_plan: Callable[[], RebalancePlan],
        reserve_quote: Callable[[], tuple[Decimal, Decimal]],
        buying_power: Callable[[], Decimal], sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.settings, self.orders, self.account_seq = settings, orders, account_seq
        self.state, self.refresh_plan = state, refresh_plan
        self.reserve_quote, self.buying_power, self.sleep = reserve_quote, buying_power, sleep
        self.monotonic = monotonic

    def run(self, initial: RebalancePlan) -> list[dict]:
        with self.state.execution_lock():
            return self._run_locked(initial)

    def _run_locked(self, initial: RebalancePlan) -> list[dict]:
        previous = self.state.load()
        if previous and previous.get("status") not in {"COMPLETE", "FAILED"}:
            raise RuntimeError(
                "An unresolved execution cycle exists. Inspect execution_state.json and open orders; "
                "new orders are blocked."
            )
        cycle_id = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        record = {"cycle_id": cycle_id, "status": "STARTED", "decision": initial.decision.value, "orders": []}
        self.state.save(record)
        try:
            if initial.first_leg is None:
                record["status"] = "COMPLETE"
                self.state.save(record)
                return []
            first = self._execute_leg(initial.first_leg, cycle_id, record)
            if initial.second_leg_requires_recalculation:
                second = self._recalculate_second(initial, first)
                if second is not None:
                    self._execute_leg(second, cycle_id, record)
            record["status"] = "COMPLETE"
            self.state.save(record)
            return record["orders"]
        except Exception as exc:
            unsafe = any(order.get("status") in {"SUBMITTING", "PENDING"} for order in record["orders"])
            first_filled_without_second = (
                initial.second_leg_requires_recalculation
                and len(record["orders"]) == 1
                and Decimal(str((record["orders"][0].get("execution") or {}).get("filledQuantity", "0"))) > 0
            )
            record["status"] = "UNRESOLVED" if unsafe or first_filled_without_second else "FAILED"
            record["error"] = str(exc)
            self.state.save(record)
            raise

    def _execute_leg(self, leg: OrderLeg, cycle_id: str, record: dict) -> dict:
        amount = Decimal(leg.quantity) * leg.limit_price
        if amount > self.settings.max_order_amount_usd:
            raise RuntimeError(f"Order amount ${amount} exceeds MAX_ORDER_AMOUNT_USD.")
        if leg.quantity <= 0:
            raise RuntimeError("Refusing to submit a zero-quantity order.")
        client_id = f"va-{cycle_id}-l{leg.sequence}-{leg.side.lower()}"[:36]
        item = {"leg": leg.sequence, "client_order_id": client_id, "symbol": leg.symbol,
                "side": leg.side, "quantity": leg.quantity, "price": str(leg.limit_price),
                "status": "SUBMITTING"}
        record["orders"].append(item)
        record["status"] = f"LEG_{leg.sequence}_SUBMITTING"
        self.state.save(record)
        result = self.orders.create_limit_order(
            self.account_seq, client_order_id=client_id, symbol=leg.symbol,
            side=leg.side, quantity=leg.quantity, price=str(leg.limit_price),
        )
        order_id = result.get("orderId")
        if not order_id:
            raise RuntimeError("Order response did not contain orderId.")
        item.update(order_id=order_id, status="PENDING")
        record["status"] = f"LEG_{leg.sequence}_PENDING"
        self.state.save(record)
        detail = self._wait_until_resolved(order_id)
        item["status"] = detail.get("status")
        item["execution"] = detail.get("execution", {})
        self.state.save(record)
        if detail.get("status") == "REJECTED":
            raise RuntimeError(f"Order {order_id} was rejected.")
        return detail

    def _wait_until_resolved(self, order_id: str) -> dict:
        deadline = self.monotonic() + self.settings.order_fill_timeout_seconds
        while True:
            detail = self.orders.order_detail(self.account_seq, order_id)
            if detail.get("status") in TERMINAL:
                return detail
            if self.monotonic() >= deadline:
                self.orders.cancel_order(self.account_seq, order_id)
                return self._wait_for_terminal_after_cancel(order_id)
            self.sleep(self.settings.order_poll_interval_seconds)

    def _wait_for_terminal_after_cancel(self, order_id: str) -> dict:
        deadline = self.monotonic() + self.settings.order_fill_timeout_seconds
        while self.monotonic() < deadline:
            detail = self.orders.order_detail(self.account_seq, order_id)
            if detail.get("status") in TERMINAL:
                return detail
            self.sleep(self.settings.order_poll_interval_seconds)
        raise RuntimeError(f"Order {order_id} state is unresolved after cancellation; new orders are blocked.")

    def _recalculate_second(self, initial: RebalancePlan, first: dict) -> OrderLeg | None:
        execution = first.get("execution") or {}
        filled = Decimal(str(execution.get("filledQuantity", "0")))
        if filled <= 0:
            return None
        if initial.decision.value == "BUY":
            refreshed = self.refresh_plan()
            leg = refreshed.first_leg
            if leg is None:
                return None
            if not (leg.symbol == "TQQQ" and leg.side == "BUY"):
                raise RuntimeError("After SGOV fill, a direct TQQQ buy is not safely fundable; stopped.")
            return OrderLeg(2, leg.side, leg.symbol, leg.quantity, leg.limit_price, "recalculated after SGOV fill")

        filled_amount = Decimal(str(execution.get("filledAmount") or "0"))
        commission = Decimal(str(execution.get("commission") or "0"))
        tax = Decimal(str(execution.get("tax") or "0"))
        net = max(filled_amount - commission - tax, Decimal("0"))
        _, ask = self.reserve_quote()
        spendable = min(net, self.buying_power())
        quantity = int((spendable / ask).to_integral_value(rounding=ROUND_FLOOR)) if ask > 0 else 0
        return OrderLeg(2, "BUY", "SGOV", quantity, ask, "invest actual TQQQ net proceeds") if quantity else None
