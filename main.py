from __future__ import annotations

import argparse
import sys
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

from config import ConfigurationError, Settings
from execution.engine import ExecutionEngine
from persistence.state import StateStore
from toss.account import AccountAPI
from toss.client import TossAPIError, TossClient
from toss.market import MarketAPI, current_us_session
from toss.orders import OrdersAPI
from va.calculator import Decision, OrderLeg, build_rebalance_plan
from va.path import VAPath


def choose_account(accounts: list[dict[str, Any]], configured: str | None) -> str:
    if not accounts:
        raise RuntimeError("No active Toss Securities account was returned.")
    by_seq = {str(account["accountSeq"]): account for account in accounts}
    if configured is not None:
        if configured not in by_seq:
            raise RuntimeError(f"Configured TOSS_ACCOUNT_SEQ={configured} was not found.")
        return configured
    brokerage = next((a for a in accounts if a.get("accountType") == "BROKERAGE"), accounts[0])
    return str(brokerage["accountSeq"])


def money(value: Any, currency: str = "USD") -> str:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        return "N/A"
    symbol = "$" if currency == "USD" else f"{currency} "
    return f"{symbol}{amount:,.2f}"


def build_status(settings: Settings, client: TossClient) -> str:
    market, account_api, orders_api = MarketAPI(client), AccountAPI(client), OrdersAPI(client)
    accounts = account_api.accounts()
    account_seq = choose_account(accounts, settings.account_seq)
    calendar = market.us_calendar()
    price = market.price(settings.symbol)
    orderbook = market.orderbook(settings.symbol)
    holdings = account_api.holdings(account_seq, settings.symbol)
    buying_power = account_api.buying_power(account_seq)
    orders = orders_api.open_orders(account_seq, settings.symbol)
    items = holdings.get("items", [])
    holding = next((item for item in items if item.get("symbol", "").upper() == settings.symbol), None)
    asks, bids = orderbook.get("asks") or [], orderbook.get("bids") or []
    currency = price.get("currency", "USD")
    quantity = holding.get("quantity", "0") if holding else "0"
    market_value = holding.get("marketValue", {}).get("amount", "0") if holding else "0"
    lines = [
        "Toss authentication: OK", f"Account: loaded (seq={account_seq})", "", "US Market",
        f"Current session: {current_us_session(calendar)}", "", settings.symbol,
        f"Price: {money(price.get('lastPrice'), currency)}",
        f"Best bid: {money(bids[0].get('price'), currency) if bids else 'N/A'}",
        f"Best ask: {money(asks[0].get('price'), currency) if asks else 'N/A'}",
        f"Quantity: {quantity}", f"Market Value: {money(market_value, currency)}", "",
        "USD Buying Power", money(buying_power.get("cashBuyingPower"), "USD"), "",
        "Open TQQQ Orders",
    ]
    if not orders:
        lines.append("None")
    else:
        for order in orders:
            price_text = money(order.get("price"), order.get("currency", "USD")) if order.get("price") else "MARKET"
            lines.append(f"{order.get('side')} {order.get('quantity')} @ {price_text} "
                         f"[{order.get('status')}] id={order.get('orderId')}")
    return "\n".join(lines)


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError) as exc:
        raise ValueError(f"Invalid decimal value for {label}.") from exc
    return result


def _holding(holdings: dict[str, Any], symbol: str) -> dict[str, Any] | None:
    return next(
        (item for item in holdings.get("items", []) if item.get("symbol", "").upper() == symbol),
        None,
    )


def _best_price(orderbook: dict[str, Any], side: str, symbol: str) -> Decimal:
    levels = orderbook.get("asks" if side == "ask" else "bids") or []
    if not levels:
        raise ValueError(f"{symbol} best {side} is unavailable; dry-run stopped.")
    return _decimal(levels[0].get("price"), f"{symbol} best {side}")


def _format_leg(leg: OrderLeg, *, deferred: bool = False) -> list[str]:
    title = f"Leg {leg.sequence}{' (estimated; recalculate after fill)' if deferred else ''}"
    return [
        title,
        f"{leg.side} {leg.quantity} {leg.symbol} @ {money(leg.limit_price)} LIMIT",
        f"Purpose: {leg.purpose}",
    ]


def build_dry_run(
    settings: Settings, client: TossClient, *, run_date=None
) -> str:
    market, account_api, orders_api = MarketAPI(client), AccountAPI(client), OrdersAPI(client)
    account_seq = choose_account(account_api.accounts(), settings.account_seq)
    calendar = market.us_calendar()
    today = run_date or datetime.now(ZoneInfo("Asia/Seoul")).date()
    path = VAPath.from_csv(settings.va_path_file).for_date(today)

    open_orders = orders_api.open_orders(account_seq)
    relevant_orders = [
        order for order in open_orders
        if order.get("symbol", "").upper() in {settings.symbol, settings.reserve_symbol}
    ]
    if relevant_orders:
        symbols = ", ".join(sorted({order.get("symbol", "?") for order in relevant_orders}))
        raise RuntimeError(f"Open TQQQ/SGOV order exists ({symbols}); dry-run stopped.")

    holdings = account_api.holdings(account_seq)
    tqqq_holding = _holding(holdings, settings.symbol)
    sgov_holding = _holding(holdings, settings.reserve_symbol)
    tqqq_price = market.price(settings.symbol)
    sgov_price = market.price(settings.reserve_symbol)
    tqqq_book = market.orderbook(settings.symbol)
    sgov_book = market.orderbook(settings.reserve_symbol)
    buying_power = account_api.buying_power(account_seq)
    tqqq_sellable = account_api.sellable_quantity(account_seq, settings.symbol)
    sgov_sellable = account_api.sellable_quantity(account_seq, settings.reserve_symbol)

    tqqq_last = _decimal(tqqq_price.get("lastPrice"), "TQQQ last price")
    sgov_last = _decimal(sgov_price.get("lastPrice"), "SGOV last price")
    tqqq_quantity = _decimal(tqqq_holding.get("quantity", "0") if tqqq_holding else "0", "TQQQ quantity")
    sgov_quantity = _decimal(sgov_holding.get("quantity", "0") if sgov_holding else "0", "SGOV quantity")
    # Keep the strategy independent of aggregate account value: only TQQQ is
    # compared with the VA band, while SGOV is the reserve asset.
    current_value = tqqq_quantity * tqqq_last
    plan = build_rebalance_plan(
        path=path,
        current_value=current_value,
        tqqq_last=tqqq_last,
        tqqq_bid=_best_price(tqqq_book, "bid", settings.symbol),
        tqqq_ask=_best_price(tqqq_book, "ask", settings.symbol),
        sgov_bid=_best_price(sgov_book, "bid", settings.reserve_symbol),
        sgov_ask=_best_price(sgov_book, "ask", settings.reserve_symbol),
        usd_buying_power=_decimal(buying_power.get("cashBuyingPower"), "USD buying power"),
        tqqq_sellable=_decimal(tqqq_sellable.get("sellableQuantity"), "TQQQ sellable quantity"),
        sgov_sellable=_decimal(sgov_sellable.get("sellableQuantity"), "SGOV sellable quantity"),
        tqqq_symbol=settings.symbol,
        reserve_symbol=settings.reserve_symbol,
    )

    lines = [
        "DRY RUN - NO ORDERS WILL BE SUBMITTED",
        f"Date (Asia/Seoul): {today.isoformat()}",
        f"Account: seq={account_seq}",
        f"US market session: {current_us_session(calendar)}",
        "",
        "VA Path",
        f"Lower band: {money(path.lower_band)}",
        f"Target value: {money(path.target_value)}",
        f"Upper band: {money(path.upper_band)}",
        "",
        "Portfolio",
        f"TQQQ: {tqqq_quantity} shares @ {money(tqqq_last)} = {money(current_value)}",
        f"SGOV: {sgov_quantity} shares @ {money(sgov_last)}",
        f"USD buying power: {money(buying_power.get('cashBuyingPower'))}",
        "",
        f"Decision: {plan.decision.value}",
    ]
    if plan.decision is Decision.HOLD:
        lines.append("TQQQ value is inside the configured band.")
        return "\n".join(lines)

    lines.extend([
        f"TQQQ shares required to enter band: {plan.required_tqqq_quantity}",
        f"TQQQ shares executable now: {plan.executable_tqqq_quantity}",
        "",
    ])
    if plan.first_leg:
        lines.extend(_format_leg(plan.first_leg))
    if plan.second_leg:
        lines.append("")
        lines.extend(_format_leg(plan.second_leg, deferred=plan.second_leg_requires_recalculation))
    if plan.constrained:
        lines.extend(["", f"WARNING: {plan.reason}"])
    lines.extend([
        "",
        "Safety: Leg 2 must not be submitted until Leg 1 is fully resolved,",
        "then holdings, buying power, prices, and the VA decision must be recalculated.",
    ])
    return "\n".join(lines)


def collect_rebalance_plan(settings: Settings, client: TossClient, account_seq: str):
    market, account_api = MarketAPI(client), AccountAPI(client)
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    path = VAPath.from_csv(settings.va_path_file).for_date(today)
    holdings = account_api.holdings(account_seq)
    tqqq_holding = _holding(holdings, settings.symbol)
    tqqq_price = market.price(settings.symbol)
    tqqq_book = market.orderbook(settings.symbol)
    sgov_book = market.orderbook(settings.reserve_symbol)
    tqqq_last = _decimal(tqqq_price.get("lastPrice"), "TQQQ last price")
    tqqq_quantity = _decimal(tqqq_holding.get("quantity", "0") if tqqq_holding else "0", "TQQQ quantity")
    return build_rebalance_plan(
        path=path, current_value=tqqq_quantity * tqqq_last, tqqq_last=tqqq_last,
        tqqq_bid=_best_price(tqqq_book, "bid", settings.symbol),
        tqqq_ask=_best_price(tqqq_book, "ask", settings.symbol),
        sgov_bid=_best_price(sgov_book, "bid", settings.reserve_symbol),
        sgov_ask=_best_price(sgov_book, "ask", settings.reserve_symbol),
        usd_buying_power=_decimal(account_api.buying_power(account_seq).get("cashBuyingPower"), "USD buying power"),
        tqqq_sellable=_decimal(account_api.sellable_quantity(account_seq, settings.symbol).get("sellableQuantity"), "TQQQ sellable"),
        sgov_sellable=_decimal(account_api.sellable_quantity(account_seq, settings.reserve_symbol).get("sellableQuantity"), "SGOV sellable"),
        tqqq_symbol=settings.symbol, reserve_symbol=settings.reserve_symbol,
    )


def run_live(settings: Settings, client: TossClient) -> str:
    if settings.dry_run:
        raise ConfigurationError("Set DRY_RUN=false before using live execution.")
    account_api, market, orders_api = AccountAPI(client), MarketAPI(client), OrdersAPI(client)
    account_seq = choose_account(account_api.accounts(), settings.account_seq)
    session = current_us_session(market.us_calendar())
    if session == "CLOSED":
        return "LIVE RUN\nUS market session: CLOSED\nNo orders submitted."
    relevant = [o for o in orders_api.open_orders(account_seq)
                if o.get("symbol", "").upper() in {settings.symbol, settings.reserve_symbol}]
    if relevant:
        raise RuntimeError("An open TQQQ/SGOV order already exists; live execution stopped.")
    initial = collect_rebalance_plan(settings, client, account_seq)
    if initial.constrained:
        raise RuntimeError(initial.reason or "Rebalance plan is constrained.")
    if initial.decision is Decision.HOLD:
        return "LIVE RUN\nDecision: HOLD\nNo orders submitted."

    def reserve_quote():
        book = market.orderbook(settings.reserve_symbol)
        return (_best_price(book, "bid", settings.reserve_symbol),
                _best_price(book, "ask", settings.reserve_symbol))

    engine = ExecutionEngine(
        settings, orders_api, account_seq, StateStore(settings.execution_state_file),
        lambda: collect_rebalance_plan(settings, client, account_seq), reserve_quote,
        lambda: _decimal(account_api.buying_power(account_seq).get("cashBuyingPower"), "USD buying power"),
    )
    results = engine.run(initial)
    lines = ["LIVE RUN COMPLETE", f"Initial decision: {initial.decision.value}"]
    for item in results:
        execution = item.get("execution") or {}
        lines.append(
            f"Leg {item['leg']}: {item['side']} {item['symbol']} {item['quantity']} "
            f"status={item['status']} filled={execution.get('filledQuantity', '0')} orderId={item.get('order_id')}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TQQQ Value Averaging AutoTrader")
    parser.add_argument("command", choices=["status", "dry-run", "run"])
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        if args.command == "status":
            print(build_status(settings, TossClient(settings)))
        elif args.command == "dry-run":
            if not settings.dry_run:
                raise ConfigurationError("DRY_RUN must be true to use the dry-run command.")
            print(build_dry_run(settings, TossClient(settings)))
        elif args.command == "run":
            print(run_live(settings, TossClient(settings)))
        return 0
    except (ConfigurationError, TossAPIError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
