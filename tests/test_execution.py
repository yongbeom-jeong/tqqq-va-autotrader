from decimal import Decimal

import pytest

from config import Settings
from execution.engine import ExecutionEngine
from persistence.state import StateStore
from va.calculator import Decision, OrderLeg, RebalancePlan


D = Decimal


def settings(tmp_path, **values):
    base = dict(
        client_id="c", client_secret="s", account_seq="1", dry_run=False,
        execution_state_file=tmp_path / "state.json", max_order_amount_usd=D("5000"),
        order_fill_timeout_seconds=1, order_poll_interval_seconds=.01,
    )
    base.update(values)
    return Settings(**base)


class FakeOrders:
    def __init__(self, details):
        self.details = iter(details)
        self.created = []
        self.canceled = []

    def create_limit_order(self, account_seq, **payload):
        self.created.append(payload)
        return {"orderId": f"order-{len(self.created)}", "clientOrderId": payload["client_order_id"]}

    def order_detail(self, account_seq, order_id):
        return next(self.details)

    def cancel_order(self, account_seq, order_id):
        self.canceled.append(order_id)
        return {"orderId": "cancel-1"}


def filled(symbol, side, quantity, amount):
    return {"status": "FILLED", "symbol": symbol, "side": side,
            "execution": {"filledQuantity": str(quantity), "filledAmount": str(amount),
                          "commission": "0.30", "tax": "0"}}


def test_sell_tqqq_then_buy_sgov_sequentially_from_actual_net_proceeds(tmp_path):
    first = OrderLeg(1, "SELL", "TQQQ", 4, D("73.74"), "enter band")
    estimated = OrderLeg(2, "BUY", "SGOV", 2, D("100.51"), "estimate")
    plan = RebalancePlan(Decision.SELL, 4, 4, first, estimated, True)
    orders = FakeOrders([filled("TQQQ", "SELL", 4, "294.96"), filled("SGOV", "BUY", 2, "201.02")])
    engine = ExecutionEngine(
        settings(tmp_path), orders, "1", StateStore(tmp_path / "state.json"),
        lambda: plan, lambda: (D("100.50"), D("100.51")), lambda: D("500"),
        sleep=lambda _: None,
    )
    result = engine.run(plan)
    assert [(x["side"], x["symbol"], x["quantity"]) for x in orders.created] == [
        ("SELL", "TQQQ", 4), ("BUY", "SGOV", 2)
    ]
    assert [x["status"] for x in result] == ["FILLED", "FILLED"]
    assert StateStore(tmp_path / "state.json").load()["status"] == "COMPLETE"


def test_buy_sgov_funding_leg_then_uses_refreshed_tqqq_plan(tmp_path):
    first = OrderLeg(1, "SELL", "SGOV", 3, D("100.50"), "fund")
    estimate = OrderLeg(2, "BUY", "TQQQ", 4, D("73.76"), "estimate")
    initial = RebalancePlan(Decision.BUY, 4, 4, first, estimate, True)
    refreshed_leg = OrderLeg(1, "BUY", "TQQQ", 3, D("73.80"), "refreshed")
    refreshed = RebalancePlan(Decision.BUY, 3, 3, refreshed_leg, None, False)
    orders = FakeOrders([filled("SGOV", "SELL", 3, "301.50"), filled("TQQQ", "BUY", 3, "221.40")])
    engine = ExecutionEngine(
        settings(tmp_path), orders, "1", StateStore(tmp_path / "state.json"),
        lambda: refreshed, lambda: (D("100.50"), D("100.51")), lambda: D("400"),
        sleep=lambda _: None,
    )
    engine.run(initial)
    assert orders.created[1]["quantity"] == 3
    assert orders.created[1]["price"] == "73.80"


def test_timeout_cancels_and_resolves_before_returning(tmp_path):
    leg = OrderLeg(1, "SELL", "TQQQ", 1, D("73.74"), "enter band")
    plan = RebalancePlan(Decision.SELL, 1, 1, leg, None, False)
    orders = FakeOrders([
        {"status": "PENDING", "execution": {"filledQuantity": "0"}},
        {"status": "CANCELED", "execution": {"filledQuantity": "0"}},
    ])
    ticks = iter([0, 2, 3, 3.1])
    engine = ExecutionEngine(
        settings(tmp_path), orders, "1", StateStore(tmp_path / "state.json"),
        lambda: plan, lambda: (D("100"), D("101")), lambda: D("100"),
        sleep=lambda _: None, monotonic=lambda: next(ticks),
    )
    engine.run(plan)
    assert orders.canceled == ["order-1"]


def test_order_amount_limit_fails_before_submission(tmp_path):
    leg = OrderLeg(1, "BUY", "TQQQ", 100, D("74"), "too large")
    plan = RebalancePlan(Decision.BUY, 100, 100, leg, None, False)
    orders = FakeOrders([])
    engine = ExecutionEngine(
        settings(tmp_path, max_order_amount_usd=D("1000")), orders, "1",
        StateStore(tmp_path / "state.json"), lambda: plan,
        lambda: (D("100"), D("101")), lambda: D("10000"), sleep=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="exceeds"):
        engine.run(plan)
    assert orders.created == []


def test_existing_lock_blocks_duplicate_process(tmp_path):
    leg = OrderLeg(1, "BUY", "TQQQ", 1, D("74"), "buy")
    plan = RebalancePlan(Decision.BUY, 1, 1, leg, None, False)
    state = StateStore(tmp_path / "state.json")
    lock = (tmp_path / "state.json.lock")
    lock.write_text("other-pid", encoding="ascii")
    engine = ExecutionEngine(
        settings(tmp_path), FakeOrders([]), "1", state, lambda: plan,
        lambda: (D("100"), D("101")), lambda: D("100"), sleep=lambda _: None,
    )
    with pytest.raises(RuntimeError, match="Another execution"):
        engine.run(plan)
