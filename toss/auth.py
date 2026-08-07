from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class AuthManager:
    client_id: str
    client_secret: str
    _access_token: str | None = None
    _expires_at: float = 0.0

    def get_token(self, client: Any) -> str:
        if self._access_token and time.time() < self._expires_at - 60:
            return self._access_token
        payload = client.request_public("POST", "/oauth2/token", data={
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        })
        token = payload.get("access_token")
        if not token:
            raise ValueError("Token response did not contain access_token.")
        self._access_token = str(token)
        self._expires_at = time.time() + int(payload.get("expires_in", 86400))
        return self._access_token

