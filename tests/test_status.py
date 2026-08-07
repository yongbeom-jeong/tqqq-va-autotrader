from datetime import datetime, timezone

import pytest

from config import Settings
from main import build_status, choose_account, run_live
from toss.client import TossAPIError, TossClient
from toss.market import current_us_session


class FakeResponse:
    def __init__(self, status, payload, headers=None):
        self.status_code, self._payload = status, payload
        self.headers, self.reason = headers or {}, "error"
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.calls = iter(responses), []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


def settings(**overrides):
    values = dict(client_id="client", client_secret="secret", account_seq=None, max_retries=0)
    values.update(overrides)
    return Settings(**values)


def test_choose_account_prefers_brokerage_and_validates_configured_value():
    accounts = [{"accountSeq": 2, "accountType": "OTHER"}, {"accountSeq": 7, "accountType": "BROKERAGE"}]
    assert choose_account(accounts, None) == "7"
    assert choose_account(accounts, "2") == "2"
    with pytest.raises(RuntimeError, match="was not found"):
        choose_account(accounts, "99")


def test_current_us_session_uses_api_times():
    calendar = {"today": {"regularMarket": {"startTime": "2026-08-07T13:00:00+00:00",
                                                "endTime": "2026-08-07T20:00:00+00:00"}}}
    assert current_us_session(calendar, datetime(2026, 8, 7, 15, tzinfo=timezone.utc)) == "REGULAR"
    assert current_us_session(calendar, datetime(2026, 8, 7, 21, tzinfo=timezone.utc)) == "CLOSED"


def test_us_calendar_uses_us_local_date():
    session = FakeSession([
        FakeResponse(200, {"access_token": "token", "expires_in": 86400}),
        FakeResponse(200, {"result": {"today": {}}}),
    ])
    client = TossClient(settings(), session=session)
    from toss.market import MarketAPI

    MarketAPI(client).us_calendar(datetime(2026, 8, 7).date())
    assert session.calls[-1][2]["params"] == {"date": "2026-08-07"}


def test_http_error_exposes_safe_api_message_without_credentials():
    session = FakeSession([FakeResponse(200, {"access_token": "private-token", "expires_in": 86400}),
                           FakeResponse(403, {"error": {"code": "forbidden", "message": "IP not allowed",
                                                        "requestId": "r1"}})])
    client = TossClient(settings(), session=session)
    with pytest.raises(TossAPIError, match=r"403 \[forbidden\].*requestId: r1") as caught:
        client.get("/api/v1/accounts")
    assert "private-token" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_oauth_error_parses_flat_standard_response():
    session = FakeSession([FakeResponse(401, {
        "error": "invalid_client", "error_description": "Client authentication failed."
    })])
    with pytest.raises(TossAPIError, match=r"401 \[invalid_client\]: Client authentication failed"):
        TossClient(settings(), session=session).get("/api/v1/accounts")


def test_rate_limit_retries_once(monkeypatch):
    session = FakeSession([
        FakeResponse(200, {"access_token": "token", "expires_in": 86400}),
        FakeResponse(429, {"error": {"code": "rate-limit-exceeded", "message": "Slow down"}},
                     {"Retry-After": "0"}),
        FakeResponse(200, {"result": []}),
    ])
    monkeypatch.setattr("toss.client.time.sleep", lambda _: None)
    client = TossClient(settings(max_retries=1), session=session)
    assert client.get("/api/v1/accounts") == []
    assert len(session.calls) == 3


def test_status_cycle_is_read_only_and_formats_empty_holding():
    responses = [
        FakeResponse(200, {"access_token": "token", "expires_in": 86400}),
        FakeResponse(200, {"result": [{"accountSeq": 1, "accountType": "BROKERAGE"}]}),
        FakeResponse(200, {"result": {"today": {"regularMarket": None}}}),
        FakeResponse(200, {"result": [{"symbol": "TQQQ", "lastPrice": "52.31", "currency": "USD"}]}),
        FakeResponse(200, {"result": {"asks": [{"price": "52.32"}], "bids": [{"price": "52.30"}]}}),
        FakeResponse(200, {"result": {"items": []}}),
        FakeResponse(200, {"result": {"currency": "USD", "cashBuyingPower": "1000.5"}}),
        FakeResponse(200, {"result": {"orders": [], "nextCursor": None, "hasNext": False}}),
    ]
    session = FakeSession(responses)
    output = build_status(settings(), TossClient(settings(), session=session))
    assert "Price: $52.31" in output
    assert "Quantity: 0" in output
    assert "Open TQQQ Orders\nNone" in output
    assert [method for method, _, _ in session.calls].count("POST") == 1
    assert session.calls[-1][2]["params"] == {"status": "OPEN", "symbol": "TQQQ"}


def test_live_run_closed_market_exits_successfully_without_order_calls():
    responses = [
        FakeResponse(200, {"access_token": "token", "expires_in": 86400}),
        FakeResponse(200, {"result": [{"accountSeq": 1, "accountType": "BROKERAGE"}]}),
        FakeResponse(200, {"result": {"today": {
            "dayMarket": None, "preMarket": None, "regularMarket": None, "afterMarket": None
        }}}),
    ]
    session = FakeSession(responses)
    live_settings = settings(dry_run=False)
    output = run_live(live_settings, TossClient(live_settings, session=session))
    assert output == "LIVE RUN\nUS market session: CLOSED\nNo orders submitted."
    assert [call[1].rsplit("/", 1)[-1] for call in session.calls] == ["token", "accounts", "US"]
