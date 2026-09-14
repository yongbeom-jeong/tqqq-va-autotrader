from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from toss.client import TossClient


class MarketAPI:
    def __init__(self, client: TossClient):
        self.client = client

    def price(self, symbol: str) -> dict[str, Any]:
        result = self.client.get("/api/v1/prices", params={"symbols": symbol})
        matches = [item for item in result if item.get("symbol", "").upper() == symbol.upper()]
        if not matches:
            raise ValueError(f"Price response did not contain {symbol}.")
        return matches[0]

    def orderbook(self, symbol: str) -> dict[str, Any]:
        return self.client.get("/api/v1/orderbook", params={"symbol": symbol})

    def us_calendar(self, market_date: date | None = None) -> dict[str, Any]:
        # The API's `date` parameter is a US-local calendar date. Around KST
        # midnight, the same trading session still belongs to the previous US
        # date, so using the Korean date incorrectly selects a weekend/holiday.
        market_date = market_date or datetime.now(ZoneInfo("America/New_York")).date()
        return self.client.get(
            "/api/v1/market-calendar/US", params={"date": market_date.isoformat()}
        )


def current_us_session(calendar: dict[str, Any], now: datetime | None = None) -> str:
    now = now or datetime.now().astimezone()
    # A Korean daytime session can belong to the next US business date.
    # Check all returned days using absolute timestamps, including overnight
    # sessions belonging to the previous business date.
    for day_key in ("today", "previousBusinessDay", "nextBusinessDay"):
        day = calendar.get(day_key) or {}
        for key, label in (("dayMarket", "DAY"), ("preMarket", "PRE"),
                           ("regularMarket", "REGULAR"), ("afterMarket", "AFTER")):
            session = day.get(key)
            if not session:
                continue
            start = datetime.fromisoformat(session["startTime"])
            end = datetime.fromisoformat(session["endTime"])
            if start <= now.astimezone(start.tzinfo) < end:
                return label
    return "CLOSED"
