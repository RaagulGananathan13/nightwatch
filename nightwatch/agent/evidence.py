"""The evidence ledger (spec F4): every tool result, numbered and stored with the incident.

Each result is redacted, truncated and saved as ``EV#nnn`` under the incident's partition,
so a diagnosis can always be traced back to the exact query and result it relied on.
Results stay inside DynamoDB's item limit by truncating large data.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import datetime
from typing import Any, Literal

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.guard import redact
from nightwatch.agent.models import Evidence
from nightwatch.agent.tools import ToolContext, ToolError, run_tool
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.timeutil import to_iso, utcnow

MAX_DATA_CHARS = 12_000
MAX_SUMMARY_CHARS = 2_500


def load_evidence(store: IncidentStore, incident_id: str) -> list[Evidence]:
    """Everything gathered for an incident, in order: E1, E2, ..."""
    reply = store.client.query(
        TableName=store.table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :ev)",
        ExpressionAttributeValues={
            ":pk": {"S": f"TENANT#{store.tenant}#INC#{incident_id}"},
            ":ev": {"S": "EV#"},
        },
    )
    return [Evidence.model_validate_json(item["json"]["S"]) for item in reply.get("Items", [])]


class EvidenceLedger:
    def __init__(
        self,
        store: IncidentStore,
        incident_id: str,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.store = store
        self.incident_id = incident_id
        self.clock = clock
        self.items: list[Evidence] = []

    def gather(
        self,
        ctx: ToolContext,
        tool: str,
        args: dict[str, Any],
        label: str = "",
        requested_by: Literal["plan", "llm"] = "plan",
    ) -> Evidence:
        """Run a read-only tool and record its result (or its failure) as the next E#."""
        started_at = self.clock()
        started = time.monotonic()
        try:
            result = run_tool(tool, args, ctx)
            summary, data, ok = result.summary, result.data, True
        except ToolError as exc:
            summary, data, ok = f"Tool refused: {exc}", None, False
        except (BotoCoreError, ClientError, TimeoutError, ValueError, KeyError) as exc:
            summary, data, ok = f"Tool failed: {type(exc).__name__}: {str(exc)[:200]}", None, False
        evidence = Evidence(
            id=f"E{len(self.items) + 1}",
            tool=tool,
            label=label,
            args=redact(args),
            summary=_limit(redact(summary), MAX_SUMMARY_CHARS),
            data=_truncate(redact(data)),
            ok=ok,
            requested_by=requested_by,
            started_at=started_at,
            duration_ms=int((time.monotonic() - started) * 1000),
        )
        self.items.append(evidence)
        self._save(evidence)
        return evidence

    def by_id(self) -> dict[str, Evidence]:
        return {item.id: item for item in self.items}

    def _save(self, evidence: Evidence) -> None:
        try:
            self.store.client.put_item(
                TableName=self.store.table,
                Item={
                    "PK": {"S": f"TENANT#{self.store.tenant}#INC#{self.incident_id}"},
                    "SK": {"S": f"EV#{int(evidence.id[1:]):03d}"},
                    "type": {"S": "evidence"},
                    "evidence_id": {"S": evidence.id},
                    "tool": {"S": evidence.tool},
                    "at": {"S": to_iso(evidence.started_at)},
                    "json": {"S": evidence.model_dump_json()},
                },
            )
        except (BotoCoreError, ClientError) as exc:  # the investigation goes on without it
            emit("ERROR", "evidence write failed", evidence=evidence.id, error=type(exc).__name__)


def _limit(text: str, size: int) -> str:
    return text if len(text) <= size else text[: size - 15] + " …[truncated]"


def _truncate(data: Any) -> Any:
    if data is None:
        return None
    encoded = json.dumps(data, default=str)
    if len(encoded) <= MAX_DATA_CHARS:
        return data
    return {"truncated": True, "preview": encoded[:MAX_DATA_CHARS]}
