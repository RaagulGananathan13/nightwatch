"""Scoring a chaos run, the results report, and the kill switch around a whole evaluation."""

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from chaos.runner import Outcome, Scenario
from eval.run import evaluate, load
from eval.score import render, score, summarize
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import to_iso
from nightwatch.remediation.actions import Action
from tests.unit.chaos.test_runner import START, FakeLab, FakeTime
from tests.unit.nightwatch.test_remediation import make_action

FAULTED = START + timedelta(seconds=10)
OPENED = START + timedelta(seconds=70)


def incident(hypotheses: list[dict[str, Any]], alerts: int = 2) -> Incident:
    return Incident.model_validate(
        {
            "id": "01JB0000000000000000000INC",
            "tenant_id": "default",
            "number": 7,
            "service": "shoplite",
            "title": "shoplite-lambda-errors",
            "first_alarm": "shoplite-lambda-errors",
            "status": "resolved",
            "opened_at": OPENED,
            "updated_at": OPENED,
            "last_alert_at": OPENED,
            "alert_count": alerts,
            "alarms": {"shoplite-lambda-errors": "OK"},
            "investigation": "done",
            "investigation_result": {
                "method": "llm",
                "model": "openai/gpt-oss-120b",
                "prompt_version": "investigate_v1",
                "llm_calls": 1,
                "tokens": 3000,
                "diagnosis": {"summary": "x", "hypotheses": hypotheses},
            },
        }
    )


class ScoreLab:
    def __init__(self, actions: list[Action]) -> None:
        self._actions = actions

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        posted = OPENED + timedelta(seconds=44)
        return [
            {"event": "notification_sent", "at": to_iso(OPENED)},
            {"event": "diagnosis_posted", "at": to_iso(posted)},
        ]

    def actions(self, incident_id: str) -> list[Action]:
        return self._actions


def scenario() -> Scenario:
    return Scenario(
        name="Lambda timeout",
        ground_truth="The hang flag makes requests time out.",
        inject=lambda out: lambda: None,
        expect=["hang"],
        slug="lambda-timeout",
        fix="reset-feature-flags",
    )


HYPOTHESES = [
    {"statement": "Requests time out after 5 s.", "confidence": 0.8, "verified": True},
    {
        "statement": "The hang flag in /shoplite/flags causes it.",
        "confidence": 0.6,
        "verified": True,
        "notes": ["added E4, which contains '5'"],
    },
    {"statement": "Made-up cause 99.", "confidence": 0.2, "verified": False},
]


def test_a_run_is_scored_from_the_incident_audit_and_actions() -> None:
    fix = make_action("blocked", runbook_id="reset-feature-flags")
    outcome = Outcome(0, START, FAULTED, [incident(HYPOTHESES)])
    run = score(ScoreLab([fix]), scenario(), outcome, "abc123")
    assert run["status"] == "diagnosed"
    assert run["detect_seconds"] == 60.0
    assert run["diagnosis_seconds"] == 44.0
    assert run["top1"] is None  # the top hypothesis names only the symptom
    assert run["top3"] == "hang"
    assert (run["hypotheses"], run["verified"], run["verified_as_written"]) == (3, 2, 1)
    assert run["fix_proposed"] == "reset-feature-flags"
    assert (run["llm_calls"], run["tokens"], run["commit"]) == (1, 3000, "abc123")


def test_missing_incidents_and_runs_that_never_started() -> None:
    assert score(ScoreLab([]), scenario(), Outcome(1, START, FAULTED), "c")["status"] == (
        "no incident"
    )
    assert score(ScoreLab([]), scenario(), Outcome(2, START, None), "c")["status"] == "not run"


def test_report_shows_the_headline_numbers_and_how_they_are_scored() -> None:
    right = [{**HYPOTHESES[1], "notes": []}, HYPOTHESES[0]]
    runs = [
        score(ScoreLab([]), scenario(), Outcome(0, START, FAULTED, [incident(right)]), "c1"),
        score(ScoreLab([]), scenario(), Outcome(0, START, FAULTED, [incident(HYPOTHESES)]), "c1"),
        score(ScoreLab([]), scenario(), Outcome(1, START, FAULTED), "c1"),
    ]
    summary = summarize(runs)
    assert (summary["top1"], summary["top3"], summary["scenarios"]) == (1, 2, 3)
    assert summary["mean_diagnosis_seconds"] == 44.0
    assert (summary["verified"], summary["hypotheses"]) == (4, 5)
    markdown = render(runs)
    assert "| Top-1 root-cause accuracy | **1/3 (33%)** |" in markdown
    assert "| Mean time to diagnosis (alarm → diagnosis in Discord) | **44 s** |" in markdown
    assert "| Evidence validity (hypotheses the guard verified) | **4/5 (80%)** |" in markdown
    assert "| Lambda timeout | **none** |" in markdown
    assert "`hang`" in markdown
    assert "## Notes\n\n- The ground truth was corrected." in render(
        runs, ["The ground truth was corrected."]
    )


class EvalLab(FakeLab):
    def __init__(self) -> None:
        super().__init__(FakeTime())
        self.switch = "false"
        self.switch_history: list[str] = []

    def kill_switch(self) -> str:
        return self.switch

    def set_kill_switch(self, value: str) -> None:
        self.switch = value
        self.switch_history.append(value)

    def actions(self, incident_id: str) -> list[Action]:
        return []


def test_evaluation_turns_fixes_off_and_always_turns_them_back(tmp_path: Path) -> None:
    lab = EvalLab()

    def one(lab: EvalLab, scenario: Scenario, out: Any) -> Outcome:
        assert lab.switch == "true"
        return Outcome(0, START, FAULTED, [incident(HYPOTHESES)])

    evaluate(lab, ["lambda-timeout"], "c1", out=lambda _: None, folder=tmp_path, run_one=one)
    assert lab.switch_history == ["true", "false"]
    assert load(tmp_path)["lambda-timeout"]["top3"] == "hang"
    assert "Lambda timeout" in (tmp_path / "results.md").read_text(encoding="utf-8")

    def broken(lab: EvalLab, scenario: Scenario, out: Any) -> Outcome:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        evaluate(lab, ["bad-deploy"], "c1", out=lambda _: None, folder=tmp_path, run_one=broken)
    assert lab.switch == "false"
    assert list(load(tmp_path)) == ["lambda-timeout"]  # earlier results are kept
