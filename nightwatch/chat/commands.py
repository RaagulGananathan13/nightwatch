"""Slash commands (spec F9, F13): ``/nw status``, ``/nw ack INC-12``, ``/nw ask <question>``
and ``/nw update INC-12``.

Discord sends these to the same endpoint as button clicks, after the same signature check.
``status`` and ``ack`` answer at once. ``ask`` needs an investigation (10 to 60 seconds) and
``update`` an LLM call, so they answer "NightWatch is thinking..." straight away and start a
Lambda that edits that reply when it is done. Only a service's approvers may ``ack``, ``ask``
or ``update`` for it, because those act for the team or spend the LLM budget.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import Any

from nightwatch.agent.services import load_services, service_info
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident

COMMAND = "nw"
MESSAGE, DEFERRED = 4, 5  # response types; DEFERRED shows "NightWatch is thinking..."
EPHEMERAL = 64
_LABEL = re.compile(r"^(?:INC-?)?0*(\d{1,6})$", re.IGNORECASE)


def command(
    interaction: dict[str, Any],
    store: IncidentStore,
    audit: AuditLog,
    now: datetime,
    start_job: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    data = interaction.get("data") or {}
    options = data.get("options") or []
    if data.get("name") != COMMAND or not options:
        return private("NightWatch doesn't know that command.")
    sub = options[0]
    args = {str(o.get("name")): o.get("value") for o in sub.get("options") or []}
    user = (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    user_id = str(user.get("id", ""))
    user_name = str(user.get("global_name") or user.get("username") or "someone")[:80]

    if sub.get("name") == "status":
        return status(store)
    if sub.get("name") == "ack":
        return ack(store, audit, str(args.get("incident", "")), user_id, user_name, now)
    if sub.get("name") == "ask":
        job = {
            "question": str(args.get("question", ""))[:300],
            "service": str(args.get("service") or _only_service()),
            "application_id": str(interaction.get("application_id", "")),
            "token": str(interaction.get("token", "")),
            "user": user_name,
        }
        return ask(job, user_id, audit, start_job)
    if sub.get("name") == "update":
        job = {
            "application_id": str(interaction.get("application_id", "")),
            "token": str(interaction.get("token", "")),
            "user": user_name,
        }
        return update(store, audit, str(args.get("incident", "")), job, user_id, start_job)
    return private("NightWatch doesn't know that command.")


def status(store: IncidentStore) -> dict[str, Any]:
    incidents = store.list_incidents("open", limit=10)
    if not incidents:
        return public("✅ No open incidents.")
    lines = [f"**{len(incidents)} open incident(s):**"]
    for incident in incidents:
        line = (
            f"🔴 **{incident.label}** `{_clean(incident.service)}`: {_clean(incident.title)}, "
            f"opened <t:{int(incident.opened_at.timestamp())}:R>, {incident.alert_count} alert(s)"
        )
        if incident.acked_by_name:
            line += f", taken by {_clean(incident.acked_by_name)}"
        lines.append(line)
    return public("\n".join(lines))


def ack(
    store: IncidentStore,
    audit: AuditLog,
    label: str,
    user_id: str,
    user_name: str,
    now: datetime,
) -> dict[str, Any]:
    match = _LABEL.match(label.strip())
    if not match:
        return private("Give an incident like `INC-12`.")
    number = int(match.group(1))
    incident = _find(store, number, ("open",))
    if incident is None:
        return private(f"INC-{number:03d} isn't open.")
    if user_id not in service_info(incident.service).approvers:
        return private(f"Only {incident.service}'s approvers can take its incidents.")
    if not store.acknowledge(incident.id, user_id, user_name, now):
        return private(f"{incident.label} is already taken or no longer open.")
    audit.record("incident_acknowledged", incident.id, user=user_name, user_id=user_id)
    return public(f"👀 **{incident.label}** is taken by {_clean(user_name)}.")


def ask(
    job: dict[str, Any],
    user_id: str,
    audit: AuditLog,
    start_job: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    service = job["service"]
    if service not in load_services():
        return private(f"NightWatch doesn't watch a service called `{_clean(service)}`.")
    if user_id not in service_info(service).approvers:
        return private(f"Only {service}'s approvers can ask about it.")
    if not job["question"].strip():
        return private("Ask a question, e.g. `/nw ask why is checkout slow?`")
    if not start_job("ask", job):
        return private("Questions are switched off right now.")
    audit.record(
        "question_asked", None, user=job["user"], service=service, question=job["question"]
    )
    return {"type": DEFERRED}


def update(
    store: IncidentStore,
    audit: AuditLog,
    label: str,
    job: dict[str, Any],
    user_id: str,
    start_job: Callable[[str, dict[str, Any]], bool],
) -> dict[str, Any]:
    """Draft a customer update and a leadership summary for an open or resolved incident."""
    match = _LABEL.match(label.strip())
    if not match:
        return private("Give an incident like `INC-12`.")
    number = int(match.group(1))
    incident = _find(store, number, ("open", "resolved"))
    if incident is None:
        return private(f"INC-{number:03d} isn't among the recent incidents.")
    if user_id not in service_info(incident.service).approvers:
        return private(f"Only {incident.service}'s approvers can draft its updates.")
    if not start_job("updates", {**job, "incident_id": incident.id}):
        return private("Status updates are switched off right now.")
    audit.record("updates_requested", incident.id, user=job["user"], user_id=user_id)
    return {"type": DEFERRED}


def public(text: str) -> dict[str, Any]:
    return {"type": MESSAGE, "data": {"content": text[:1900], "allowed_mentions": {"parse": []}}}


def private(text: str) -> dict[str, Any]:
    return {"type": MESSAGE, "data": {"content": text, "flags": EPHEMERAL}}


def _find(store: IncidentStore, number: int, statuses: tuple[str, ...]) -> Incident | None:
    for status in statuses:
        found = next(
            (i for i in store.list_incidents(status, limit=50) if i.number == number), None
        )
        if found:
            return found
    return None


def _only_service() -> str:
    services = list(load_services())
    return services[0] if len(services) == 1 else ""


def _clean(text: str) -> str:
    return text.replace("`", "'").replace("*", "").replace("\n", " ")[:200]
