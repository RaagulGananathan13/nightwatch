"""Shared data models (spec section 9). Pydantic validates anything that crosses a trust boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AlertState = Literal["ALARM", "OK"]
Severity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "investigating", "awaiting_approval", "remediating", "resolved"]

SEVERITY_RANK: dict[str, int] = {"low": 1, "medium": 2, "high": 3, "critical": 4}


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

    @property
    def label(self) -> str:
        return f"INC-{self.number:03d}" if self.number is not None else f"INC-{self.id[-6:]}"
