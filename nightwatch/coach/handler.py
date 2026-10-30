"""Lambda entry point: the weekly alert-quality report (spec F11).

EventBridge Scheduler runs it every Monday:
1. score each alarm over the last 7 days (stats.py);
2. read each alarm's settings from CloudWatch, for the tuning suggestions;
3. store the report, which the dashboard's Alarms page shows;
4. post a digest to Discord.
"""

from __future__ import annotations

import json
from datetime import timedelta
from functools import cache
from typing import Any

import boto3

from nightwatch.chat.discord import DiscordError, webhook_from_ssm
from nightwatch.coach.stats import alarm_stats, digest, suggest, within
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import to_iso, utcnow
from nightwatch.remediation.actions import ActionStore

PERIOD = timedelta(days=7)
_secrets = SecretCache()


@cache
def _cloudwatch() -> Any:
    return boto3.client("cloudwatch")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    now = utcnow()
    days = int((event or {}).get("days", PERIOD.days))
    since = now - timedelta(days=days)
    store = IncidentStore(settings.table_name, settings.tenant_id)
    actions = ActionStore(settings.table_name, settings.tenant_id)
    incidents = [
        incident
        for status in ("open", "resolved")
        for incident in store.list_incidents(status, limit=200)
        if within(incident, since)
    ]
    records = [(i, store.alerts(i.id), actions.for_incident(i.id)) for i in incidents]
    stats = alarm_stats(records)
    configs = _alarm_configs(sorted(stats))
    for name, entry in stats.items():
        entry.suggestion = suggest(entry, configs.get(name))
    report = {
        **digest(stats),
        "period_days": days,
        "since": to_iso(since),
        "until": to_iso(now),
        "incidents": len(incidents),
    }
    store.client.put_item(
        TableName=store.table,
        Item={
            "PK": {"S": f"TENANT#{store.tenant}"},
            "SK": {"S": f"REPORT#alarms#{to_iso(now)}"},
            "type": {"S": "alarm_report"},
            "json": {"S": json.dumps(report)},
            "expires_at": {"N": str(int((now + timedelta(days=180)).timestamp()))},
        },
    )
    AuditLog(settings.table_name, settings.tenant_id, "nightwatch-coach").record(
        "alarm_report_written", None, alarms=len(stats), noisy=report["noisy"]
    )
    _post(settings, report)
    return {"alarms": len(stats), "noisy": report["noisy"], "incidents": len(incidents)}


def _alarm_configs(names: list[str]) -> dict[str, dict[str, Any]]:
    configs: dict[str, dict[str, Any]] = {}
    for start in range(0, len(names), 100):
        reply = _cloudwatch().describe_alarms(AlarmNames=names[start : start + 100])
        for alarm in reply.get("MetricAlarms", []):
            configs[alarm["AlarmName"]] = alarm
    return configs


def message(report: dict[str, Any]) -> dict[str, Any]:
    """The weekly digest: one line of totals, then the noisiest alarms with a fix each."""
    noisy = [a for a in report["alarms"] if a["alarm"] in report["noisy"]]
    if noisy:
        headline = (
            f"📊 **Alert quality, last {report['period_days']} days:** {report['fired']} alarm "
            f"firings across {report['incidents']} incidents. {len(noisy)} alarm(s) caused "
            f"{report['noisy_share']}% of the pages that cleared by themselves. Suggested fixes below."
        )
    else:
        headline = (
            f"📊 **Alert quality, last {report['period_days']} days:** {report['fired']} alarm "
            f"firings across {report['incidents']} incidents, and no alarm stood out as noise."
        )
    embeds = [
        {
            "title": f"{alarm['alarm']} · noise {alarm['noise']}/100",
            "color": 0xF5A524,
            "fields": [
                {"name": "Fired", "value": str(alarm["fired"]), "inline": True},
                {
                    "name": "Led to action",
                    "value": f"{alarm['acted_on']} of {alarm['incidents']}",
                    "inline": True,
                },
                {
                    "name": "Cleared by itself",
                    "value": str(alarm["cleared_by_itself"]),
                    "inline": True,
                },
                {"name": "Suggestion", "value": alarm["suggestion"][:1000]},
            ],
        }
        for alarm in noisy
    ]
    return {"content": headline, "embeds": embeds}


def _post(settings: Settings, report: dict[str, Any]) -> None:
    hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
    if hook is None:
        return
    try:
        hook.send(message(report))
    except DiscordError as exc:  # the report is stored either way
        emit("WARNING", "could not post the alert digest", error=str(exc)[:200])
