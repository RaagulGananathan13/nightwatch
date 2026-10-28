"""A ShopLite table and flags parameter in moto, plus helpers to call the handler like Lambda does."""

import json
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any

import boto3
import pytest
from moto import mock_aws

from shoplite import app, store
from shoplite.flags import FlagSource

TABLE = "shoplite-test"
FLAGS_PARAM = "/shoplite/flags"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    with mock_aws():
        dynamodb = boto3.client("dynamodb")
        dynamodb.create_table(
            TableName=TABLE,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PROVISIONED",
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        dynamodb.put_item(
            TableName=TABLE,
            Item={
                "PK": {"S": "PRODUCT"},
                "SK": {"S": "PRODUCT#p-100"},
                "name": {"S": "Coffee mug"},
                "price_cents": {"N": "1200"},
            },
        )
        boto3.client("ssm").put_parameter(Name=FLAGS_PARAM, Value="{}", Type="String")

        # Fresh clients inside the mock, and no flag cache carried between tests.
        store._client.cache_clear()
        monkeypatch.setattr(app, "_flags", FlagSource(FLAGS_PARAM, ttl=0))
        yield
        store._client.cache_clear()


def set_flags(**flags: Any) -> None:
    boto3.client("ssm").put_parameter(
        Name=FLAGS_PARAM, Value=json.dumps(flags), Type="String", Overwrite=True
    )


def call(method: str, path: str, body: Any = None, raw_body: str | None = None) -> dict[str, Any]:
    """Invoke the handler like a function URL would. ``raw_body`` skips JSON encoding."""
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "body": (
            raw_body if raw_body is not None else (json.dumps(body) if body is not None else None)
        ),
        "isBase64Encoded": False,
    }
    context = SimpleNamespace(aws_request_id="req-1", get_remaining_time_in_millis=lambda: 4000)
    response = app.handler(event, context)
    response["json"] = json.loads(response["body"])
    return response
