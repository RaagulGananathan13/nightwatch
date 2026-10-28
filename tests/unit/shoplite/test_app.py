"""ShopLite routes, error handling and the one-line-per-request log contract."""

import json

import pytest

from shoplite import faults
from tests.unit.shoplite.conftest import call, set_flags

pytestmark = pytest.mark.usefixtures("aws")


def test_health_reports_the_running_version() -> None:
    response = call("GET", "/health")
    assert response["statusCode"] == 200
    assert response["json"] == {"status": "ok", "version": "dev"}


def test_lists_products() -> None:
    products = call("GET", "/products")["json"]["products"]
    assert products == [{"id": "p-100", "name": "Coffee mug", "price_cents": 1200}]


def test_order_round_trip() -> None:
    created = call("POST", "/orders", {"product_id": "p-100", "quantity": 2})
    assert created["statusCode"] == 201
    order = created["json"]["order"]
    assert order["total_cents"] == 2400

    fetched = call("GET", f"/orders/{order['id']}")
    assert fetched["statusCode"] == 200
    assert fetched["json"]["order"] == order


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ([1, 2], "body must be a JSON object"),
        ({"quantity": 1}, "product_id is required"),
        ({"product_id": "p-100", "quantity": "2"}, "quantity must be a whole number"),
        ({"product_id": "p-100", "quantity": True}, "quantity must be a whole number"),
        ({"product_id": "p-100", "quantity": 11}, "quantity must be between 1 and 10"),
    ],
)
def test_bad_orders_get_400(body: object, message: str) -> None:
    response = call("POST", "/orders", body)
    assert response["statusCode"] == 400
    assert response["json"]["error"] == message


def test_body_that_is_not_json_gets_400() -> None:
    response = call("POST", "/orders", raw_body="not json")
    assert response["statusCode"] == 400
    assert response["json"]["error"] == "body must be JSON"


def test_unknown_things_get_404() -> None:
    assert call("POST", "/orders", {"product_id": "p-999"})["statusCode"] == 404
    assert call("GET", "/orders/does-not-exist")["statusCode"] == 404
    assert call("GET", "/nowhere")["statusCode"] == 404
    assert call("GET", "/orders/bad%20id")["statusCode"] == 400


def test_every_request_logs_one_json_line(capsys: pytest.CaptureFixture[str]) -> None:
    call("GET", "/orders/abc123")
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["service"] == "shoplite"
    assert record["route"] == "GET /orders/{id}"
    assert record["status"] == 404
    assert record["request_id"] == "req-1"
    assert record["level"] == "INFO"


def test_injected_errors_propagate_and_are_logged(capsys: pytest.CaptureFixture[str]) -> None:
    set_flags(error_rate=1.0)
    with pytest.raises(faults.InjectedFaultError):
        call("GET", "/products")
    record = json.loads(capsys.readouterr().out.strip())
    assert record["level"] == "ERROR"
    assert record["status"] == 502
    assert record["error_type"] == "InjectedFaultError"


def test_missing_table_fails_like_a_broken_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", "shoplite-typo")
    with pytest.raises(Exception, match=r"ResourceNotFound|non-existent|not found"):
        call("GET", "/products")
