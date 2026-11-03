"""Lambda entry point for the cost guardian (spec F17).

Two ways in, one way out:
- every morning (EventBridge Scheduler): the daily check in signals.py, on free CloudWatch data;
- an AWS Budget alert (SNS): real spend, measured by AWS itself.

Each signal becomes an alert on a ``cost-guard:`` alarm and goes through the same pipeline as a
CloudWatch alarm (``ingest.handler.process``): incident, Discord, investigation, postmortem.
When the money is real, the emergency stop (stop.py) also switches everything off: a budget
alert at or past the stop line, the runaway-invocations alarm (which calls this directly), or
a morning check that finds a free tier already used up.
The daily check also sends the all-clear for cost alarms that stopped firing, so their
incidents resolve like any other. A budget alert has no all-clear of its own, so the next
morning's check sends it. Cost Explorer is never called: it charges per request.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from functools import cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.services import load_services
from nightwatch.chat.discord import DiscordError, webhook_from_ssm
from nightwatch.chat.slack import SlackError
from nightwatch.chat.slack import bot_from_ssm as slack_bot
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Alert
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.cost import PREFIX, is_cost_alarm
from nightwatch.cost.signals import Signal, budget, budget_spend, daily, owner
from nightwatch.cost.stop import stop
from nightwatch.cost.usage import service_usage
from nightwatch.ingest.handler import process
from nightwatch.ingest.normalize import fingerprint

CLEAR_AFTER = timedelta(hours=12)  # a cost alarm quiet this long gets its all-clear
_secrets = SecretCache()


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
        result: dict[str, Any] = {"budget_alerts": raised}
        spent = [
            budget_spend(str(n.get("Message", "")), str(n.get("Subject") or "")) for n in notes
        ]
        real = [usd for kind, usd in spent if kind == "ACTUAL" and usd is not None]
        if real and max(real) >= settings.stop_at_usd:
            result["stopped"] = emergency_stop(
                settings,
                f"AWS reports ${max(real):.2f} of real spend this month, past the "
                f"${settings.stop_at_usd:.2f} stop line.",
            )
        return result

    alarm = event.get("alarmData") or {}
    if event.get("source") == "aws.cloudwatch" and alarm:  # the runaway-invocations alarm
        if (alarm.get("state") or {}).get("value") != "ALARM":
            return {"alarm": "not firing"}
        reason = (
            f"Lambda calls ran away: alarm {alarm.get('alarmName', '?')} saw more calls in five "
            "minutes than NightWatch ever makes, such as a flood on a public endpoint."
        )
        return {"stopped": emergency_stop(settings, reason)}

    signals = daily(cloudwatch, lambda_, dynamodb, now, settings.table_name)
    raised = [raise_signal(settings, s, f"cost-{now:%Y%m%d}-{s.alarm}", now) for s in signals]
    cleared = clear(settings, {s.alarm for s in signals}, now)
    result = {"signals": raised, "cleared": cleared}
    costing = [s for s in signals if _costs_money(s)]
    if costing:
        reason = "The morning check found real cost. " + " ".join(s.summary for s in costing)
        result["stopped"] = emergency_stop(settings, reason[:600])
    return result


def emergency_stop(settings: Settings, reason: str) -> dict[str, Any]:
    """Switch NightWatch and ShopLite off (stop.py) and record why."""
    _, lambda_, dynamodb = _clients()
    tables = [
        settings.table_name,
        *[t for s in load_services().values() for t in s.dynamodb_tables],
    ]
    stopped = stop(
        reason,
        lambda_,
        boto3.client("ssm"),
        dynamodb,
        settings.kill_switch_param,
        tables,
        lambda message: _tell(settings, message),
        me=os.environ.get("AWS_LAMBDA_FUNCTION_NAME", ""),
    )
    AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-cost").record(
        "emergency_stop",
        None,
        reason=reason,
        functions=len(stopped.functions),
        tables=stopped.tables,
        problems=stopped.problems,
    )
    emit("ERROR", "emergency stop", reason=reason, problems=stopped.problems)
    return {
        "functions": len(stopped.functions),
        "tables": stopped.tables,
        "problems": stopped.problems,
    }


def _costs_money(signal: Signal) -> bool:
    """A free tier already used up, or capacity past the free tier: money is being spent now.
    A forecast or a jump only warns."""
    used_up = signal.alarm.startswith(f"{PREFIX}free-tier:") and signal.severity == "high"
    return used_up or signal.alarm == f"{PREFIX}dynamodb-capacity"


def _tell(settings: Settings, message: dict[str, Any]) -> None:
    """Discord and Slack, best effort: the stop goes ahead even if neither answers."""
    try:
        hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
        if hook:
            hook.send(message)
    except (DiscordError, BotoCoreError, ClientError, ValueError) as exc:
        emit("WARNING", "could not tell Discord about the stop", error=str(exc)[:200])
    try:
        bot = slack_bot(settings.slack_bot_token_param, settings.slack_channel_id, _secrets.get)
        if bot:
            bot.send(message)
    except (SlackError, BotoCoreError, ClientError, ValueError) as exc:
        emit("WARNING", "could not tell Slack about the stop", error=str(exc)[:200])


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
