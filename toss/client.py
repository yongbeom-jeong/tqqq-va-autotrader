from __future__ import annotations

import time
from email.utils import parsedate_to_datetime
from typing import Any

import requests

from config import Settings
from toss.auth import AuthManager


class TossAPIError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class TossClient:
    """HTTP client containing only the read-only surface used by status."""

    def __init__(self, settings: Settings, session: requests.Session | None = None):
        self.settings = settings
        self.session = session or requests.Session()
        self.auth = AuthManager(settings.client_id, settings.client_secret)

    def request_public(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        return self._request(method, path, authenticated=False, **kwargs)

    def get(self, path: str, *, account_seq: str | None = None, params: dict[str, Any] | None = None) -> Any:
        headers = {"X-Tossinvest-Account": str(account_seq)} if account_seq is not None else {}
        payload = self._request("GET", path, headers=headers, params=params)
        if "result" not in payload:
            raise TossAPIError("Toss response did not contain the expected result field.")
        return payload["result"]

    def post(
        self, path: str, *, account_seq: str, json: dict[str, Any]
    ) -> Any:
        payload = self._request(
            "POST", path, headers={"X-Tossinvest-Account": str(account_seq)}, json=json
        )
        if "result" not in payload:
            raise TossAPIError("Toss response did not contain the expected result field.")
        return payload["result"]

    def _request(self, method: str, path: str, *, authenticated: bool = True, **kwargs: Any) -> dict[str, Any]:
        headers = dict(kwargs.pop("headers", {}))
        if authenticated:
            headers["Authorization"] = f"Bearer {self.auth.get_token(self)}"
        headers.setdefault("Accept", "application/json")
        response: requests.Response | None = None
        for attempt in range(self.settings.max_retries + 1):
            try:
                response = self.session.request(method, f"{self.settings.base_url}{path}", headers=headers,
                                                timeout=self.settings.timeout_seconds, **kwargs)
            except requests.RequestException as exc:
                if attempt >= self.settings.max_retries:
                    raise TossAPIError(f"Network error while calling Toss API: {exc}") from exc
                time.sleep(min(2**attempt, 4))
                continue
            if response.status_code != 429 or attempt >= self.settings.max_retries:
                break
            time.sleep(self._retry_delay(response, attempt))
        assert response is not None
        if not response.ok:
            raise self._error_from_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise TossAPIError("Toss API returned a non-JSON response.", status_code=response.status_code) from exc
        if not isinstance(payload, dict):
            raise TossAPIError("Toss API returned an unexpected JSON response.")
        return payload

    @staticmethod
    def _retry_delay(response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        if value:
            try:
                return max(0.0, float(value))
            except ValueError:
                try:
                    return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
                except (TypeError, ValueError):
                    pass
        return min(2**attempt, 4)

    @staticmethod
    def _error_from_response(response: requests.Response) -> TossAPIError:
        code = None
        message = response.reason or "Unknown error"
        request_id = None
        try:
            payload = response.json()
            # OAuth uses flat {error, error_description}; other APIs use
            # {error: {code, message, requestId}}.
            nested = payload.get("error") if isinstance(payload, dict) else None
            error = nested if isinstance(nested, dict) else payload
            if isinstance(error, dict):
                code = error.get("code") or error.get("error")
                message = error.get("message") or error.get("error_description") or message
                request_id = error.get("requestId")
        except ValueError:
            pass
        details = f"Toss API error {response.status_code}"
        if code:
            details += f" [{code}]"
        details += f": {message}"
        if request_id:
            details += f" (requestId: {request_id})"
        return TossAPIError(details, status_code=response.status_code, code=code)
