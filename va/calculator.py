from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from enum import Enum

from va.path import PathEntry


ZERO = Decimal("0")


class Decision(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


@dataclass(frozen=True)
class OrderLeg:
    sequence: int
    side: str
    symbol: str
    quantity: int
    limit_price: Decimal
    purpose: str


@dataclass(frozen=True)
class RebalancePlan:
    decision: Decision
    required_tqqq_quantity: int
    executable_tqqq_quantity: int
    first_leg: OrderLeg | None
    second_leg: OrderLeg | None
    second_leg_requires_recalculation: bool
    constrained: bool = False
    reason: str | None = None


def _ceil_shares(amount: Decimal, price: Decimal) -> int:
    if amount <= ZERO:
        return 0
    if price <= ZERO:
        raise ValueError("Price must be positive.")
    return int((amount / price).to_integral_value(rounding=ROUND_CEILING))


def _floor_shares(amount: Decimal, price: Decimal) -> int:
    if amount <= ZERO:
        return 0
    if price <= ZERO:
        raise ValueError("Price must be positive.")
    return int((amount / price).to_integral_value(rounding=ROUND_FLOOR))


def build_rebalance_plan(
    *, path: PathEntry, current_value: Decimal, tqqq_last: Decimal,
    tqqq_bid: Decimal, tqqq_ask: Decimal, sgov_bid: Decimal, sgov_ask: Decimal,
    usd_buying_power: Decimal, tqqq_sellable: Decimal, sgov_sellable: Decimal,
    tqqq_symbol: str = "TQQQ", reserve_symbol: str = "SGOV",
) -> RebalancePlan:
    prices = (tqqq_last, tqqq_bid, tqqq_ask, sgov_bid, sgov_ask)
    if any(price <= ZERO for price in prices):
        raise ValueError("All market prices must be positive.")

    if current_value < path.lower_band:
        required = _ceil_shares(path.lower_band - current_value, tqqq_last)
        estimated_cost = Decimal(required) * tqqq_ask
        shortage = max(estimated_cost - usd_buying_power, ZERO)
        if shortage == ZERO:
            leg = OrderLeg(1, "BUY", tqqq_symbol, required, tqqq_ask, "enter lower band")
            return RebalancePlan(Decision.BUY, required, required, leg, None, False)

        required_sgov = _ceil_shares(shortage, sgov_bid)
        sellable_sgov = max(0, int(sgov_sellable.to_integral_value(rounding=ROUND_FLOOR)))
        sgov_to_sell = min(required_sgov, sellable_sgov)
        constrained = sgov_to_sell < required_sgov
        if constrained:
            return RebalancePlan(
                Decision.BUY, required, 0, None, None, False, True,
                "Insufficient sellable SGOV to fund entry into the TQQQ band.",
            )
        first = OrderLeg(1, "SELL", reserve_symbol, sgov_to_sell, sgov_bid, "fund TQQQ purchase")
        second = OrderLeg(2, "BUY", tqqq_symbol, required, tqqq_ask, "recalculate after SGOV fill")
        return RebalancePlan(Decision.BUY, required, required, first, second, True)

    if current_value > path.upper_band:
        required = _ceil_shares(current_value - path.upper_band, tqqq_last)
        sellable_tqqq = max(0, int(tqqq_sellable.to_integral_value(rounding=ROUND_FLOOR)))
        executable = min(required, sellable_tqqq)
        constrained = executable < required
        if constrained:
            return RebalancePlan(
                Decision.SELL, required, 0, None, None, False, True,
                "Insufficient sellable TQQQ to enter the band.",
            )
        proceeds = Decimal(executable) * tqqq_bid
        estimated_sgov = _floor_shares(proceeds, sgov_ask)
        first = OrderLeg(1, "SELL", tqqq_symbol, executable, tqqq_bid, "enter upper band")
        second = (
            OrderLeg(2, "BUY", reserve_symbol, estimated_sgov, sgov_ask, "recalculate after TQQQ fill")
            if estimated_sgov > 0 else None
        )
        return RebalancePlan(Decision.SELL, required, executable, first, second, second is not None)

    return RebalancePlan(Decision.HOLD, 0, 0, None, None, False)
