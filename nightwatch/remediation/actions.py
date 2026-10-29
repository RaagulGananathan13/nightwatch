"""Proposed fixes and their approval state (spec section 9: the Action item).

| Item   | PK                     | SK          |
|--------|------------------------|-------------|
| Action | TENANT#<t>#INC#<ulid>  | ACT#<ulid>  |

An action moves through these statuses, and every move is a conditional write on the item's
version, so a double click, a late click and the approval timeout can't both win:

    proposed ─► awaiting_approval ─► approved ─► executing ─► executed ─► verified
        │              │                 │            │            ├────► not_fixed / no_data
        │              │                 │            └► failed    └────► worse ─► reverted
        └──────────────┴► blocked        └─► failed                              (or revert_failed)
                       ├► rejected
                       └► expired

The Step Functions task token is stored on the item (not in the JSON copy) so the button
handler can resume the workflow.
"""

from __future__ import annotations

from collections.abc import Collection
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, get_args

import boto3
from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

from nightwatch.common.ids import new_ulid
from nightwatch.remediation.planner import Proposal

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

Status = Literal[
    "proposed",
    "awaiting_approval",
    "approved",
    "rejected",
    "expired",
    "blocked",
    "executing",
    "executed",
    "failed",
    "verified",
    "not_fixed",
    "no_data",
    "worse",
    "reverted",
    "revert_failed",
]
ALL: frozenset[str] = frozenset(get_args(Status))
FINAL: frozenset[str] = frozenset(
    {"rejected", "expired", "blocked", "failed", "verified", "not_fixed", "no_data"}
    | {"reverted", "revert_failed"}
)


class TransitionError(Exception):
    """The action was not in a status that allows this change (someone else got there first)."""

    def __init__(self, status: str) -> None:
        super().__init__(f"the action is already {status}")
        self.status = status


class Action(BaseModel):
    id: str
    incident_id: str
    service: str
    runbook_id: str
    title: str
    risk: Literal["low", "medium", "high"]
    auto_approve: bool = False
    reason: str  # which evidence showed the problem
    evidence_id: str
    preview: str
    blast_radius: str
    params: dict[str, str | int]
    call: dict[str, Any]
    rollback: dict[str, Any] | None = None
    verify: dict[str, Any]
    before: dict[str, str]  # state when the fix was planned
    status: Status = "proposed"
    version: int = 0
    created_at: datetime
    expires_at: datetime | None = None  # when the approval request times out
    message_id: str | None = None  # the approval card in Discord
    decided_by: str | None = None  # Discord user id
    decided_by_name: str | None = None
    decided_at: datetime | None = None
    executed_at: datetime | None = None
    after: dict[str, str] | None = None  # state right after the fix
    result: str = ""  # what happened, in one sentence
    verification: dict[str, Any] | None = None
    task_token: str | None = Field(default=None, exclude=True)

    @classmethod
    def from_proposal(
        cls, proposal: Proposal, incident_id: str, service: str, now: datetime
    ) -> Action:
        book = proposal.runbook
        return cls(
            id=new_ulid(now),
            incident_id=incident_id,
            service=service,
            runbook_id=book.id,
            title=book.title,
            risk=book.risk,
            auto_approve=book.auto_approve and book.risk == "low",
            reason=proposal.reason,
            evidence_id=proposal.evidence_id,
            preview=proposal.preview,
            blast_radius=proposal.blast_radius,
            params=proposal.params,
            call=proposal.call,
            rollback=proposal.rollback,
            verify=proposal.verify,
            before=proposal.before,
            created_at=now,
        )


class ActionStore:
    def __init__(
        self, table_name: str, tenant_id: str, client: DynamoDBClient | None = None
    ) -> None:
        self.table = table_name
        self.tenant = tenant_id
        self._client = client

    @property
    def client(self) -> DynamoDBClient:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    def create(self, action: Action) -> Action:
        self.client.put_item(
            TableName=self.table,
            Item=self._item(action),
            ConditionExpression="attribute_not_exists(PK)",
        )
        return action

    def get(self, incident_id: str, action_id: str) -> Action | None:
        reply = self.client.get_item(
            TableName=self.table, Key=self._key(incident_id, action_id), ConsistentRead=True
        )
        item = reply.get("Item")
        return _action(item) if item else None

    def for_incident(self, incident_id: str) -> list[Action]:
        reply = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :act)",
            ExpressionAttributeValues={
                ":pk": {"S": self._pk(incident_id)},
                ":act": {"S": "ACT#"},
            },
            ConsistentRead=True,
        )
        return [_action(item) for item in reply.get("Items", [])]

    def transition(self, action: Action, allowed: Collection[str], **changes: Any) -> Action:
        """Apply ``changes`` if the action is still in one of the ``allowed`` statuses.

        Writes are guarded by the item's version. If someone else wrote first, read it again
        and try once more, so an unrelated change (say, the card's message id) isn't lost.
        """
        for _ in range(3):
            if action.status not in allowed:
                raise TransitionError(action.status)
            updated = action.model_copy(update={**changes, "version": action.version + 1})
            try:
                self.client.put_item(
                    TableName=self.table,
                    Item=self._item(updated),
                    ConditionExpression="#ver = :ver",
                    ExpressionAttributeNames={"#ver": "ver"},
                    ExpressionAttributeValues={":ver": {"N": str(action.version)}},
                )
                return updated
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "ConditionalCheckFailedException":
                    raise
            fresh = self.get(action.incident_id, action.id)
            if fresh is None:
                raise TransitionError("deleted")
            action = fresh
        raise TransitionError(action.status)

    def _pk(self, incident_id: str) -> str:
        return f"TENANT#{self.tenant}#INC#{incident_id}"

    def _key(self, incident_id: str, action_id: str) -> dict[str, Any]:
        return {"PK": {"S": self._pk(incident_id)}, "SK": {"S": f"ACT#{action_id}"}}

    def _item(self, action: Action) -> dict[str, Any]:
        item = {
            **self._key(action.incident_id, action.id),
            "type": {"S": "action"},
            "status": {"S": action.status},
            "ver": {"N": str(action.version)},
            "json": {"S": action.model_dump_json()},
        }
        if action.task_token:
            item["task_token"] = {"S": action.task_token}
        return item


def _action(item: dict[str, Any]) -> Action:
    action = Action.model_validate_json(item["json"]["S"])
    action.task_token = item.get("task_token", {}).get("S")
    return action
