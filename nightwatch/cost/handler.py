"""Lambda entry point for the cost guardian (spec F17).

Two ways in, one way out:
- every morning (EventBridge Scheduler): the daily check in signals.py, on free CloudWatch data;
- an AWS Budget alert (SNS): real spend, measured by AWS itself.

Each signal becomes an alert on a ``cost-guard:`` alarm and goes through the same pipeline as a
CloudWatch alarm (``ingest.handler.process``): incident, Discord, investigation, postmortem.
The daily check also sends the all-clear for cost alarms that stopped firing, so their
incidents resolve like any other. A budget alert has no all-clear of its own, so the next
morning's check sends it. Cost Explorer is never called: it charges per request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from functools import cache
from typing import Any

import boto3

from nightwatch.agent.services import load_services
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Alert
from nightwatch.common.timeutil import utcnow
from nightwatch.cost import is_cost_alarm
from nightwatch.cost.signals import Signal, budget, daily, owner
from nightwatch.cost.usage import service_usage
from nightwatch.ingest.handler import process
from nightwatch.ingest.normalize import fingerprint

CLEAR_AFTER = timedelta(hours=12)  # a cost alarm quiet this long gets its all-clear


@cache
def _clients() -> tuple[Any, Any, Any]:
    return boto3.client("cloudwatch"), boto3.client("lambda"), boto3.client("dynamodb")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    now = utcnow()
    cloudwatch, lambda_, dynamodb = _clients()
    notes = [r["Sns"] for r in event.get("Records") or [] if r.get("EventSource") == "aws:sns"]
    if notes:
        services = list(load_services().values())
        usage = {s.name: service_usage(cloudwatch, lambda_, s, now) for s in services}
        service = owner(usage, "requests", services)
        raised = [
            raise_signal(
                settings,
                budget(str(n.get("Message", "")), str(n.get("Subject") or ""), service),
                f"budget-{n.get('MessageId', now.isoformat())}",
                now,
            )
            for n in notes
        ]
        return {"budget_alerts": raised}
    signals = daily(cloudwatch, lambda_, dynamodb, now, settings.table_name)
    raised = [raise_signal(settings, s, f"cost-{now:%Y%m%d}-{s.alarm}", now) for s in signals]
    cleared = clear(settings, {s.alarm for s in signals}, now)
    return {"signals": raised, "cleared": cleared}


def raise_signal(settings: Settings, signal: Signal, alert_id: str, now: datetime) -> str:
    """Send one signal through the alarm pipeline. Returns the incident label, or the outcome."""
    alert = Alert(
        id=alert_id,
        source="cost",
        service=signal.service,
        alarm_name=signal.alarm,
        state="ALARM",
        severity=signal.severity,
        description=signal.summary[:300],
        reason=signal.summary[:1000],
        fingerprint=fingerprint("cost", signal.alarm),
        occurred_at=now,
    )
    outcome = process(alert, settings, actor="nightwatch-cost")
    return f"{signal.alarm}: {outcome.incident.label if outcome.incident else outcome.kind}"


def clear(settings: Settings, firing: set[str], now: datetime) -> list[str]:
    """The all-clear for each open incident's cost alarms that no longer fire."""
    store = IncidentStore(settings.table_name, settings.tenant_id)
    cleared: list[str] = []
    for incident in store.list_incidents("open", limit=50):
        if now - incident.last_alert_at < CLEAR_AFTER:
            continue
        for name, state in sorted(incident.alarms.items()):
            if not is_cost_alarm(name) or state != "ALARM" or name in firing:
                continue
            alert = Alert(
                id=f"cost-{now:%Y%m%d}-{name}-ok",
                source="cost",
                service=incident.service,
                alarm_name=name,
                state="OK",
                description="The cost guardian's daily check no longer sees this.",
                fingerprint=fingerprint("cost", name),
                occurred_at=now,
            )
            process(alert, settings, actor="nightwatch-cost")
            cleared.append(f"{incident.label} {name}")
    return cleared
