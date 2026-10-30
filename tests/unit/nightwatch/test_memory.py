"""Incident memory: the lexical embedding, the store, and the similar-incidents tool."""

from datetime import timedelta
from typing import Any

import pytest

from nightwatch.agent.tools import run_tool
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.memory.embed import embed, rank, tokens
from nightwatch.memory.store import Memory, MemoryStore, remember
from tests.unit.nightwatch.conftest import T0, TABLE
from tests.unit.nightwatch.test_agent_tools import context
from tests.unit.nightwatch.test_remediation import make_action

DEPLOY = "Version 13 introduced a KeyError on the missing price field after a deploy"
FLAG = "The flag /shoplite/flags set error_rate to 0.8, so requests fail with InjectedFaultError"
CAPACITY = "Read capacity of the shoplite table was lowered, so reads are throttled"


def test_tokens_keep_identifiers_and_drop_filler() -> None:
    assert tokens("The TABLE_NAME was changed to shoplite-catalogue") == [
        "table_name",
        "changed",
        "shoplite",
        "catalogue",
    ]


def test_the_matching_story_ranks_first_and_shared_words_count_less() -> None:
    query = embed("shoplite errors: KeyError 'price' from the new version after a deploy")
    scores = rank(query, [embed(FLAG), embed(DEPLOY), embed(CAPACITY)])
    assert scores.index(max(scores)) == 1
    assert scores[1] > 0.2
    assert rank(embed(""), [embed(DEPLOY)]) == [0.0]


def memory(incident_id: str, cause: str, days_ago: float = 1) -> Memory:
    when = T0 - timedelta(days=days_ago)
    return Memory(
        incident_id=incident_id,
        label=f"INC-{incident_id[-3:]}",
        service="shoplite",
        opened_at=when,
        resolved_at=when,
        cause=cause,
        fix="Roll back the deploy: shoplite-api alias live: version 13 -> 12 (verified)",
        text=cause,
        vector=embed(cause),
    )


def test_store_finds_the_most_similar_and_prefers_recent_near_twins(aws: None) -> None:
    store = MemoryStore(TABLE, "default")
    store.put(memory("01JB0000000000000000000001", DEPLOY, days_ago=20))
    store.put(memory("01JB0000000000000000000002", FLAG))
    store.put(memory("01JB0000000000000000000003", DEPLOY, days_ago=0.1))
    matches = store.search("shoplite", "KeyError price after the deploy", T0)
    assert [m.memory.label for m in matches[:2]] == ["INC-003", "INC-001"]  # twins: newest first
    assert all(m.score >= 0.2 for m in matches)
    excluded = store.search("shoplite", DEPLOY, T0, exclude="01JB0000000000000000000003")
    assert excluded[0].memory.label == "INC-001"
    assert store.search("other-service", DEPLOY, T0) == []


def resolved(result: dict[str, Any] | None = None) -> Incident:
    return Incident.model_validate(
        {
            "id": "01JB0000000000000000000INC",
            "tenant_id": "default",
            "number": 9,
            "service": "shoplite",
            "title": "shoplite-lambda-errors",
            "first_alarm": "shoplite-lambda-errors",
            "status": "resolved",
            "opened_at": T0,
            "updated_at": T0,
            "last_alert_at": T0,
            "resolved_at": T0 + timedelta(minutes=6),
            "alarms": {"shoplite-lambda-errors": "OK", "shoplite-5xx-rate": "OK"},
            "investigation": "done",
            "investigation_result": result
            or {"diagnosis": {"summary": "Bad deploy.", "hypotheses": [{"statement": DEPLOY}]}},
        }
    )


@pytest.mark.parametrize(
    ("status", "fix"),
    [
        ("verified", "Roll back the deploy: shoplite-api alias live: version 2 -> 1 (verified)"),
        ("blocked", "Roll back the deploy was proposed (blocked); the alarms recovered without it"),
        (None, "the alarms recovered without a fix from NightWatch"),
    ],
)
def test_remember_keeps_the_cause_and_how_it_was_fixed(status: str | None, fix: str) -> None:
    kept = remember(resolved(), [make_action(status)] if status else [])
    assert kept.cause == DEPLOY
    assert kept.fix == fix
    assert "shoplite-5xx-rate" in kept.text
    assert kept.vector == embed(kept.text)


def test_similar_tool_reads_memory_and_names_the_earlier_incident(store: IncidentStore) -> None:
    MemoryStore(TABLE, "default").put(memory("01JB0000000000000000000005", DEPLOY, days_ago=2))
    ctx = context(store=store)
    result = run_tool("search_similar_incidents", {"text": "KeyError price deploy"}, ctx)
    assert result.summary.splitlines()[1].startswith("INC-005, 2d 0h ago (similarity ")
    assert "Fix: Roll back the deploy" in result.summary
    assert result.data["incidents"][0]["label"] == "INC-005"
    empty = run_tool("search_similar_incidents", {"text": "nothing alike"}, ctx)
    assert empty.data == {"incidents": []}
