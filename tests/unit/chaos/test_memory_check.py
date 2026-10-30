"""The Phase 6 check: the second run's diagnosis must cite the first incident."""

from typing import Any

from chaos.memory_check import check
from chaos.runner import Outcome, Scenario
from nightwatch.chat.cards import diagnosis_embed
from nightwatch.common.models import Incident
from tests.unit.chaos.test_runner import START, FakeLab, FakeTime


def incident(number: int, similar: list[str], postmortem: str | None = None) -> Incident:
    return Incident.model_validate(
        {
            "id": f"01JB{number:022d}",
            "tenant_id": "default",
            "number": number,
            "service": "shoplite",
            "title": "shoplite-lambda-errors",
            "first_alarm": "shoplite-lambda-errors",
            "status": "resolved",
            "opened_at": START,
            "updated_at": START,
            "last_alert_at": START,
            "postmortem_key": postmortem,
            "investigation": "done",
            "investigation_result": {
                "diagnosis": {
                    "summary": "Same cause as INC-001." if similar else "A deploy.",
                    "hypotheses": [{"statement": "A deploy broke it."}],
                },
                "similar": [
                    {"label": label, "when": "25m 0s ago", "cause": "x"} for label in similar
                ],
            },
        }
    )


class MemoryLab(FakeLab):
    def __init__(self, written: bool = True) -> None:
        super().__init__(FakeTime())
        self.written = written

    def incident(self, incident_id: str) -> Incident | None:
        return incident(1, [], "postmortems/default/INC-001.md")

    def postmortem(self, key: str) -> str | None:
        return "# INC-001: shoplite-lambda-errors\n..." if self.written else None


def runs(*outcomes: Outcome) -> Any:
    queue = list(outcomes)
    return lambda lab, scenario, out: queue.pop(0)


def test_passes_when_the_second_diagnosis_cites_the_first() -> None:
    lines: list[str] = []
    first = Outcome(0, START, START, [incident(1, [])])
    second = Outcome(0, START, START, [incident(2, ["INC-001"])])
    code = check(MemoryLab(), "bad-deploy", lines.append, lambda s: None, runs(first, second))
    assert code == 0, lines
    assert "The model's own words name INC-001: yes" in lines
    assert lines[-1] == "PASS: INC-002's diagnosis cites INC-001, whose postmortem is in S3."


def test_fails_without_a_postmortem_or_a_citation() -> None:
    first = Outcome(0, START, START, [incident(1, [])])
    no_pm = check(MemoryLab(written=False), "bad-deploy", print, lambda s: None, runs(first))
    assert no_pm == 1
    forgot = Outcome(0, START, START, [incident(2, ["INC-000"])])
    assert check(MemoryLab(), "bad-deploy", print, lambda s: None, runs(first, forgot)) == 1


def test_card_shows_what_the_incident_looks_like() -> None:
    result = {
        "method": "rules",
        "diagnosis": {"summary": "x", "hypotheses": [{"statement": "y"}]},
        "similar": [{"label": "INC-001", "when": "2d 0h ago", "cause": "A bad deploy."}],
    }
    field = diagnosis_embed(result)["fields"][0]
    assert field == {"name": "Looks like", "value": "INC-001, 2d 0h ago: A bad deploy."}
    _ = Scenario  # the scenario itself isn't needed here
