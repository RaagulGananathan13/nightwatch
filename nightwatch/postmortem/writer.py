"""Write a blameless postmortem in markdown (spec F8).

Everything factual comes from records, not from a model:
- the timeline, from the audit log;
- the impact, from the incident and its evidence;
- the root cause, from the guard-checked diagnosis;
- the fix, from the action record.

Only the lessons (what went well, what didn't, action items) may be drafted by an LLM, and
the document says so. When no LLM is available, fixed rules write them instead.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from nightwatch.agent.models import Evidence
from nightwatch.chat.cards import duration
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import from_iso
from nightwatch.remediation.actions import Action

# Audit events worth a timeline row, and how to say them.
_TIMELINE = {
    "incident_opened": "Alarm {alarm} fired and the incident opened",
    "alert_grouped": "Alarm {alarm} fired and joined the incident",
    "incident_reopened": "Alarm {alarm} fired again and reopened the incident",
    "notification_sent": "Posted to Discord",
    "investigation_started": "Investigation started",
    "investigation_completed": "Investigation finished ({method}, {llm_calls} LLM call(s))",
    "diagnosis_posted": "Diagnosis posted to Discord",
    "remediation_proposed": "Fix proposed: {preview}",
    "approval_requested": "Approval requested",
    "approval_decided": "Fix {decided} by {user}",
    "approval_expired": "Nobody approved the fix in time; approvers pinged",
    "remediation_blocked": "Fix blocked: {why}",
    "remediation_executed": "Fix ran: {call}",
    "remediation_verified": "Fix checked: {verdict}",
    "remediation_reverted": "Fix undone automatically: {call}",
    "incident_acknowledged": "Acknowledged by {user}",
    "alarm_recovered": "Alarm {alarm} recovered",
    "incident_resolved": "Alarm {alarm} recovered; all alarms OK, incident resolved",
}
_ACTION_ITEMS = {
    "rollback-lambda-alias": "Add a test for the code path that failed, so CI stops the next "
    "bad deploy before it ships",
    "reset-feature-flags": "Require review for production flag changes, and record why each "
    "change was made",
    "raise-dynamodb-capacity": "Alert on capacity decreases, and change capacity only through "
    "reviewed Terraform",
    "reset-lambda-concurrency": "Alert when a function's reserved concurrency changes",
}


@dataclass
class Facts:
    incident: Incident
    events: list[dict[str, Any]]  # audit entries, oldest first
    actions: list[Action]
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class Lessons:
    went_well: list[str]
    went_badly: list[str]
    action_items: list[str]
    written_by: str  # a model name, or "rules"


def timeline(facts: Facts) -> list[tuple[datetime, str]]:
    rows: list[tuple[datetime, str]] = []
    for entry in facts.events:
        template = _TIMELINE.get(entry["event"])
        if not template:
            continue
        details = {k: str(v) for k, v in (entry.get("details") or {}).items()}
        details.setdefault("user", details.get("user_id", "someone"))
        details["decided"] = "approved" if details.get("decision") == "approve" else "rejected"
        text = template.format_map(_Blank(details))
        rows.append((from_iso(entry["at"]), _clean(text)))
    return rows


def rule_lessons(facts: Facts) -> Lessons:
    """Lessons from fixed rules, when no LLM writes them."""
    incident, action = facts.incident, _action(facts)
    result = incident.investigation_result or {}
    top = ((result.get("diagnosis") or {}).get("hypotheses") or [{}])[0]
    posted = _first(facts, "diagnosis_posted")
    well, badly, items = [], [], []
    if posted:
        seconds = int((posted - incident.opened_at).total_seconds())
        well.append(f"A diagnosis reached Discord {duration_text(seconds)} after the alarm")
    if top.get("verified"):
        well.append("Every fact the diagnosis cited was found in the gathered evidence")
    elif top:
        badly.append("The top hypothesis could not be verified against the evidence")
    if action and action.status == "verified":
        well.append(f"The fix ({action.title.lower()}) ran and verification passed")
    elif action and action.status in ("not_fixed", "reverted", "failed", "revert_failed"):
        badly.append(f"The fix ({action.title.lower()}) did not hold: {action.status}")
    elif not action:
        badly.append("No runbook matched, so NightWatch could not propose a fix")
    if incident.reopen_count:
        badly.append(f"The alarms flapped: the incident reopened {incident.reopen_count} time(s)")
    items.append(
        _ACTION_ITEMS.get(action.runbook_id if action else "", "")
        or "Write a runbook for this failure, so NightWatch can propose a fix next time"
    )
    return Lessons(well or ["The alarm fired and opened one incident"], badly, items, "rules")


def lessons_prompt(facts: Facts) -> str:
    return render(facts, None)


def parse_lessons(text: str, model: str) -> Lessons | None:
    """The model's JSON reply, or None if it isn't usable."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        raw = json.loads(match.group(0)) if match else None
    except ValueError:
        return None
    if not isinstance(raw, dict):
        return None

    def items(key: str) -> list[str]:
        value = raw.get(key)
        return [_clean(str(v))[:300] for v in value[:4]] if isinstance(value, list) else []

    lessons = Lessons(items("went_well"), items("went_badly"), items("action_items"), model)
    return lessons if lessons.action_items else None


def render(facts: Facts, lessons: Lessons | None) -> str:
    """The postmortem. Without lessons it's the factual part, which is also the LLM's input."""
    incident, action = facts.incident, _action(facts)
    result = incident.investigation_result or {}
    diagnosis = result.get("diagnosis") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    end = incident.resolved_at or incident.updated_at
    posted = _first(facts, "diagnosis_posted")
    lines = [
        f"# {incident.label}: {_clean(incident.title)}",
        "",
        "*A blameless postmortem written by NightWatch. Every fact comes from the incident's "
        "alerts, evidence and audit log.*",
        "",
        "| | |",
        "|---|---|",
        f"| Service | {incident.service} |",
        f"| Severity | {incident.severity} |",
        f"| Opened | {incident.opened_at:%Y-%m-%d %H:%M:%S} UTC |",
        f"| Resolved | {end:%Y-%m-%d %H:%M:%S} UTC, after {duration(end - incident.opened_at)} |",
        f"| Alarms | {', '.join(sorted(incident.alarms))} |",
    ]
    if posted:
        seconds = int((posted - incident.opened_at).total_seconds())
        lines.append(f"| Alarm to diagnosis | {duration_text(seconds)} |")
    if incident.acked_by_name:
        lines.append(f"| Acknowledged by | {_clean(incident.acked_by_name)} |")
    lines += ["", "## Summary", "", _clean(str(diagnosis.get("summary") or "Not diagnosed.")), ""]
    lines += ["## Impact", ""]
    lines.append(
        f"- {incident.service} was affected for {duration(end - incident.opened_at)}, from the "
        "first alarm to the last recovery."
    )
    lines.append(f"- {incident.alert_count} alert(s) grouped into this one incident.")
    errors = _errors_logged(facts)
    if errors:
        lines.append(f"- {errors[0]:g} errors logged ({errors[1]}).")
    if diagnosis.get("impact"):
        lines.append(f"- {_clean(str(diagnosis['impact']))}")
    lines += ["", "## Root cause", ""]
    if hypotheses:
        top = hypotheses[0]
        checked = "verified" if top.get("verified") else "NOT verified"
        cited = ", ".join(top.get("evidence_ids") or []) or "none"
        lines.append(
            f"{_clean(str(top.get('statement')))} (confidence "
            f"{round(float(top.get('confidence', 0)) * 100)}%, {checked} against evidence {cited})"
        )
        if len(hypotheses) > 1:
            lines += ["", "Also considered:"]
            lines += [f"- {_clean(str(h.get('statement')))}" for h in hypotheses[1:3]]
    else:
        lines.append("No diagnosis was made.")
    lines += [
        "",
        "## Timeline (UTC)",
        "",
        "| Time | Since the alarm | What happened |",
        "|---|---|---|",
    ]
    for at, text in timeline(facts):
        since = duration(at - incident.opened_at) if at >= incident.opened_at else "before"
        lines.append(f"| {at:%H:%M:%S} | {since} | {text} |")
    lines += ["", "## Resolution", ""]
    if action and action.executed_at:
        lines.append(
            f"NightWatch ran the {action.runbook_id} runbook ({_clean(action.preview)}), approved "
            f"by {_clean(action.decided_by_name or 'auto-approval')}. It ended as "
            f"'{action.status.replace('_', ' ')}'. "
            f"{_clean(str((action.verification or {}).get('detail', '')))}"
        )
    elif action:
        lines.append(
            f"NightWatch proposed the {action.runbook_id} runbook ({_clean(action.preview)}), but "
            f"it was {action.status.replace('_', ' ')}. The alarms recovered without it."
        )
    else:
        lines.append("The alarms recovered without a fix from NightWatch.")
    similar = result.get("similar") or []
    if similar:
        lines += ["", "## Similar earlier incidents", ""]
        lines += [
            f"- {s.get('label')}, {s.get('when')}: {_clean(str(s.get('cause')))}" for s in similar
        ]
    if lessons is not None:
        lines += ["", "## What went well", ""]
        lines += [f"- {item}" for item in lessons.went_well] or ["- Nothing noted."]
        lines += ["", "## What could have gone better", ""]
        lines += [f"- {item}" for item in lessons.went_badly] or ["- Nothing noted."]
        lines += ["", "## Action items", ""]
        lines += [f"- [ ] {item}" for item in lessons.action_items]
        source = "fixed rules" if lessons.written_by == "rules" else lessons.written_by
        lines += ["", f"*Lessons drafted by {source}. Review them before acting.*"]
    return "\n".join(lines) + "\n"


def duration_text(seconds: int) -> str:
    return f"{seconds} s" if seconds < 90 else duration_minutes(seconds)


def duration_minutes(seconds: int) -> str:
    minutes, rest = divmod(seconds, 60)
    return f"{minutes} min {rest:02d} s"


def _action(facts: Facts) -> Action | None:
    return facts.actions[-1] if facts.actions else None


def _first(facts: Facts, event: str) -> datetime | None:
    return next((from_iso(e["at"]) for e in facts.events if e["event"] == event), None)


def _errors_logged(facts: Facts) -> tuple[float, str] | None:
    for item in facts.evidence:
        if item.label == "errors_by_version" and isinstance(item.data, dict):
            total = (item.data.get("totals") or {}).get("errors")
            if total:
                return float(total), f"evidence {item.id}"
    return None


def _clean(text: str) -> str:
    """Keep table cells on one line and stop pipes breaking the markdown tables."""
    return " ".join(text.replace("|", "/").split())


class _Blank(dict[str, str]):
    """str.format_map that leaves unknown placeholders empty instead of failing."""

    def __missing__(self, key: str) -> str:
        return "?"
