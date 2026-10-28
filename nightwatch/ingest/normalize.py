"""Turn an EventBridge "CloudWatch Alarm State Change" event into an Alert (spec F1).

Only alarms tagged ``nightwatch:service`` are NightWatch's business. Everything else is
ignored, so other projects' alarms in the same account never open incidents.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from nightwatch.common.models import Alert, AlertState, MetricRef, Severity

TagLookup = Callable[[str], Mapping[str, str]]

SERVICE_TAG = "nightwatch:service"
SEVERITY_TAG = "nightwatch:severity"


class _State(BaseModel):
    value: str
    reason: str = ""
    timestamp: str | None = None


class _Detail(BaseModel):
    alarm_name: str = Field(alias="alarmName")
    state: _State
    previous_state: _State | None = Field(default=None, alias="previousState")
    configuration: dict[str, Any] = Field(default_factory=dict)


class AlarmStateChange(BaseModel):
    """The parts of the EventBridge event NightWatch relies on. Anything else fails validation."""

    id: str
    source: Literal["aws.cloudwatch"]
    detail_type: Literal["CloudWatch Alarm State Change"] = Field(alias="detail-type")
    account: str
    region: str
    time: datetime
    resources: list[str] = Field(default_factory=list)
    detail: _Detail


def from_cloudwatch_event(event: Mapping[str, Any], tags: TagLookup) -> Alert | None:
    """Return the Alert, or None if the event isn't a managed alarm going to ALARM or OK."""
    try:
        change = AlarmStateChange.model_validate(event)
    except ValidationError:
        return None
    value = change.detail.state.value
    if value not in ("ALARM", "OK"):
        return None  # INSUFFICIENT_DATA says nothing about health
    state: AlertState = "ALARM" if value == "ALARM" else "OK"

    name = change.detail.alarm_name
    arn = (
        change.resources[0]
        if change.resources
        else f"arn:aws:cloudwatch:{change.region}:{change.account}:alarm:{name}"
    )
    alarm_tags = tags(arn)
    service = alarm_tags.get(SERVICE_TAG, "").strip()
    if not service:
        return None

    config = change.detail.configuration
    previous = change.detail.previous_state
    return Alert(
        id=change.id,
        source="cloudwatch",
        service=service,
        alarm_name=name,
        alarm_arn=arn,
        state=state,
        previous_state=previous.value if previous else None,
        severity=_severity(alarm_tags.get(SEVERITY_TAG, "")),
        description=str(config.get("description") or "")[:300],
        reason=change.detail.state.reason[:1000],
        fingerprint=fingerprint("cloudwatch", change.account, change.region, name),
        occurred_at=_when(change),
        metrics=_metrics(config),
        raw=json.dumps(event, default=str)[:4000],
    )


def fingerprint(*parts: str) -> str:
    """A stable id for "the same alarm", however many times it fires."""
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


_SEVERITIES: dict[str, Severity] = {
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
}


def _severity(tag: str) -> Severity:
    return _SEVERITIES.get(tag.strip().lower(), "medium")  # missing or unknown counts as medium


def _when(change: AlarmStateChange) -> datetime:
    """When the alarm changed state. CloudWatch writes e.g. 2026-10-28T10:54:01.989+0000."""
    stamp = change.detail.state.timestamp
    if stamp:
        try:
            return datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%S.%f%z")
        except ValueError:
            pass
    return change.time


def _metrics(config: Mapping[str, Any]) -> tuple[MetricRef, ...]:
    found: list[MetricRef] = []
    for entry in config.get("metrics") or []:
        stat = entry.get("metricStat") if isinstance(entry, dict) else None
        metric = stat.get("metric") if isinstance(stat, dict) else None
        if not isinstance(metric, dict) or "name" not in metric:
            continue  # metric-math expressions have no metric of their own
        found.append(
            MetricRef(
                namespace=str(metric.get("namespace", "")),
                name=str(metric["name"]),
                dimensions={str(k): str(v) for k, v in (metric.get("dimensions") or {}).items()},
                stat=str(stat.get("stat")) if isinstance(stat, dict) and stat.get("stat") else None,
            )
        )
    return tuple(found)
