"""Lambda entry point for the "Investigate" step of the incident workflow (spec 10.1).

Runs as the read-only investigator role: it can read the service's logs, metrics and
configuration, and write only to NightWatch's own table. After the diagnosis it may propose
one fix from a runbook; proposing changes nothing, a human approves it later (Phase 4).
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.loop import Budget, Investigator
from nightwatch.agent.models import Evidence, Investigation
from nightwatch.agent.providers import build_chain
from nightwatch.agent.providers.chain import DailyBudget
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.actions import Action, ActionStore
from nightwatch.remediation.calls import CallError, describe
from nightwatch.remediation.planner import plan

_secrets = SecretCache()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    incident_id = str(event["incident_id"])
    store = IncidentStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-investigate")
    incident = store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"incident {incident_id} not found")

    store.set_investigation(incident_id, "running")
    audit.record("investigation_started", incident_id)
    ctx = ToolContext(
        incident=incident,
        service=service_info(incident.service),
        now=utcnow(),
        store=store,
        sleep=time.sleep,
        http_get=github_get,
    )
    llm = build_chain(
        settings.llm_providers,
        _secrets.get,
        {"groq": settings.groq_key_param, "gemini": settings.gemini_key_param},
        DailyBudget(settings.table_name, settings.tenant_id, settings.llm_daily_requests),
    )
    ledger = EvidenceLedger(store, incident_id)
    result = Investigator(
        ctx,
        ledger,
        llm,
        audit,
        Budget(seconds=settings.investigation_seconds),
    ).run()

    store.set_investigation(incident_id, "done", result.model_dump(mode="json"))
    top = result.diagnosis.hypotheses[0]
    audit.record(
        "investigation_completed",
        incident_id,
        method=result.method,
        provider=result.provider,
        model=result.model,
        llm_calls=result.llm_calls,
        tokens=result.tokens,
        evidence=len(result.evidence),
        duration_ms=result.duration_ms,
        fallback_reason=result.fallback_reason,
        top_hypothesis_verified=top.verified,
    )
    emit(
        "INFO",
        "investigation completed",
        incident=incident.label,
        method=result.method,
        model=result.model,
        tokens=result.tokens,
        seconds=round(result.duration_ms / 1000, 1),
    )
    action = propose_fix(
        ctx, ledger.items, result, ActionStore(settings.table_name, settings.tenant_id), audit
    )
    return {"incident_id": incident_id, "method": result.method, "action": action}


def propose_fix(
    ctx: ToolContext,
    evidence: list[Evidence],
    result: Investigation,
    actions: ActionStore,
    audit: AuditLog,
) -> dict[str, Any] | None:
    """Store a proposed fix if a runbook matches the evidence. Never fails the investigation."""
    incident = ctx.incident
    try:
        proposal = plan(evidence, result.diagnosis, ctx.service, incident.opened_at, ctx.aws)
    except (BotoCoreError, ClientError, CallError, KeyError, ValueError) as exc:
        audit.record("remediation_planning_failed", incident.id, error=f"{type(exc).__name__}")
        emit("WARNING", "could not plan a fix", incident=incident.label, error=str(exc)[:200])
        return None
    if proposal is None:
        return None
    action = actions.create(Action.from_proposal(proposal, incident.id, incident.service, utcnow()))
    audit.record(
        "remediation_proposed",
        incident.id,
        action_id=action.id,
        runbook=action.runbook_id,
        preview=action.preview,
        reason=action.reason,
        call=describe(action.call),
    )
    return {"action_id": action.id, "auto_approve": action.auto_approve}


def github_get(url: str) -> dict[str, Any] | None:
    """A best-effort GitHub API read (public repos need no token); None on any problem."""
    if not url.startswith("https://api.github.com/"):
        return None
    request = urllib.request.Request(  # noqa: S310 - fixed https host checked above
        url,
        headers={"Accept": "application/vnd.github+json", "User-Agent": "NightWatch"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310  # nosec B310
            body: dict[str, Any] = json.loads(response.read())
            return body
    except (urllib.error.URLError, TimeoutError, ValueError):
        return None
