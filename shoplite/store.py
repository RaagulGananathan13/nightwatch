"""DynamoDB access for ShopLite. One table holds both products and orders.

| Item    | PK              | SK                  |
|---------|-----------------|---------------------|
| Product | ``PRODUCT``     | ``PRODUCT#<id>``    |
| Order   | ``ORDER#<id>``  | ``ORDER``           |
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

# Fail fast: when DynamoDB throttles, surface the error quickly instead of retrying for seconds.
_CONFIG = Config(retries={"mode": "standard", "max_attempts": 2}, connect_timeout=2, read_timeout=2)


class NotFoundError(Exception):
    """The product or order doesn't exist."""


@cache
def _client() -> DynamoDBClient:
    return boto3.client("dynamodb", config=_CONFIG)


def _table() -> str:
    return os.environ["TABLE_NAME"]


def list_products() -> list[dict[str, Any]]:
    response = _client().query(
        TableName=_table(),
        KeyConditionExpression="PK = :pk",
        ExpressionAttributeValues={":pk": {"S": "PRODUCT"}},
    )
    return [_product(item) for item in response.get("Items", [])]


def create_order(product_id: str, quantity: int) -> dict[str, Any]:
    found = _client().get_item(
        TableName=_table(),
        Key={"PK": {"S": "PRODUCT"}, "SK": {"S": f"PRODUCT#{product_id}"}},
    )
    if "Item" not in found:
        raise NotFoundError(f"product {product_id} not found")
    product = _product(found["Item"])

    order = {
        "id": uuid.uuid4().hex[:12],
        "product_id": product_id,
        "quantity": quantity,
        "total_cents": product["price_cents"] * quantity,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    _client().put_item(
        TableName=_table(),
        Item={
            "PK": {"S": f"ORDER#{order['id']}"},
            "SK": {"S": "ORDER"},
            "product_id": {"S": product_id},
            "quantity": {"N": str(quantity)},
            "total_cents": {"N": str(order["total_cents"])},
            "created_at": {"S": order["created_at"]},
        },
        ConditionExpression="attribute_not_exists(PK)",
    )
    return order


def get_order(order_id: str) -> dict[str, Any]:
    found = _client().get_item(
        TableName=_table(),
        Key={"PK": {"S": f"ORDER#{order_id}"}, "SK": {"S": "ORDER"}},
    )
    if "Item" not in found:
        raise NotFoundError(f"order {order_id} not found")
    item = found["Item"]
    return {
        "id": order_id,
        "product_id": item["product_id"]["S"],
        "quantity": int(item["quantity"]["N"]),
        "total_cents": int(item["total_cents"]["N"]),
        "created_at": item["created_at"]["S"],
    }


def _product(item: dict[str, AttributeValueTypeDef]) -> dict[str, Any]:
    return {
        "id": item["SK"]["S"].removeprefix("PRODUCT#"),
        "name": item["name"]["S"],
        "price_cents": int(item["price_cents"]["N"]),
    }
