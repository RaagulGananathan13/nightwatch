"""Audit log (spec F14): what NightWatch did, when, and why. Audit items never expire."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.common.ids import new_ulid
from nightwatch.common.log import emit
from nightwatch.common.timeutil import to_iso, utcnow

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient


class AuditLog:
    def __init__(
        self,
        table_name: str,
        tenant_id: str,
        actor: str,
        client: DynamoDBClient | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.table = table_name
        self.tenant = tenant_id
        self.actor = actor
        self._client = client
        self._clock = clock

    @property
    def client(self) -> DynamoDBClient:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    def record(self, event: str, incident_id: str | None = None, **details: Any) -> None:
        """Write one audit entry. A failed write is logged, never raised, so it can't stop work."""
        now = self._clock()
        item: dict[str, Any] = {
            "PK": {"S": f"TENANT#{self.tenant}"},
            "SK": {"S": f"AUDIT#{to_iso(now)}#{new_ulid(now)}"},
            "type": {"S": "audit"},
            "event": {"S": event},
            "actor": {"S": self.actor},
            "at": {"S": to_iso(now)},
            "details": {"S": json.dumps(details, default=str, sort_keys=True)},
        }
        if incident_id:
            item["incident_id"] = {"S": incident_id}
        try:
            self.client.put_item(TableName=self.table, Item=item)
        except (BotoCoreError, ClientError) as exc:
            emit("ERROR", "audit write failed", event=event, error=type(exc).__name__)

    def for_incident(self, incident_id: str) -> list[dict[str, Any]]:
        """Audit entries about one incident, oldest first."""
        entries: list[dict[str, Any]] = []
        paginator = self.client.get_paginator("query")
        for page in paginator.paginate(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :audit)",
            FilterExpression="incident_id = :incident",
            ExpressionAttributeValues={
                ":pk": {"S": f"TENANT#{self.tenant}"},
                ":audit": {"S": "AUDIT#"},
                ":incident": {"S": incident_id},
            },
        ):
            for item in page.get("Items", []):
                entries.append(
                    {
                        "event": item["event"]["S"],
                        "actor": item["actor"]["S"],
                        "at": item["at"]["S"],
                        "details": json.loads(item["details"]["S"]),
                    }
                )
        return entries
