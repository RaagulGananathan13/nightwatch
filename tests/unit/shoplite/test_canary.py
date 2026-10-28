"""The canary signs its requests and reports, never raises, when the store is broken."""

import json
from typing import Any

import pytest
from botocore.credentials import Credentials

from shoplite import canary

URL = "https://abc.lambda-url.us-east-2.on.aws"


class FakeResponse:
    def __init__(self, status: int, body: Any) -> None:
        self.status = status
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def test_requests_are_sigv4_signed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[Any] = []

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        seen.append(request)
        return FakeResponse(200, {"status": "ok"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    status, body = canary.signed_request(
        "GET", URL + "/health", Credentials("AKIDEXAMPLE", "secret"), "us-east-2"
    )
    assert (status, body) == (200, {"status": "ok"})
    auth = seen[0].get_header("Authorization")
    assert auth.startswith("AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/")
    assert "/us-east-2/lambda/aws4_request" in auth


def test_plain_http_is_refused() -> None:
    with pytest.raises(ValueError, match="https"):
        canary.signed_request("GET", "http://example.com", Credentials("a", "b"), "us-east-2")


def test_canary_walks_the_customer_journey(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    replies = {
        "GET /products": (200, {"products": [{"id": "p-100"}]}),
        "POST /orders": (201, {"order": {"id": "o-1"}}),
        "GET /orders/o-1": (200, {"order": {"id": "o-1"}}),
        "GET /health": (500, None),
    }

    def fake(method: str, url: str, *_: Any, **__: Any) -> tuple[int, Any]:
        return replies[f"{method} {url.removeprefix(URL)}"]

    monkeypatch.setenv("SHOPLITE_URL", URL + "/")
    monkeypatch.setattr(canary, "signed_request", fake)
    result = canary.handler({}, None)

    assert [check["check"] for check in result["checks"]] == [
        "list_products",
        "create_order",
        "get_order",
        "health",
    ]
    assert result["ok"] is False  # /health failed, and the canary said so without raising
    assert json.loads(capsys.readouterr().out)["level"] == "WARN"
