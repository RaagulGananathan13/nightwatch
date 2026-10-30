"""Slash commands: /nw status, /nw ack and /nw ask, plus the ask Lambda and its reply."""

import json
from typing import Any

import pytest

from nightwatch.agent import ask as ask_lambda
from nightwatch.agent.models import Diagnosis, Hypothesis, Investigation
from nightwatch.chat import interactions
from nightwatch.chat.cards import incident_message
from nightwatch.chat.discord import InteractionReply
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.ingest import handler as ingest
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_approvals import APPROVER, PUBLIC, signed
from tests.unit.nightwatch.test_chat import FakeResponse, Opener

TOKEN = "interaction-token-for-tests-0123456789"


def slash(sub: str, user: str = APPROVER, **options: str) -> dict[str, Any]:
    return {
        "type": 2,
        "application_id": "1565236782549700638",
        "token": TOKEN,
        "data": {
            "name": "nw",
            "options": [
                {
                    "type": 1,
                    "name": sub,
                    "options": [{"name": k, "value": v} for k, v in options.items()],
                }
            ],
        },
        "member": {"user": {"id": user, "global_name": "Raagul"}},
    }


@pytest.fixture
def env(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC)
    monkeypatch.setenv("ASK_FUNCTION", "nightwatch-ask")
    jobs: list[dict[str, Any]] = []

    class FakeLambda:
        def invoke(self, **kwargs: Any) -> dict[str, Any]:
            jobs.append(json.loads(kwargs["Payload"]))
            return {}

    monkeypatch.setattr(interactions, "_lambda", FakeLambda)
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    return {"store": store, "jobs": jobs, "incident": incident}


def call(body: dict[str, Any]) -> dict[str, Any]:
    reply = interactions.handler(signed(body), None)
    assert reply["statusCode"] == 200
    answer: dict[str, Any] = json.loads(reply["body"])
    return answer


def test_status_lists_open_incidents(env: dict[str, Any]) -> None:
    answer = call(slash("status"))
    assert answer["type"] == 4
    assert "**1 open incident(s):**" in answer["data"]["content"]
    assert "**INC-001** `shoplite`" in answer["data"]["content"]
    assert answer["data"]["allowed_mentions"] == {"parse": []}


def test_ack_takes_an_incident_once_and_only_for_approvers(env: dict[str, Any]) -> None:
    assert "approvers" in call(slash("ack", user="5", incident="INC-1"))["data"]["content"]
    taken = call(slash("ack", incident="inc-001"))
    assert taken["data"]["content"] == "👀 **INC-001** is taken by Raagul."
    again = call(slash("ack", incident="INC-1"))
    assert again["data"] == {"content": "INC-001 is already taken or no longer open.", "flags": 64}
    assert call(slash("ack", incident="INC-77"))["data"]["content"] == "INC-077 isn't open."
    assert call(slash("ack", incident="hello"))["data"]["flags"] == 64
    stored = env["store"].get_incident(env["incident"].id)
    assert (stored.acked_by, stored.acked_by_name) == (APPROVER, "Raagul")
    card = incident_message(stored, T0)
    assert {"name": "Acknowledged", "value": "Raagul", "inline": True} in card["embeds"][0][
        "fields"
    ]
    events = [e["event"] for e in AuditLog(TABLE, "default", "t").for_incident(stored.id)]
    assert "incident_acknowledged" in events


def test_ask_defers_and_starts_the_ask_lambda(env: dict[str, Any]) -> None:
    assert call(slash("ask", question="why is checkout slow?")) == {"type": 5}
    assert env["jobs"] == [
        {
            "question": "why is checkout slow?",
            "service": "shoplite",
            "application_id": "1565236782549700638",
            "token": TOKEN,
            "user": "Raagul",
        }
    ]
    assert call(slash("ask", user="5", question="hi"))["data"]["flags"] == 64
    assert "doesn't watch" in call(slash("ask", question="hi", service="x"))["data"]["content"]
    assert len(env["jobs"]) == 1


def test_ask_lambda_answers_by_editing_the_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    edits: list[dict[str, Any]] = []

    class FakeReply:
        def __init__(self, application_id: str, token: str) -> None:
            assert token == TOKEN

        def edit(self, message: dict[str, Any]) -> None:
            edits.append(message)

    class FakeInvestigator:
        def __init__(self, ctx: Any, *args: Any) -> None:
            assert ctx.question == "why is checkout slow?"
            assert ctx.incident.title == ctx.question

        def run(self) -> Investigation:
            diagnosis = Diagnosis(
                summary="It isn't slow.", hypotheses=[Hypothesis(statement="p95 is 40 ms.")]
            )
            return Investigation(diagnosis=diagnosis, method="rules")

    monkeypatch.setattr(ask_lambda, "InteractionReply", FakeReply)
    monkeypatch.setattr(ask_lambda, "Investigator", FakeInvestigator)
    monkeypatch.setattr(ask_lambda, "build_chain", lambda *a, **k: None)
    monkeypatch.setattr(ask_lambda, "AuditLog", lambda *a, **k: AuditLogStub())
    out = ask_lambda.handler(
        {
            "question": "why is checkout slow?",
            "service": "shoplite",
            "application_id": "1565236782549700638",
            "token": TOKEN,
            "user": "Raagul",
        },
        None,
    )
    assert out["method"] == "rules"
    (message,) = edits
    assert message["content"] == "**Raagul asked:** why is checkout slow?"
    assert message["embeds"][0]["title"] == "💬 Answer (rule-based)"


class AuditLogStub:
    def record(self, *args: Any, **kwargs: Any) -> None:
        return None


def test_interaction_reply_edits_the_original_message_and_keeps_its_buttons() -> None:
    opener = Opener(FakeResponse({}))
    row = {"type": 1, "components": [{"type": 2, "style": 3, "label": "Go", "custom_id": "x"}]}
    InteractionReply("123", TOKEN, opener=opener).edit({"content": "hi", "components": [row]})
    request = opener.requests[0]
    assert (
        request.full_url == f"https://discord.com/api/v10/webhooks/123/{TOKEN}/messages/@original"
    )
    assert request.get_method() == "PATCH"
    assert json.loads(request.data)["components"] == [row]  # the reply is the app's own
    with pytest.raises(ValueError, match="interaction token"):
        InteractionReply("123", "../../x")


def test_ingest_starts_the_postmortem_when_an_incident_resolves(
    aws: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    from tests.unit.nightwatch.conftest import alarm_event

    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("POSTMORTEM_FUNCTION", "nightwatch-postmortem")
    monkeypatch.setattr(ingest, "alarm_tags", lambda arn: {"nightwatch:service": "shoplite"})
    monkeypatch.setattr(ingest, "_webhook", lambda settings: None)
    started: list[tuple[str, str]] = []

    def start(function: str, incident_id: str) -> bool:
        started.append((function, incident_id))
        return True

    monkeypatch.setattr(ingest, "start_postmortem", start)
    monkeypatch.setattr(ingest, "utcnow", lambda: T0 + timedelta(minutes=1))
    opened = ingest.handler(alarm_event(), None)
    assert started == []
    monkeypatch.setattr(ingest, "utcnow", lambda: T0 + timedelta(minutes=5))
    ingest.handler(alarm_event(state="OK", at=T0 + timedelta(minutes=4)), None)
    assert started == [("nightwatch-postmortem", started[0][1])]
    assert opened["incident"] == "INC-001"
