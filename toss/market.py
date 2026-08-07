from __future__ import annotations

from datetime import datetime
from typing import Any

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

    def us_calendar(self) -> dict[str, Any]:
        return self.client.get("/api/v1/market-calendar/US")


def current_us_session(calendar: dict[str, Any], now: datetime | None = None) -> str:
    today = calendar.get("today", {})
    now = now or datetime.now().astimezone()
    for key, label in (("dayMarket", "DAY"), ("preMarket", "PRE"),
                       ("regularMarket", "REGULAR"), ("afterMarket", "AFTER")):
        session = today.get(key)
        if not session:
            continue
        start = datetime.fromisoformat(session["startTime"])
        end = datetime.fromisoformat(session["endTime"])
        if start <= now.astimezone(start.tzinfo) < end:
            return label
    return "CLOSED"

