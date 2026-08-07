from __future__ import annotations

from typing import Any

from toss.client import TossClient


class OrdersAPI:
    def __init__(self, client: TossClient):
        self.client = client

    def open_orders(self, account_seq: str, symbol: str | None = None) -> list[dict[str, Any]]:
        params = {"status": "OPEN"}
        if symbol:
            params["symbol"] = symbol
        result = self.client.get("/api/v1/orders", account_seq=account_seq, params=params)
        return result.get("orders", [])

    def create_limit_order(
        self, account_seq: str, *, client_order_id: str, symbol: str,
        side: str, quantity: int, price: str,
    ) -> dict[str, Any]:
        if quantity <= 0:
            raise ValueError("Order quantity must be positive.")
        return self.client.post("/api/v1/orders", account_seq=account_seq, json={
            "clientOrderId": client_order_id,
            "symbol": symbol,
            "side": side,
            "orderType": "LIMIT",
            "timeInForce": "DAY",
            "quantity": str(quantity),
            "price": price,
        })

    def order_detail(self, account_seq: str, order_id: str) -> dict[str, Any]:
        return self.client.get(f"/api/v1/orders/{order_id}", account_seq=account_seq)

    def cancel_order(self, account_seq: str, order_id: str) -> dict[str, Any]:
        return self.client.post(
            f"/api/v1/orders/{order_id}/cancel", account_seq=account_seq, json={}
        )
