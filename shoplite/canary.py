"""ShopLite canary: a little steady traffic, so alarms and baselines have real data.

EventBridge Scheduler runs this every minute. It acts like a customer: browse products, place
an order, read it back. The chaos lab can also invoke it with ``{"load_seconds": 20,
"parallel": 4}`` for heavy traffic from inside AWS (see ``load``). The function URL uses AWS_IAM auth, so every request is SigV4-signed.
Failures are logged but never raised, because a broken store is for NightWatch to notice.

This file is deployed on its own, so it only imports the standard library and botocore
(which the Lambda runtime provides). The chaos lab and smoke test reuse ``signed_request``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from botocore.session import Session


def signed_request(
    method: str,
    url: str,
    credentials: Credentials,
    region: str,
    body: dict[str, Any] | None = None,
    timeout: float = 10.0,
) -> tuple[int, Any]:
    """Call a Lambda function URL with SigV4 auth. Returns (status, parsed JSON or None)."""
    if not url.startswith("https://"):
        raise ValueError("function URLs are always https")
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"} if data is not None else {}
    signed = AWSRequest(method=method, url=url, data=data, headers=headers)
    SigV4Auth(credentials, "lambda", region).add_auth(signed)

    request = urllib.request.Request(  # noqa: S310 - https enforced above
        url, data=data, headers=dict(signed.headers.items()), method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310  # nosec B310
            return resp.status, _json(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _json(exc.read())
    except (urllib.error.URLError, TimeoutError):
        return 0, None


def handler(event: Any, context: Any) -> dict[str, Any]:
    base = os.environ["SHOPLITE_URL"].rstrip("/")
    region = os.environ.get("AWS_REGION", "us-east-2")
    credentials = Session().get_credentials()
    if credentials is None:
        raise RuntimeError("no AWS credentials available to sign requests")

    if isinstance(event, dict) and event.get("load_seconds"):
        seconds, parallel = float(event["load_seconds"]), int(event.get("parallel", 4))
        return load(base, credentials, region, seconds, parallel)

    checks: list[dict[str, Any]] = []

    def call(name: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        status, payload = signed_request(method, base + path, credentials, region, body)
        checks.append({"check": name, "status": status})
        return payload if 200 <= status < 300 else None

    products = call("list_products", "GET", "/products")
    if products and products.get("products"):
        product_id = products["products"][0]["id"]
        order = call("create_order", "POST", "/orders", {"product_id": product_id, "quantity": 1})
        if order:
            call("get_order", "GET", f"/orders/{order['order']['id']}")
    call("health", "GET", "/health")

    ok = all(200 <= check["status"] < 300 for check in checks)
    summary = {"level": "INFO" if ok else "WARN", "msg": "canary", "ok": ok, "checks": checks}
    sys.stdout.write(json.dumps(summary, separators=(",", ":")) + "\n")
    return summary


def load(
    base: str, credentials: Credentials, region: str, seconds: float, parallel: int
) -> dict[str, Any]:
    """GET /products as fast as ``parallel`` workers can for ``seconds`` (capped at 4 workers
    and 25 s, inside the 30 s timeout). The chaos lab uses it to exhaust DynamoDB capacity."""
    seconds, parallel = min(max(seconds, 1.0), 25.0), min(max(parallel, 1), 4)
    deadline = time.monotonic() + seconds

    def worker() -> Counter[int]:
        counts: Counter[int] = Counter()
        while time.monotonic() < deadline:
            status, _ = signed_request("GET", base + "/products", credentials, region)
            counts[status] += 1
        return counts

    with ThreadPoolExecutor(parallel) as pool:
        futures = [pool.submit(worker) for _ in range(parallel)]
        totals: Counter[int] = sum((future.result() for future in futures), Counter())
    summary = {
        "level": "INFO",
        "msg": "load",
        "seconds": seconds,
        "parallel": parallel,
        "statuses": {str(code): count for code, count in sorted(totals.items())},
    }
    sys.stdout.write(json.dumps(summary, separators=(",", ":")) + "\n")
    return summary


def _json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None
