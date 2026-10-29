"""Lambda entry point for the "Investigate" step of the incident workflow (spec 10.1).

Runs as the read-only investigator role: it can read the service's logs, metrics and
configuration, and write only to NightWatch's own table.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.loop import Budget, Investigator
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
    result = Investigator(
        ctx,
        EvidenceLedger(store, incident_id),
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
    return {"incident_id": incident_id, "method": result.method}


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
