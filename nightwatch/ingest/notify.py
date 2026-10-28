"""Keep exactly one Discord message per incident up to date.

The Lambda that opens an incident posts the message. Later alerts edit it in place: "3 alerts
grouped", then "resolved". Notification failures are logged and audited but never raised:
the incident is already safely stored, and a retry would only process the alert twice.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.chat.cards import incident_message
from nightwatch.chat.discord import DiscordError, DiscordWebhook
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident
from nightwatch.ingest.correlate import Outcome


def notify(
    outcome: Outcome,
    store: IncidentStore,
    audit: AuditLog,
    webhook: Callable[[], DiscordWebhook | None],
    now: datetime,
) -> None:
    incident = outcome.incident
    if incident is None or outcome.kind in ("duplicate", "ignored"):
        return
    try:
        hook = webhook()
        if hook is None:
            return  # notifications are switched off
        if outcome.kind == "opened":
            message_id = hook.send(incident_message(incident, now))
            store.set_discord_message(incident.id, message_id)
            audit.record("notification_sent", incident.id, message_id=message_id)
            # Alerts that arrived while we were posting: show them straight away.
            latest = store.get_incident(incident.id)
            if latest is not None and _changed(latest, incident):
                hook.edit(message_id, incident_message(latest, now))
        elif incident.discord_message_id:
            hook.edit(incident.discord_message_id, incident_message(incident, now))
            audit.record("notification_updated", incident.id, change=outcome.kind)
    except (DiscordError, BotoCoreError, ClientError, ValueError, KeyError) as exc:
        emit("ERROR", "notification failed", incident=incident.label, error=str(exc)[:200])
        audit.record("notification_failed", incident.id, error=str(exc)[:200])


def _changed(latest: Incident, shown: Incident) -> bool:
    keys = ("alert_count", "status", "alarms", "severity", "number")
    return any(getattr(latest, key) != getattr(shown, key) for key in keys)
