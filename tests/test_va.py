from datetime import date
from decimal import Decimal

import pytest

from va.calculator import Decision, build_rebalance_plan
from va.path import PathEntry, VAPath


D = Decimal
PATH = PathEntry(date(2026, 8, 7), D("5855.07"), D("5269.56"), D("6505.63"))


def plan(current, **overrides):
    values = dict(
        path=PATH,
        current_value=D(current),
        tqqq_last=D("73.73"),
        tqqq_bid=D("73.74"),
        tqqq_ask=D("73.76"),
        sgov_bid=D("100.50"),
        sgov_ask=D("100.51"),
        usd_buying_power=D("54.24"),
        tqqq_sellable=D("85"),
        sgov_sellable=D("45"),
    )
    values.update(overrides)
    return build_rebalance_plan(**values)


def test_hold_includes_both_band_boundaries():
    assert plan("5269.56").decision is Decision.HOLD
    assert plan("6267.05").decision is Decision.HOLD
    assert plan("6505.63").decision is Decision.HOLD


def test_buy_uses_ceiling_and_sells_sgov_first_when_cash_is_short():
    result = plan("5000")
    assert result.decision is Decision.BUY
    assert result.required_tqqq_quantity == 4
    assert (result.first_leg.side, result.first_leg.symbol, result.first_leg.quantity) == ("SELL", "SGOV", 3)
    assert (result.second_leg.side, result.second_leg.symbol, result.second_leg.quantity) == ("BUY", "TQQQ", 4)
    assert result.second_leg_requires_recalculation


def test_buy_goes_directly_to_tqqq_when_cash_is_sufficient():
    result = plan("5000", usd_buying_power=D("1000"))
    assert result.first_leg.symbol == "TQQQ"
    assert result.first_leg.quantity == 4
    assert result.second_leg is None


def test_insufficient_sgov_fails_closed_without_order_legs():
    result = plan("5000", sgov_sellable=D("1"))
    assert result.constrained
    assert result.executable_tqqq_quantity == 0
    assert result.first_leg is None and result.second_leg is None


def test_sell_uses_ceiling_then_estimates_whole_sgov_shares():
    result = plan("6800")
    assert result.decision is Decision.SELL
    assert result.required_tqqq_quantity == 4
    assert (result.first_leg.side, result.first_leg.symbol, result.first_leg.quantity) == ("SELL", "TQQQ", 4)
    assert (result.second_leg.side, result.second_leg.symbol, result.second_leg.quantity) == ("BUY", "SGOV", 2)


def test_path_csv_rejects_duplicate_dates(tmp_path):
    csv_file = tmp_path / "path.csv"
    csv_file.write_text(
        "date,target_value,lower_band,upper_band\n"
        "2026-08-07,100,90,112\n"
        "2026-08-07,101,91,113\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Duplicate"):
        VAPath.from_csv(csv_file)


def test_path_requires_exact_date(tmp_path):
    csv_file = tmp_path / "path.csv"
    csv_file.write_text(
        "date,target_value,lower_band,upper_band\n2026-08-07,100,90,112\n",
        encoding="utf-8",
    )
    path = VAPath.from_csv(csv_file)
    assert path.for_date(date(2026, 8, 7)).target_value == D("100")
    with pytest.raises(ValueError, match="No VA path entry"):
        path.for_date(date(2026, 8, 8))
