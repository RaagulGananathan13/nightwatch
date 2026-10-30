"""Lambda entry point for ``/nw update`` (spec F13): draft the updates for one incident.

The interactions Lambda answers Discord at once ("NightWatch is thinking...") and starts this.
It writes both drafts (one LLM call, or rules), keeps them in the table for 30 days, and
replaces the "thinking" reply with them and two buttons: Publish to status page, and Discard.
It runs under the investigator's read-only role: it reads the incident and writes only to
NightWatch's own table.
"""

from __future__ import annotations

from typing import Any

from nightwatch.agent.guard import redact
from nightwatch.agent.providers import build_chain
from nightwatch.agent.providers.base import LLMError, LLMUnavailableError
from nightwatch.agent.providers.chain import DailyBudget
from nightwatch.agent.services import service_info
from nightwatch.chat.cards import drafts_message
from nightwatch.chat.discord import DiscordError, InteractionReply
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.ids import new_ulid
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import to_iso, utcnow
from nightwatch.status.drafts import INSTRUCTIONS, facts, public_name, write

_secrets = SecretCache()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    reply = InteractionReply(str(event["application_id"]), str(event["token"]))
    store = IncidentStore(settings.table_name, settings.tenant_id)
    incident = store.get_incident(str(event["incident_id"]))
    if incident is None:
        reply.edit({"content": "NightWatch can't find that incident any more."})
        return {"drafted": False}
    service = service_info(incident.service)
    drafts = write(incident, service, _ask_model(settings, facts(incident, service)))
    now = utcnow()
    draft = {
        "id": new_ulid(now),
        "incident_id": incident.id,
        "label": incident.label,
        "service": service.name,
        "public_name": public_name(service),
        "customer": drafts.customer,
        "leadership": drafts.leadership,
        "state": drafts.state,
        "method": drafts.method,
        "model": drafts.model,
        "notes": list(drafts.notes),
        "requested_by": str(event.get("user", "")),
        "created_at": to_iso(now),
    }
    store.save_draft(draft, now)
    AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-updates").record(
        "updates_drafted",
        incident.id,
        draft_id=draft["id"],
        method=drafts.method,
        notes=list(drafts.notes),
    )
    try:
        reply.edit(drafts_message(draft))
    except DiscordError as exc:  # the 15-minute token ran out, or Discord is down
        emit("WARNING", "could not deliver the drafts", error=str(exc)[:200])
    return {"draft_id": draft["id"], "method": drafts.method}


def _ask_model(settings: Settings, text: str) -> tuple[str, str] | None:
    """One LLM call: its text and model name. None (so rules write) on any problem."""
    chain = build_chain(
        settings.llm_providers,
        _secrets.get,
        {"groq": settings.groq_key_param, "gemini": settings.gemini_key_param},
        DailyBudget(settings.table_name, settings.tenant_id, settings.llm_daily_requests),
    )
    if chain is None:
        return None
    messages = [
        {"role": "system", "content": INSTRUCTIONS},
        {"role": "user", "content": redact(text)},
    ]
    try:
        response = chain.complete(messages, max_tokens=700)
    except (LLMError, LLMUnavailableError) as exc:
        emit("WARNING", "updates fell back to rules", error=str(exc)[:200])
        return None
    return response.text, response.model
