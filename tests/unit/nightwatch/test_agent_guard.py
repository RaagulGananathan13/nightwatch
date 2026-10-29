"""The evidence guard (no unsupported claims) and PII redaction (nothing sensitive leaves)."""

from datetime import UTC, datetime

import pytest

from nightwatch.agent.guard import check, redact
from nightwatch.agent.models import Diagnosis, Evidence, Hypothesis

NOW = datetime(2026, 10, 28, 10, 0, tzinfo=UTC)
EVIDENCE = {
    "E1": Evidence(
        id="E1",
        tool="recent_changes",
        summary="-2m 00s: shoplite-api version 5 published (sha=3f2a9c1e4b5d) at 09:58:07 UTC",
        data={"error_rate": 0.92, "errors": 15.47},
        started_at=NOW,
    ),
}


def verdict(statement: str, ids: list[str] | None = None) -> Hypothesis:
    diagnosis = Diagnosis(
        summary="s", hypotheses=[Hypothesis(statement=statement, evidence_ids=ids or ["E1"])]
    )
    return check(diagnosis, EVIDENCE).hypotheses[0]


@pytest.mark.parametrize(
    "statement",
    [
        "Version 5 (3f2a9c1e4b5d) went out at 09:58:07 UTC.",
        "Short SHA 3f2a9c1 is fine.",
        "About 92% of requests fail.",
        "Errors run at 15.5 per minute.",
        "Version 5 broke it.",  # single digits are too generic to check
    ],
)
def test_supported_claims_stay_verified(statement: str) -> None:
    assert verdict(statement).verified


@pytest.mark.parametrize(
    ("statement", "problem"),
    [
        ("Commit 9e9e9e9e1 caused it.", "'9e9e9e9e1'"),
        ("Errors rose by 340 per minute.", "'340'"),
        ("It started at 09:31:00 UTC.", "'09:31:00'"),
    ],
)
def test_invented_numbers_are_flagged(statement: str, problem: str) -> None:
    result = verdict(statement)
    assert not result.verified
    assert any(problem in note for note in result.notes)
    assert result.confidence <= 0.3


def test_a_fact_cited_from_the_wrong_item_is_repaired_not_rejected() -> None:
    """Seen live on 2026-10-29: the model quoted the alarm's 92.3% but cited other evidence."""
    alarm = Evidence(
        id="E2",
        tool="get_alarm_context",
        summary="Now ALARM: Threshold Crossed: 1 datapoint [57.14285714285714] > 5.0",
        started_at=NOW,
    )
    diagnosis = Diagnosis(
        summary="s",
        hypotheses=[Hypothesis(statement="Version 5 caused a 57% 5xx rate.", evidence_ids=["E1"])],
    )
    (checked,) = check(diagnosis, {**EVIDENCE, "E2": alarm}).hypotheses
    assert checked.verified
    assert checked.evidence_ids == ["E1", "E2"]
    assert checked.notes == ["added E2, which contains '57%'"]


def test_missing_and_unknown_evidence() -> None:
    assert not verdict("Something broke.", ids=["E7"]).verified
    assert "doesn't exist: E7" in verdict("x", ids=["E1", "E7"]).notes[0]


def test_verified_hypotheses_come_first() -> None:
    diagnosis = Diagnosis(
        summary="s",
        hypotheses=[
            Hypothesis(statement="Made up 777.", confidence=0.95, evidence_ids=["E1"]),
            Hypothesis(statement="Version 5 went out.", confidence=0.6, evidence_ids=["E1"]),
        ],
    )
    first, second = check(diagnosis, EVIDENCE).hypotheses
    assert first.verified
    assert not second.verified


# Credential-shaped samples are assembled at runtime, so this file never contains anything a
# secret scanner (or a reviewer) would mistake for a real key.
AWS_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"
JWT = ".".join(["ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiIxIn0", "c2lnbmF0dXJl"])
PASSWORD = "hunter2" * 2


@pytest.mark.parametrize(
    ("text", "hidden", "replacement"),
    [
        ("contact jane.doe@example.com now", "jane.doe@example.com", "[email]"),
        (f"key {AWS_KEY} leaked", AWS_KEY, "[aws-key]"),
        (f"auth {JWT}", JWT, "[jwt]"),
        ("from 203.0.113.42 at noon", "203.0.113.42", "[ip]"),
        ("card 4111 1111 1111 1111 declined", "4111 1111 1111 1111", "[card]"),
        ('{"pass' + 'word": "' + PASSWORD + '"}', PASSWORD, "[secret]"),
        ("call +94 77 123 4567", "+94 77 123 4567", "[phone]"),
    ],
)
def test_redaction(text: str, hidden: str, replacement: str) -> None:
    cleaned = redact(text)
    assert hidden not in cleaned
    assert replacement in cleaned


def test_redaction_keeps_what_diagnosis_needs() -> None:
    text = "version 3f2a9c1e4b5d at 2026-10-28T10:00:07Z, 57 errors, order 1234567890123"
    assert redact(text) == text  # SHAs, times, counts and non-Luhn numbers survive
    assert redact({"rows": [{"email": "a@b.co"}]}) == {"rows": [{"email": "[email]"}]}
