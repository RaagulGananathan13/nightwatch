"""What an incident looks like in chat. One message per incident, edited as things change."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nightwatch.common.models import Incident
from nightwatch.common.timeutil import to_iso

_RED = 0xE5484D
_GREEN = 0x30A46C


def incident_message(incident: Incident, now: datetime) -> dict[str, Any]:
    """A Discord message (content plus one embed) describing the incident right now."""
    label, service = incident.label, _clean(incident.service)
    resolved = incident.status == "resolved"

    if resolved:
        took = duration((incident.resolved_at or now) - incident.opened_at)
        content = (
            f"✅ **{label} resolved** for **{service}** after {took}: "
            f"all {len(incident.alarms)} alarms recovered."
        )
    elif incident.alert_count == 1:
        content = (
            f"🔴 **{label} opened** for **{service}**: "
            f"alarm `{_clean(incident.first_alarm)}` fired."
        )
    else:
        content = (
            f"🔴 **{label}** for **{service}**: "
            f"{incident.alert_count} alerts grouped into 1 incident."
        )

    alarm_lines = [
        f"{'🔴' if state == 'ALARM' else '🟢'} `{_clean(name)}`"
        for name, state in sorted(incident.alarms.items())
    ]
    fields = [
        {"name": "Status", "value": "Resolved" if resolved else "Open", "inline": True},
        {"name": "Severity", "value": incident.severity.title(), "inline": True},
        {"name": "Alerts", "value": f"{incident.alert_count} grouped", "inline": True},
        {"name": "Alarms", "value": _limit("\n".join(alarm_lines) or "-", 1024), "inline": False},
        {"name": "Opened", "value": f"<t:{int(incident.opened_at.timestamp())}:R>", "inline": True},
    ]
    if incident.reopen_count:
        fields.append(
            {"name": "Reopened", "value": f"{incident.reopen_count}x (flapping)", "inline": True}
        )
    embed = {
        "title": _limit(f"{label} · {service}", 256),
        "description": _limit(_clean(incident.title), 400),
        "color": _GREEN if resolved else _RED,
        "fields": fields,
        "footer": {"text": f"NightWatch · {incident.id}"},
        "timestamp": to_iso(incident.opened_at),
    }
    return {"content": _limit(content, 2000), "embeds": [embed]}


def duration(span: timedelta) -> str:
    """Short human duration: 45s, 6m 20s, 1h 5m."""
    seconds = max(0, int(span.total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _clean(text: str) -> str:
    """Keep text that came from alarms from breaking our markdown."""
    return text.replace("`", "'").replace("*", "").replace("\n", " ")


def _limit(text: str, size: int) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"
