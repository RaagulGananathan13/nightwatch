"""Group alerts into incidents: one active incident per service (ADR 0010).

- A firing alert joins the service's open incident, however many alarms are involved.
- When every alarm on the incident has recovered, the incident resolves.
- An alarm that fires again within the flap window reopens that incident instead of opening a
  new one, so a flapping alarm can't spam the channel.
- An incident with no new alert for ``stale_after`` stops collecting alerts.

Changes are optimistic: read the service pointer, decide, write with a version check. If
another Lambda got there first, read again and decide again.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from nightwatch.common.config import Settings
from nightwatch.common.incidents import ConflictError, DuplicateAlertError, IncidentStore, Pointer
from nightwatch.common.models import Alert, Incident

MAX_ATTEMPTS = 5

OutcomeKind = Literal[
    "opened", "grouped", "reopened", "recovering", "resolved", "duplicate", "ignored"
]


@dataclass(frozen=True)
class Outcome:
    kind: OutcomeKind
    incident: Incident | None = None
    detail: str = ""


def correlate(store: IncidentStore, alert: Alert, settings: Settings, now: datetime) -> Outcome:
    for _ in range(MAX_ATTEMPTS):
        pointer = store.get_pointer(alert.service)
        try:
            if alert.state == "ALARM":
                return _on_alarm(store, alert, pointer, settings, now)
            return _on_recovery(store, alert, pointer, now)
        except ConflictError:
            continue  # someone else changed this service's incident; look again
        except DuplicateAlertError:
            incident = store.get_incident(pointer.incident_id) if pointer else None
            return Outcome("duplicate", incident, "alert was already recorded")
    raise RuntimeError(f"could not settle {alert.service} after {MAX_ATTEMPTS} attempts")


def accepts(pointer: Pointer | None, now: datetime, settings: Settings) -> bool:
    """Should a new firing alert join the pointer's incident?"""
    if pointer is None:
        return False
    if pointer.status == "open":
        return now - pointer.last_alert_at <= settings.stale_after
    return pointer.resolved_at is not None and now - pointer.resolved_at <= settings.flap_window


def _on_alarm(
    store: IncidentStore, alert: Alert, pointer: Pointer | None, settings: Settings, now: datetime
) -> Outcome:
    if pointer is not None and accepts(pointer, now, settings):
        incident = store.get_incident(pointer.incident_id)
        if incident is not None:
            updated = store.attach(pointer, incident, alert, now)
            return Outcome("reopened" if pointer.status == "resolved" else "grouped", updated)
    return Outcome("opened", store.open_incident(alert, replaces=pointer, now=now))


def _on_recovery(
    store: IncidentStore, alert: Alert, pointer: Pointer | None, now: datetime
) -> Outcome:
    if pointer is None or pointer.status != "open":
        return Outcome("ignored", detail="no open incident for this service")
    incident = store.get_incident(pointer.incident_id)
    if incident is None or alert.alarm_name not in incident.alarms:
        return Outcome("ignored", detail="alarm is not part of the open incident")
    updated = store.attach(pointer, incident, alert, now)
    return Outcome("resolved" if updated.status == "resolved" else "recovering", updated)
