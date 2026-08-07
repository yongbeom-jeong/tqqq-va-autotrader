from __future__ import annotations

from typing import Any

from toss.client import TossClient


class AccountAPI:
    def __init__(self, client: TossClient):
        self.client = client

    def accounts(self) -> list[dict[str, Any]]:
        return self.client.get("/api/v1/accounts")

    def holdings(self, account_seq: str, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol} if symbol else None
        return self.client.get("/api/v1/holdings", account_seq=account_seq, params=params)

    def buying_power(self, account_seq: str, currency: str = "USD") -> dict[str, Any]:
        return self.client.get("/api/v1/buying-power", account_seq=account_seq, params={"currency": currency})

    def sellable_quantity(self, account_seq: str, symbol: str) -> dict[str, Any]:
        return self.client.get(
            "/api/v1/sellable-quantity", account_seq=account_seq, params={"symbol": symbol}
        )
