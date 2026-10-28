"""Lambda entry point: CloudWatch alarm changes in, incidents and Discord messages out.

EventBridge delivers every alarm that goes to ALARM or back to OK. Alarms without a
``nightwatch:service`` tag belong to someone else and are ignored.
"""

from __future__ import annotations

import time
from dataclasses import replace
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.chat.discord import DiscordWebhook
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.ingest.correlate import correlate
from nightwatch.ingest.normalize import from_cloudwatch_event
from nightwatch.ingest.notify import notify

if TYPE_CHECKING:
    from mypy_boto3_cloudwatch import CloudWatchClient

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
    return {"outcome": outcome.kind, "incident": label}


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
    if not settings.discord_webhook_param:
        return None
    return DiscordWebhook(_secrets.get(settings.discord_webhook_param))


@cache
def _cloudwatch() -> CloudWatchClient:
    return boto3.client("cloudwatch")
