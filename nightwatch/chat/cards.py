"""What an incident looks like in chat. One message per incident, edited as things change."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nightwatch.common.models import Incident
from nightwatch.common.timeutil import to_iso

_RED = 0xE5484D
_GREEN = 0x30A46C
_BLUE = 0x3E63DD
_GREY = 0x8B8D98


def incident_message(incident: Incident, now: datetime) -> dict[str, Any]:
    """A Discord message describing the incident right now: status, then the diagnosis."""
    label, service = incident.label, _clean(incident.service)
    resolved = incident.status == "resolved"
    result = incident.investigation_result if incident.investigation == "done" else None
    top = _top_hypothesis(result)

    if resolved:
        took = duration((incident.resolved_at or now) - incident.opened_at)
        count = len(incident.alarms)
        recovered = "its alarm recovered" if count == 1 else f"all {count} alarms recovered"
        content = f"✅ **{label} resolved** for **{service}** after {took}: {recovered}."
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
    if top and not resolved:
        content = f"🔍 **{label}** for **{service}**: likely cause: {_clean(top)}"

    alarm_lines = [
        f"{'🔴' if state == 'ALARM' else '🟢'} `{_clean(name)}`"
        for name, state in sorted(incident.alarms.items())
    ]
    fields = [
        {"name": "Status", "value": "Resolved" if resolved else "Open", "inline": True},
        {"name": "Severity", "value": incident.severity.title(), "inline": True},
        {"name": "Alerts", "value": _alerts(incident.alert_count), "inline": True},
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
    embeds: list[dict[str, Any]] = [embed]
    if result:
        embeds.append(diagnosis_embed(result))
    elif incident.investigation == "failed":
        reason = str((incident.investigation_result or {}).get("error", "unknown error"))
        embeds.append(
            {
                "title": "Investigation failed",
                "description": _limit(_clean(reason), 400),
                "color": _GREY,
            }
        )
    return {"content": _limit(content, 2000), "embeds": embeds}


def diagnosis_embed(result: dict[str, Any]) -> dict[str, Any]:
    """The investigation result (nightwatch.agent.models.Investigation) as an embed."""
    diagnosis = result.get("diagnosis") or {}
    lines = [_clean(str(diagnosis.get("summary", "")))]
    for number, hypothesis in enumerate((diagnosis.get("hypotheses") or [])[:3], start=1):
        refs = " ".join(f"[{ident}]" for ident in hypothesis.get("evidence_ids", []))
        confidence = round(float(hypothesis.get("confidence", 0)) * 100)
        flag = "" if hypothesis.get("verified", True) else "⚠️ *unverified:* "
        lines.append(
            f"**{number}.** {flag}{_clean(str(hypothesis.get('statement', '')))} "
            f"({confidence}%) {refs}".rstrip()
        )
    actions = [
        f"• {_clean(str(a.get('description', '')))} ({a.get('risk', 'medium')} risk)"
        for a in (diagnosis.get("suggested_actions") or [])[:3]
    ]
    unknowns = [f"• {_clean(str(u))}" for u in (diagnosis.get("unknowns") or [])[:3]]
    evidence = [
        f"`{e.get('id')}` {_clean(str(e.get('brief', '')))}" for e in result.get("evidence") or []
    ]
    fields = [
        {"name": "Impact", "value": _limit(_clean(str(diagnosis.get("impact") or "-")), 512)},
        {"name": "Next steps", "value": _limit("\n".join(actions) or "-", 700)},
        {"name": "Not verified", "value": _limit("\n".join(unknowns) or "-", 500)},
        {"name": "Evidence", "value": _limit("\n".join(evidence) or "-", 1000)},
    ]
    if result.get("method") == "llm":
        footer = (
            f"{result.get('model')} via {result.get('provider')} · {result.get('llm_calls')} "
            f"LLM call(s) · {int(result.get('tokens') or 0):,} tokens · "
            f"{int(result.get('duration_ms') or 0) / 1000:.0f}s · {result.get('prompt_version')}"
        )
    else:
        footer = f"Rule-based: {result.get('fallback_reason') or 'no LLM used'}"
    return {
        "title": "🔍 Diagnosis" + (" (rule-based)" if result.get("method") == "rules" else ""),
        "description": _limit("\n\n".join(line for line in lines if line), 1800),
        "color": _BLUE,
        "fields": fields,
        "footer": {"text": _limit(footer, 300)},
    }


def _top_hypothesis(result: dict[str, Any] | None) -> str | None:
    hypotheses = ((result or {}).get("diagnosis") or {}).get("hypotheses") or []
    if not hypotheses:
        return None
    statement = str(hypotheses[0].get("statement", ""))
    return _limit(statement, 200) if statement else None


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


def _alerts(count: int) -> str:
    return "1 alert" if count == 1 else f"{count} grouped"


def _clean(text: str) -> str:
    """Keep text that came from alarms from breaking our markdown."""
    return text.replace("`", "'").replace("*", "").replace("\n", " ")


def _limit(text: str, size: int) -> str:
    return text if len(text) <= size else text[: size - 1] + "…"
