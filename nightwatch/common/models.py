"""Shared data models (spec section 9). Pydantic validates anything that crosses a trust boundary."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

AlertState = Literal["ALARM", "OK"]
Severity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "investigating", "awaiting_approval", "remediating", "resolved"]
InvestigationStatus = Literal["running", "done", "failed"]

SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}


def _json_or_none(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class MetricRef(BaseModel):
    """A metric an alarm watches, kept so investigation tools know where to look."""

    model_config = ConfigDict(frozen=True)

    namespace: str
    name: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    stat: str | None = None


class Alert(BaseModel):
    """One alarm state change, normalised from whatever sent it."""

    model_config = ConfigDict(frozen=True)

    id: str  # the sender's event id, which makes repeated deliveries harmless
    source: Literal["cloudwatch", "webhook"]
    service: str
    alarm_name: str
    alarm_arn: str | None = None
    state: AlertState
    previous_state: str | None = None
    severity: Severity = "medium"
    description: str = ""
    reason: str = ""
    fingerprint: str
    occurred_at: datetime
    metrics: tuple[MetricRef, ...] = ()
    raw: str = ""


class Incident(BaseModel):
    id: str  # ULID
    tenant_id: str
    number: int | None = None  # the human-friendly INC-001; assigned right after opening
    service: str
    title: str
    first_alarm: str
    status: IncidentStatus = "open"
    severity: Severity = "medium"
    opened_at: datetime
    updated_at: datetime
    last_alert_at: datetime
    resolved_at: datetime | None = None
    alert_count: int = 1  # firing alerts grouped into this incident
    alarms: dict[str, AlertState] = Field(default_factory=dict)  # latest state of each alarm
    reopen_count: int = 0
    discord_message_id: str | None = None
    investigation: InvestigationStatus | None = None
    # nightwatch.agent.models.Investigation, stored as JSON text (DynamoDB has no floats).
    investigation_result: Annotated[dict[str, Any] | None, BeforeValidator(_json_or_none)] = None
    postmortem_key: str | None = None  # S3 key of the postmortem, once written (spec F8)
    acked_by: str | None = None  # Discord user id, from /nw ack (spec F9)
    acked_by_name: str | None = None
    acked_at: datetime | None = None
    fix_pr_url: str | None = None  # the Terraform pull request NightWatch opened (spec F10)

    @property
    def label(self) -> str:
        return f"INC-{self.number:03d}" if self.number is not None else f"INC-{self.id[-6:]}"
