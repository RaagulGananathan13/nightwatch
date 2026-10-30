"""Lambda entry point for ``/nw ask`` (spec F9): investigate a question, with no incident.

It is the same agent and the same read-only tools as an incident investigation, under the same
read-only role. Instead of an incident's alarms it starts from the service's main metrics, and
the question goes to the model inside <question> tags, as data. The answer replaces the
"thinking..." reply in Discord.
"""

from __future__ import annotations

import time
from typing import Any

from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.handler import github_get
from nightwatch.agent.loop import Budget, Investigator
from nightwatch.agent.providers import build_chain
from nightwatch.agent.providers.chain import DailyBudget
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext
from nightwatch.chat.cards import diagnosis_embed
from nightwatch.chat.discord import DiscordError, InteractionReply
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.ids import new_ulid
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow

_secrets = SecretCache()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    question = str(event["question"])[:300]
    reply = InteractionReply(str(event["application_id"]), str(event["token"]))
    now = utcnow()
    ask_id = new_ulid(now)
    service = service_info(str(event["service"]))
    store = IncidentStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-ask")
    # Not stored: it only gives the tools a service, a time and a title to work from.
    incident = Incident(
        id=ask_id,
        tenant_id=settings.tenant_id,
        service=service.name,
        title=question,
        first_alarm="",
        opened_at=now,
        updated_at=now,
        last_alert_at=now,
        alert_count=0,
    )
    ctx = ToolContext(
        incident=incident,
        service=service,
        now=now,
        store=store,
        sleep=time.sleep,
        http_get=github_get,
        question=question,
    )
    llm = build_chain(
        settings.llm_providers,
        _secrets.get,
        {"groq": settings.groq_key_param, "gemini": settings.gemini_key_param},
        DailyBudget(settings.table_name, settings.tenant_id, settings.llm_daily_requests),
    )
    try:
        result = Investigator(
            ctx, EvidenceLedger(store, ask_id), llm, audit, Budget(seconds=60)
        ).run()
    except Exception as exc:
        reply.edit({"content": f"Sorry, I couldn't answer that: {type(exc).__name__}."})
        raise
    answer = diagnosis_embed(result.model_dump(mode="json"))
    answer["title"] = "💬 " + answer["title"].removeprefix("🔍 ").replace("Diagnosis", "Answer")
    try:
        reply.edit(
            {
                "content": f"**{event.get('user', 'Someone')} asked:** {question}",
                "embeds": [answer],
            }
        )
    except DiscordError as exc:  # the 15-minute token ran out, or Discord is down
        emit("WARNING", "could not deliver the answer", error=str(exc)[:200])
    audit.record(
        "question_answered",
        ask_id,
        service=service.name,
        method=result.method,
        llm_calls=result.llm_calls,
        tokens=result.tokens,
    )
    return {"ask_id": ask_id, "method": result.method}
