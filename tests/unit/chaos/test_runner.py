"""Chaos scenarios always clean up, and the verdict is the Phase 2 and 3 acceptance check."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from chaos import bad_deploy, error_flag
from chaos.lab import BUG
from chaos.runner import Scenario, run, run_fix
from nightwatch.common.models import Incident
from nightwatch.remediation.actions import Action
from tests.unit.nightwatch.test_remediation import make_action

START = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
SHA = "3f2a9c1e4b5d"


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
    def __init__(
        self,
        time: FakeTime,
        incidents: int = 1,
        messages: int = 1,
        diagnosis: str = f"Deploy {SHA} broke listings.",
    ) -> None:
        self.time = time
        self.flags: dict[str, Any] = {}
        self.live = "4"
        self.history: list[str] = []
        self.requests = 0
        self.incident_count = incidents
        self.messages = messages
        self.diagnosis = diagnosis
        self.flap_wait = 0.0
        self.fail_requests = False
        self.open_polls = 0  # an earlier incident stays open for this many checks
        self.capacity = 5
        self.env = {"TABLE_NAME": "shoplite"}

    # ShopLite
    def get_flags(self) -> dict[str, Any]:
        return dict(self.flags)

    def set_flags(self, flags: dict[str, Any]) -> None:
        self.flags = dict(flags)
        self.history.append(f"flags={flags}")

    def build_buggy_shoplite(self, subject: str) -> tuple[str, bytes]:
        return SHA, b"zip"

    def deploy(self, code: bytes, description: str) -> dict[str, str]:
        self.history.append(f"deploy {description}")
        previous, self.live = self.live, "5"
        return {"version": "5", "previous_version": previous}

    def live_version(self) -> str:
        return self.live

    def deploy_settings(self, changes: dict[str, str], description: str) -> dict[str, Any]:
        self.history.append(f"settings {changes}")
        previous, self.live = self.live, "6"
        original, self.env = dict(self.env), {**self.env, **changes}
        return {"version": "6", "previous_version": previous, "original": original}

    def restore_settings(self, variables: dict[str, str]) -> None:
        self.env = dict(variables)
        self.history.append("settings restored")

    def read_capacity(self) -> int:
        return self.capacity

    def set_read_capacity(self, units: int) -> None:
        self.capacity = units
        self.history.append(f"read={units}")

    def broken(self) -> bool:
        return bool(self.flags) or self.live in ("5", "6") or self.capacity == 1

    def set_live_version(self, version: str) -> None:
        self.live = version
        self.history.append(f"live={version}")

    def request(self, method: str, path: str, body: Any = None) -> int:
        if self.fail_requests:
            raise RuntimeError("network down")
        self.requests += 1
        return 502 if self.broken() else 200

    # NightWatch
    def incidents_since(self, service: str, since: datetime) -> list[Incident]:
        if self.time.seconds < 90:
            return []
        return [
            self._incident(n, "open" if self.broken() else "resolved")
            for n in range(1, self.incident_count + 1)
        ]

    def incident_open(self, service: str) -> bool:
        self.open_polls -= 1
        return self.open_polls >= 0

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        return [{"event": "notification_sent"}] * self.messages

    def seconds_until_new_incident(self, service: str) -> float:
        return self.flap_wait

    def _incident(self, number: int, status: str) -> Incident:
        result = {
            "method": "llm",
            "model": "openai/gpt-oss-120b",
            "diagnosis": {
                "summary": self.diagnosis,
                "hypotheses": [
                    {
                        "statement": self.diagnosis,
                        "confidence": 0.9,
                        "evidence_ids": ["E4"],
                        "verified": True,
                    }
                ],
            },
        }
        return Incident.model_validate(
            {
                "id": f"01JB{number:022d}",
                "tenant_id": "default",
                "number": number,
                "service": "shoplite",
                "title": "shoplite-lambda-errors",
                "first_alarm": "shoplite-lambda-errors",
                "status": status,
                "opened_at": START + timedelta(seconds=70),
                "updated_at": START,
                "last_alert_at": START,
                "alert_count": 2,
                "alarms": {"shoplite-5xx-rate": "ALARM", "shoplite-lambda-errors": "ALARM"},
                "investigation": "done",
                "investigation_result": result,
            }
        )


def go(lab: FakeLab, time: FakeTime, scenario: Scenario, lines: list[str]) -> int:
    return run(
        lab, scenario, 3, 6, out=lines.append, clock=time.clock, sleep=time.sleep, now=time.now
    )


def test_bad_deploy_passes_and_always_rolls_back() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lines: list[str] = []
    assert go(lab, time, bad_deploy.scenario(lab), lines) == 0
    assert lab.history[0].startswith(f"deploy sha={SHA} by=chaos-lab")
    assert lab.history[-1] == "live=4"
    assert any(f"Deployed commit {SHA} as version 5 (was version 4)" in line for line in lines)
    assert lines[-1].startswith("PASS")


def test_diagnosis_must_name_the_commit() -> None:
    time = FakeTime()
    lab = FakeLab(time, diagnosis="DynamoDB is slow.")
    lines: list[str] = []
    assert go(lab, time, bad_deploy.scenario(lab), lines) == 1
    assert f"FAIL: the diagnosis does not mention {SHA[:7]}" in lines


def test_error_flag_scenario() -> None:
    time = FakeTime()
    lab = FakeLab(time, diagnosis="The feature flag /shoplite/flags set error_rate to 0.8.")
    lines: list[str] = []
    assert go(lab, time, error_flag.scenario(lab, 0.8), lines) == 0
    assert lab.history == ["flags={'error_rate': 0.8}", "flags={}"]


@pytest.mark.parametrize(("incidents", "messages"), [(2, 1), (1, 2)])
def test_wrong_counts_fail(incidents: int, messages: int) -> None:
    time = FakeTime()
    lab = FakeLab(time, incidents, messages)
    lines: list[str] = []
    assert go(lab, time, bad_deploy.scenario(lab), lines) == 1
    assert lab.live == "4"


def test_undo_runs_even_when_something_breaks() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lab.fail_requests = True
    with pytest.raises(RuntimeError, match="network down"):
        go(lab, time, bad_deploy.scenario(lab), [])
    assert lab.live == "4"


def test_waits_out_the_flap_window_and_refuses_active_faults() -> None:
    time = FakeTime()
    lab = FakeLab(time)
    lab.flap_wait = 330.0
    lines: list[str] = []
    assert go(lab, time, bad_deploy.scenario(lab), lines) == 0
    assert "Waiting 5m 30s" in lines[0]
    busy = FakeLab(FakeTime())
    busy.flags = {"hang": True}
    assert go(busy, busy.time, bad_deploy.scenario(busy), []) == 2
    assert busy.history == []


def test_the_bug_still_matches_shoplite_source() -> None:
    """If store.py changes, the bad-deploy patch must change with it."""
    source = (Path(__file__).resolve().parents[3] / "shoplite" / "store.py").read_text(
        encoding="utf-8"
    )
    assert BUG[0] in source


class FixLab(FakeLab):
    """NightWatch proposes a rollback at 2 min; the approver decides at 3 min 20 s."""

    def __init__(self, time: FakeTime, decision: str) -> None:
        super().__init__(time)
        self.decision = decision
        self.fixed = False

    def actions(self, incident_id: str) -> list[Action]:
        seconds = self.time.seconds
        if seconds < 120:
            return []
        status = "awaiting_approval"
        if seconds >= 200 and self.decision == "reject":
            status = "rejected"
        elif seconds >= 200:
            if not self.fixed:
                self.fixed, self.live = True, "4"  # NightWatch's rollback
            status = "verified" if seconds >= 400 else "executed"
        return [make_action(status, decided_by_name="Raagul")]

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        chain = ["notification_sent", "remediation_proposed", "approval_requested"]
        if self.time.seconds >= 200:
            chain.append("approval_decided")
            if self.decision == "approve":
                chain += ["remediation_executed", "remediation_verified"]
        return [{"event": event} for event in chain]


def test_an_approved_fix_is_verified_and_the_chain_is_audited() -> None:
    time, lines = FakeTime(), list[str]()
    lab = FixLab(time, "approve")
    code = run_fix(lab, bad_deploy.scenario(lab), out=lines.append, clock=time.clock,
                   sleep=time.sleep, now=time.now)  # fmt: skip
    assert code == 0, lines
    assert any(line.startswith("PASS: approved by Raagul") for line in lines)
    assert "remediation_proposed -> approval_requested -> approval_decided" in "\n".join(lines)
    assert lab.live == "4"


def test_rejecting_changes_nothing_and_the_lab_still_cleans_up() -> None:
    time, lines = FakeTime(), list[str]()
    lab = FixLab(time, "reject")
    code = run_fix(lab, bad_deploy.scenario(lab), out=lines.append, clock=time.clock,
                   sleep=time.sleep, now=time.now)  # fmt: skip
    assert code == 0, lines
    assert any(line.startswith("PASS: rejected by Raagul, and nothing changed") for line in lines)
    assert lab.history[-1] == "live=4"  # the lab's own undo, not NightWatch
