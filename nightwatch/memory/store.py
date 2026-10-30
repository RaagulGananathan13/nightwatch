"""Incident memories in DynamoDB, and the search over them (spec F6).

| Item   | PK         | SK                           |
|--------|------------|------------------------------|
| Memory | TENANT#<t> | MEM#<service>#<incident id>  |

The postmortem step writes one when an incident resolves: what it looked like, what caused it
and what fixed it, with its vector. ``search_similar_incidents`` reads them back.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

import boto3

from nightwatch.common.models import Incident
from nightwatch.common.timeutil import from_iso, to_iso
from nightwatch.memory.embed import Vector, embed, rank

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

    from nightwatch.remediation.actions import Action

MAX_MEMORIES = 200  # per service, newest first
MIN_SIMILARITY = 0.2
# Between two near-identical incidents, the recent one is usually the more useful example.
RECENCY_BONUS = 0.03
RECENCY_SPAN = timedelta(days=30)


@dataclass(frozen=True)
class Memory:
    incident_id: str
    label: str
    service: str
    opened_at: datetime
    resolved_at: datetime | None
    cause: str
    fix: str
    text: str  # what the vector was made from
    vector: Vector


@dataclass(frozen=True)
class Match:
    memory: Memory
    score: float


def remember(incident: Incident, actions: list[Action]) -> Memory:
    """What to keep about a resolved incident: how it looked, its cause and its fix."""
    result = incident.investigation_result or {}
    diagnosis = result.get("diagnosis") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    cause = str(top.get("statement") or diagnosis.get("summary") or "not diagnosed")[:400]
    action = actions[-1] if actions else None
    if action and action.status in ("executed", "verified", "not_fixed", "no_data", "reverted"):
        fix = f"{action.title}: {action.preview} ({action.status.replace('_', ' ')})"
    elif action:
        fix = f"{action.title} was proposed ({action.status}); the alarms recovered without it"
    else:
        fix = "the alarms recovered without a fix from NightWatch"
    alarms = ", ".join(sorted(incident.alarms))
    text = (
        f"{incident.title}. Alarms: {alarms}. {diagnosis.get('summary', '')} "
        f"Cause: {cause} Fix: {fix}"
    )[:3000]
    return Memory(
        incident_id=incident.id,
        label=incident.label,
        service=incident.service,
        opened_at=incident.opened_at,
        resolved_at=incident.resolved_at,
        cause=cause,
        fix=fix[:300],
        text=text,
        vector=embed(text),
    )


class MemoryStore:
    def __init__(self, table_name: str, tenant_id: str, client: DynamoDBClient | None = None):
        self.table = table_name
        self.tenant = tenant_id
        self._client = client

    @property
    def client(self) -> DynamoDBClient:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    def put(self, memory: Memory) -> None:
        item: dict[str, Any] = {
            "PK": {"S": f"TENANT#{self.tenant}"},
            "SK": {"S": f"MEM#{memory.service}#{memory.incident_id}"},
            "type": {"S": "memory"},
            "incident_id": {"S": memory.incident_id},
            "label": {"S": memory.label},
            "service": {"S": memory.service},
            "opened_at": {"S": to_iso(memory.opened_at)},
            "cause": {"S": memory.cause},
            "fix": {"S": memory.fix},
            "text": {"S": memory.text},
            "vector": {"S": json.dumps(memory.vector, separators=(",", ":"))},
        }
        if memory.resolved_at:
            item["resolved_at"] = {"S": to_iso(memory.resolved_at)}
        self.client.put_item(TableName=self.table, Item=item)

    def all(self, service: str) -> list[Memory]:
        reply = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :mem)",
            ExpressionAttributeValues={
                ":pk": {"S": f"TENANT#{self.tenant}"},
                ":mem": {"S": f"MEM#{service}#"},
            },
            ScanIndexForward=False,  # ULIDs sort by time, so this is newest first
            Limit=MAX_MEMORIES,
        )
        return [_memory(item) for item in reply.get("Items", [])]

    def search(
        self, service: str, text: str, now: datetime, exclude: str = "", limit: int = 3
    ) -> list[Match]:
        """The most similar earlier incidents, best first, above a minimum similarity."""
        memories = [m for m in self.all(service) if m.incident_id != exclude]
        if not memories or not text.strip():
            return []
        scores = rank(embed(text), [memory.vector for memory in memories])
        ranked = sorted(
            zip(memories, scores, strict=True),
            key=lambda pair: pair[1] + RECENCY_BONUS * _freshness(pair[0], now),
            reverse=True,
        )
        return [Match(memory, score) for memory, score in ranked if score >= MIN_SIMILARITY][:limit]


def _freshness(memory: Memory, now: datetime) -> float:
    """1 for an incident from today, falling to 0 over 30 days."""
    age = now - (memory.resolved_at or memory.opened_at)
    return max(0.0, 1.0 - age / RECENCY_SPAN)


def _memory(item: dict[str, Any]) -> Memory:
    resolved = item.get("resolved_at", {}).get("S")
    return Memory(
        incident_id=item["incident_id"]["S"],
        label=item["label"]["S"],
        service=item["service"]["S"],
        opened_at=from_iso(item["opened_at"]["S"]),
        resolved_at=from_iso(resolved) if resolved else None,
        cause=item["cause"]["S"],
        fix=item["fix"]["S"],
        text=item["text"]["S"],
        vector={int(key): float(value) for key, value in json.loads(item["vector"]["S"]).items()},
    )
