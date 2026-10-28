"""ShopLite HTTP API, served by a Lambda Function URL with AWS_IAM auth.

    GET  /health
    GET  /products
    POST /orders              body: {"product_id": "p-100", "quantity": 1}
    GET  /orders/{order_id}

Every request writes exactly one JSON log line. Expected problems (bad input, unknown ids)
become 4xx responses. Anything unexpected is logged and then re-raised, so Lambda records a
real error just as it would for a production bug.
"""

from __future__ import annotations

import base64
import json
import os
import re
import time
from typing import Any

from shoplite import faults, logs, store
from shoplite.build_info import VERSION
from shoplite.flags import FlagSource

_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")
_ORDER_PATH = re.compile(r"^/orders/([^/]+)$")
_MAX_QUANTITY = 10

_flags = FlagSource(os.environ.get("FLAGS_PARAM", "/shoplite/flags"))


class HttpError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    started = time.perf_counter()
    method = str(event.get("requestContext", {}).get("http", {}).get("method", "GET")).upper()
    path = str(event.get("rawPath") or "/")
    status, error_type = 502, None
    try:
        hog = faults.inject(_flags.current(), remaining_ms=lambda: _remaining_ms(context))
        status, body = _route(method, path, event)
        del hog
        return _response(status, body)
    except HttpError as exc:
        status = exc.status
        return _response(exc.status, {"error": exc.message})
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        logs.emit(
            "ERROR" if status >= 500 else "INFO",
            "request",
            request_id=getattr(context, "aws_request_id", "local"),
            method=method,
            route=_route_label(method, path),
            status=status,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            error_type=error_type,
        )


def _route(method: str, path: str, event: dict[str, Any]) -> tuple[int, Any]:
    if method == "GET" and path == "/health":
        return 200, {"status": "ok", "version": VERSION}
    if method == "GET" and path == "/products":
        return 200, {"products": store.list_products()}
    if method == "POST" and path == "/orders":
        product_id, quantity = _parse_order(event)
        try:
            return 201, {"order": store.create_order(product_id, quantity)}
        except store.NotFoundError as exc:
            raise HttpError(404, str(exc)) from exc

    match = _ORDER_PATH.match(path)
    if method == "GET" and match:
        order_id = match.group(1)
        if not _ID.match(order_id):
            raise HttpError(400, "invalid order id")
        try:
            return 200, {"order": store.get_order(order_id)}
        except store.NotFoundError as exc:
            raise HttpError(404, str(exc)) from exc

    raise HttpError(404, f"no route for {method} {path}")


def _parse_order(event: dict[str, Any]) -> tuple[str, int]:
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8", errors="replace")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HttpError(400, "body must be JSON") from exc
    if not isinstance(payload, dict):
        raise HttpError(400, "body must be a JSON object")

    product_id = payload.get("product_id")
    quantity = payload.get("quantity", 1)
    if not isinstance(product_id, str) or not _ID.match(product_id):
        raise HttpError(400, "product_id is required")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise HttpError(400, "quantity must be a whole number")
    if not 1 <= quantity <= _MAX_QUANTITY:
        raise HttpError(400, f"quantity must be between 1 and {_MAX_QUANTITY}")
    return product_id, quantity


def _route_label(method: str, path: str) -> str:
    """Group requests by route, so /orders/abc and /orders/xyz count as one in log queries."""
    return f"{method} {'/orders/{id}' if _ORDER_PATH.match(path) else path}"


def _response(status: int, body: Any) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _remaining_ms(context: Any) -> int:
    getter = getattr(context, "get_remaining_time_in_millis", None)
    return int(getter()) if callable(getter) else 5_000
