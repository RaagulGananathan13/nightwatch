"""Lambda entry point for the "Report" step: put the diagnosis on the incident's Discord message.

The diagnosis is added to the same message that announced the incident, so the channel keeps
one living message per incident. If the investigation failed, the message says so instead.
"""

from __future__ import annotations

from typing import Any

from nightwatch.chat.cards import incident_message
from nightwatch.chat.discord import webhook_from_ssm
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
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-report")

    failure = event.get("error")
    if failure:
        reason = _reason(failure)
        store.set_investigation(incident_id, "failed", {"error": reason})
        audit.record("investigation_failed", incident_id, error=reason)

    incident = store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"incident {incident_id} not found")
    hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
    if hook is None:
        return {"incident_id": incident_id, "posted": False}

    message = incident_message(incident, utcnow())
    if incident.discord_message_id:
        hook.edit(incident.discord_message_id, message)
    else:  # the first post failed earlier; post now
        store.set_discord_message(incident_id, hook.send(message))
    event_name = "investigation_failure_posted" if failure else "diagnosis_posted"
    audit.record(event_name, incident_id)
    emit("INFO", event_name.replace("_", " "), incident=incident.label)
    return {"incident_id": incident_id, "posted": True}


def _reason(failure: Any) -> str:
    """A short reason from a Step Functions error object: {"Error": ..., "Cause": ...}."""
    if isinstance(failure, dict):
        return f"{failure.get('Error', 'error')}: {str(failure.get('Cause', ''))[:200]}"
    return str(failure)[:250]
