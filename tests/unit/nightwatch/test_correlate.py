"""Correlation rules (ADR 0010): one active incident per service, safe under concurrency."""

from datetime import timedelta

import pytest

from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore, Pointer
from nightwatch.ingest.correlate import correlate
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert

SETTINGS = Settings(table_name=TABLE)


def process(store: IncidentStore, alert, minutes_later: float = 0):
    return correlate(store, alert, SETTINGS, alert.occurred_at + timedelta(minutes=minutes_later))


def test_first_alarm_opens_incident_001(store: IncidentStore) -> None:
    outcome = process(store, make_alert())
    assert outcome.kind == "opened"
    incident = store.assign_number(outcome.incident)
    assert incident.label == "INC-001"
    assert incident.status == "open"
    assert incident.alarms == {"shoplite-5xx-rate": "ALARM"}
    assert [i.id for i in store.list_incidents("open")] == [incident.id]


def test_more_alarms_on_the_same_service_are_grouped(store: IncidentStore) -> None:
    first = process(store, make_alert("shoplite-5xx-rate")).incident
    second = process(store, make_alert("shoplite-lambda-errors", severity="high"))
    assert second.kind == "grouped"
    assert second.incident.id == first.id
    assert second.incident.alert_count == 2
    assert second.incident.severity == "high"  # the worst alert sets the severity
    assert len(store.list_incidents("open")) == 1
    assert len(store.alerts(first.id)) == 2


def test_other_services_get_their_own_incident(store: IncidentStore) -> None:
    process(store, make_alert(service="shoplite"))
    other = process(store, make_alert("billing-errors", service="billing"))
    assert other.kind == "opened"
    assert len(store.list_incidents("open")) == 2


def test_two_alarms_at_the_same_moment_still_make_one_incident(store: IncidentStore) -> None:
    """Simulate the race: the second Lambda read the pointer before the first one wrote it."""
    process(store, make_alert("shoplite-5xx-rate"))

    class LateReader(IncidentStore):
        reads = 0

        def get_pointer(self, service: str) -> Pointer | None:
            self.reads += 1
            return None if self.reads == 1 else super().get_pointer(service)

    racer = LateReader(TABLE, "default")
    outcome = process(racer, make_alert("shoplite-lambda-errors"))
    assert outcome.kind == "grouped"  # its open attempt failed, so it retried and joined
    assert racer.reads == 2
    assert len(store.list_incidents("open")) == 1


def test_the_same_alert_delivered_twice_is_stored_once(store: IncidentStore) -> None:
    alert = make_alert()
    process(store, alert)
    again = process(store, alert)
    assert again.kind == "duplicate"
    assert again.incident.alert_count == 1
    assert len(store.alerts(again.incident.id)) == 1


def test_incident_resolves_when_every_alarm_recovers(store: IncidentStore) -> None:
    process(store, make_alert("shoplite-5xx-rate"))
    process(store, make_alert("shoplite-lambda-errors"))

    first_ok = process(store, make_alert("shoplite-5xx-rate", "OK", at=T0 + timedelta(minutes=3)))
    assert first_ok.kind == "recovering"
    assert first_ok.incident.status == "open"

    last_ok = process(
        store, make_alert("shoplite-lambda-errors", "OK", at=T0 + timedelta(minutes=4))
    )
    assert last_ok.kind == "resolved"
    assert last_ok.incident.status == "resolved"
    assert last_ok.incident.resolved_at is not None
    assert store.list_incidents("open") == []
    assert [i.id for i in store.list_incidents("resolved")] == [last_ok.incident.id]
    pointer = store.get_pointer("shoplite")
    assert pointer is not None
    assert pointer.status == "resolved"


def test_flapping_alarm_reopens_the_same_incident(store: IncidentStore) -> None:
    opened = process(store, make_alert()).incident
    process(store, make_alert(state="OK", at=T0 + timedelta(minutes=2)))
    again = process(store, make_alert(at=T0 + timedelta(minutes=8)))  # 6 min after resolving
    assert again.kind == "reopened"
    assert again.incident.id == opened.id
    assert again.incident.status == "open"
    assert again.incident.reopen_count == 1
    assert again.incident.resolved_at is None


def test_alarm_long_after_resolution_opens_a_new_numbered_incident(store: IncidentStore) -> None:
    first = store.assign_number(process(store, make_alert()).incident)
    process(store, make_alert(state="OK", at=T0 + timedelta(minutes=2)))
    later = process(store, make_alert(at=T0 + timedelta(minutes=30)))
    assert later.kind == "opened"
    second = store.assign_number(later.incident)
    assert (first.label, second.label) == ("INC-001", "INC-002")  # no gaps in numbering


def test_stale_open_incident_stops_collecting_alerts(store: IncidentStore) -> None:
    old = process(store, make_alert()).incident
    fresh = process(store, make_alert(at=T0 + timedelta(hours=7)))
    assert fresh.kind == "opened"
    assert fresh.incident.id != old.id


@pytest.mark.parametrize("alarm", ["shoplite-5xx-rate", "shoplite-unrelated"])
def test_recoveries_without_a_matching_open_incident_are_ignored(
    store: IncidentStore, alarm: str
) -> None:
    assert process(store, make_alert(alarm, "OK")).kind == "ignored"  # nothing open yet
    process(store, make_alert("shoplite-5xx-rate"))
    outcome = process(store, make_alert("shoplite-unrelated", "OK"))
    assert outcome.kind == "ignored"


def test_numbering_is_idempotent(store: IncidentStore) -> None:
    incident = store.assign_number(process(store, make_alert()).incident)
    assert store.assign_number(incident).number == 1
    fresh = store.get_incident(incident.id)
    assert fresh is not None
    assert store.assign_number(fresh).number == 1
