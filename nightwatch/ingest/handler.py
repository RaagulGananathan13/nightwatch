"""Lambda entry point: CloudWatch alarm changes in, incidents and Discord messages out.

EventBridge delivers every alarm that goes to ALARM or back to OK. Alarms without a
``nightwatch:service`` tag belong to someone else and are ignored.
"""

from __future__ import annotations

import json
import time
from dataclasses import replace
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.chat.discord import DiscordWebhook, webhook_from_ssm
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.ingest.correlate import correlate
from nightwatch.ingest.normalize import from_cloudwatch_event
from nightwatch.ingest.notify import notify
from nightwatch.postmortem import start as start_postmortem

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_stepfunctions import SFNClient

TAG_CACHE_SECONDS = 300.0

# Audit event names for each correlation outcome.
_AUDIT_EVENT = {
    "opened": "incident_opened",
    "grouped": "alert_grouped",
    "reopened": "incident_reopened",
    "recovering": "alarm_recovered",
    "resolved": "incident_resolved",
}

_secrets = SecretCache()
_tag_cache: dict[str, tuple[float, dict[str, str]]] = {}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    alert = from_cloudwatch_event(event, alarm_tags)
    if alert is None:
        emit("INFO", "event ignored", event_id=event.get("id"), kind=event.get("detail-type"))
        return {"outcome": "ignored"}

    store = IncidentStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-ingest")
    now = utcnow()
    outcome = correlate(store, alert, settings, now)

    if outcome.incident is not None and outcome.kind in _AUDIT_EVENT:
        outcome = replace(outcome, incident=store.assign_number(outcome.incident))
        audit.record(
            _AUDIT_EVENT[outcome.kind],
            outcome.incident.id if outcome.incident else None,
            alarm=alert.alarm_name,
            state=alert.state,
            alert_id=alert.id,
        )

    label = outcome.incident.label if outcome.incident else None
    emit(
        "INFO",
        "alert processed",
        outcome=outcome.kind,
        incident=label,
        service=alert.service,
        alarm=alert.alarm_name,
        state=alert.state,
        detail=outcome.detail or None,
    )
    notify(outcome, store, audit, lambda: _webhook(settings), now)
    if outcome.kind == "opened" and outcome.incident is not None:
        start_investigation(settings, outcome.incident, audit)
    resolved = outcome.kind == "resolved" and outcome.incident is not None
    if (
        resolved
        and outcome.incident
        and start_postmortem(settings.postmortem_function, outcome.incident.id)
    ):
        audit.record("postmortem_queued", outcome.incident.id)
    return {"outcome": outcome.kind, "incident": label}


def start_investigation(settings: Settings, incident: Incident, audit: AuditLog) -> None:
    """Start the incident workflow. Named after the incident, so a retry can't start a second one."""
    if not settings.state_machine_arn:
        return
    try:
        _stepfunctions().start_execution(
            stateMachineArn=settings.state_machine_arn,
            name=incident.id,
            input=json.dumps({"incident_id": incident.id}),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ExecutionAlreadyExists":
            return
        emit(
            "ERROR", "could not start investigation", incident=incident.label, error=str(exc)[:200]
        )
        audit.record("investigation_start_failed", incident.id, error=str(exc)[:200])
        return
    audit.record("investigation_queued", incident.id)


def alarm_tags(arn: str) -> dict[str, str]:
    """An alarm's tags, cached for a few minutes. Unreadable alarms count as untagged."""
    now = time.monotonic()
    cached = _tag_cache.get(arn)
    if cached and now - cached[0] < TAG_CACHE_SECONDS:
        return cached[1]
    try:
        response = _cloudwatch().list_tags_for_resource(ResourceARN=arn)
    except (BotoCoreError, ClientError) as exc:
        emit("WARN", "could not read alarm tags", alarm=arn, error=type(exc).__name__)
        return {}
    tags = {tag["Key"]: tag["Value"] for tag in response.get("Tags", [])}
    _tag_cache[arn] = (now, tags)
    return tags


def _webhook(settings: Settings) -> DiscordWebhook | None:
    return webhook_from_ssm(settings.discord_webhook_param, _secrets.get)


@cache
def _cloudwatch() -> CloudWatchClient:
    return boto3.client("cloudwatch")


@cache
def _stepfunctions() -> SFNClient:
    return boto3.client("stepfunctions")
