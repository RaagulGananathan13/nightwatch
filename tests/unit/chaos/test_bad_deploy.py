"""The bad-deploy scenario always cleans up, and its verdict is the Phase 2 acceptance check."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from chaos.bad_deploy import run
from nightwatch.common.models import Incident

START = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)


class FakeTime:
    def __init__(self) -> None:
        self.seconds = 0.0

    def clock(self) -> float:
        return self.seconds

    def sleep(self, seconds: float) -> None:
        self.seconds += seconds

    def now(self) -> datetime:
        return START + timedelta(seconds=self.seconds)


class FakeLab:
    def __init__(self, time: FakeTime, incidents: int = 1, messages: int = 1) -> None:
        self.time = time
        self.flags: dict[str, Any] = {}
        self.flag_history: list[dict[str, Any]] = []
        self.requests = 0
        self.incident_count = incidents
        self.messages = messages
        self.fail_requests = False

    def get_flags(self) -> dict[str, Any]:
        return dict(self.flags)

    def set_flags(self, flags: dict[str, Any]) -> None:
        self.flags = dict(flags)
        self.flag_history.append(dict(flags))

    def request(self, method: str, path: str, body: Any = None) -> int:
        if self.fail_requests:
            raise RuntimeError("network down")
        self.requests += 1
        return 502 if self.flags else 200

    def incidents_since(self, service: str, since: datetime) -> list[Incident]:
        if self.time.seconds < 120:  # alarms take a couple of minutes
            return []
        status = "open" if self.flags else "resolved"
        return [_incident(n, status) for n in range(1, self.incident_count + 1)]

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        return [{"event": "notification_sent"}] * self.messages


def _incident(number: int, status: str) -> Incident:
    return Incident.model_validate(
        {
            "id": f"01JB{number:022d}",
            "tenant_id": "default",
            "number": number,
            "service": "shoplite",
            "title": "shoplite-5xx-rate",
            "first_alarm": "shoplite-5xx-rate",
            "status": status,
            "opened_at": START + timedelta(seconds=95),
            "updated_at": START,
            "last_alert_at": START,
            "alert_count": 2,
            "alarms": {"shoplite-5xx-rate": "ALARM", "shoplite-lambda-errors": "ALARM"},
        }
    )


def scenario(lab: FakeLab, time: FakeTime, lines: list[str]) -> int:
    return run(lab, 0.8, 3, 6, out=lines.append, clock=time.clock, sleep=time.sleep, now=time.now)


def test_pass_with_one_incident_and_one_message() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lines: list[str] = []
    assert scenario(lab, time, lines) == 0
    assert lab.flag_history == [{"error_rate": 0.8}, {}]  # injected, then restored
    assert lab.requests > 60
    assert lines[-1] == "PASS: one incident and one Discord message."


@pytest.mark.parametrize(("incidents", "messages"), [(0, 1), (2, 1), (1, 2)])
def test_fails_on_wrong_counts(incidents: int, messages: int) -> None:
    time = FakeTime()
    lab = FakeLab(time, incidents, messages)
    lines: list[str] = []
    assert scenario(lab, time, lines) == 1
    assert lines[-1].startswith("FAIL")
    assert lab.flags == {}


def test_flags_are_restored_even_when_something_breaks() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lab.fail_requests = True
    with pytest.raises(RuntimeError, match="network down"):
        scenario(lab, time, [])
    assert lab.flags == {}


def test_refuses_to_start_when_a_fault_is_already_active() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lines: list[str] = []
    lab.flags = {"hang": True}
    assert scenario(lab, time, lines) == 2
    assert lab.flags == {"hang": True}  # left alone
