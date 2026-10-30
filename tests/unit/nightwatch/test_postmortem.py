"""Postmortems: facts from records, lessons from an LLM or rules, written to S3 with a memory."""

from datetime import timedelta
from typing import Any

import pytest

from nightwatch.agent.providers import fake as fake_provider
from nightwatch.agent.providers.chain import ProviderChain
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.timeutil import to_iso
from nightwatch.memory.store import MemoryStore
from nightwatch.postmortem import handler as postmortem
from nightwatch.postmortem.writer import Facts, parse_lessons, render, rule_lessons, timeline
from tests.unit.nightwatch.conftest import T0, TABLE
from tests.unit.nightwatch.test_memory import DEPLOY, resolved
from tests.unit.nightwatch.test_remediation import make_action


def event(seconds: int, name: str, **details: Any) -> dict[str, Any]:
    return {"event": name, "at": to_iso(T0 + timedelta(seconds=seconds)), "details": details}


EVENTS = [
    event(0, "incident_opened", alarm="shoplite-lambda-errors"),
    event(1, "notification_sent"),
    event(31, "llm_call", model="m"),  # not a timeline event
    event(44, "diagnosis_posted"),
    event(45, "remediation_proposed", preview="alias live: version 13 -> 12"),
    event(300, "approval_decided", decision="approve", user="Raagul"),
    event(303, "remediation_executed", call="lambda.update_alias(...)"),
    event(360, "incident_resolved", alarm="shoplite-5xx-rate"),
]


def facts(**changes: Any) -> Facts:
    action = make_action(
        "verified",
        executed_at=T0 + timedelta(seconds=303),
        verification={"detail": "Errors after the fix: 0, 0."},
    )
    base = {"incident": resolved(), "events": EVENTS, "actions": [action]}
    return Facts(**{**base, **changes})


def test_timeline_comes_from_the_audit_log_in_plain_words() -> None:
    rows = [text for _, text in timeline(facts())]
    assert rows == [
        "Alarm shoplite-lambda-errors fired and the incident opened",
        "Posted to Discord",
        "Diagnosis posted to Discord",
        "Fix proposed: alias live: version 13 -> 12",
        "Fix approved by Raagul",
        "Fix ran: lambda.update_alias(...)",
        "Alarm shoplite-5xx-rate recovered; all alarms OK, incident resolved",
    ]


def test_postmortem_has_the_facts_and_marks_who_drafted_the_lessons() -> None:
    lessons = rule_lessons(facts())
    markdown = render(facts(), lessons)
    assert markdown.startswith("# INC-009: shoplite-lambda-errors\n")
    assert "| Alarm to diagnosis | 44 s |" in markdown
    assert f"{DEPLOY} (confidence 0%, NOT verified against evidence none)" in markdown
    assert "| 09:59:60" not in markdown
    assert "| 10:05:00 | 5m 0s | Fix approved by Raagul |" in markdown
    assert "ran the rollback-lambda-alias runbook" in markdown
    assert "Errors after the fix: 0, 0." in markdown
    assert "- [ ] Add a test for the code path that failed" in markdown
    assert markdown.endswith("*Lessons drafted by fixed rules. Review them before acting.*\n")
    assert "The fix (roll back the deploy) ran and verification passed" in lessons.went_well
    without = rule_lessons(facts(actions=[]))
    assert "No runbook matched, so NightWatch could not propose a fix" in without.went_badly


def test_model_lessons_are_used_only_when_they_parse() -> None:
    good = parse_lessons(
        'Sure: {"went_well": ["Fast"], "went_badly": [], "action_items": ["Add | a test"]}', "m"
    )
    assert good is not None
    assert good.action_items == ["Add / a test"]  # can't break the markdown
    assert good.written_by == "m"
    assert parse_lessons("no json here", "m") is None
    assert parse_lessons('{"went_well": ["x"], "action_items": []}', "m") is None


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self.objects[f"{kwargs['Bucket']}/{kwargs['Key']}"] = kwargs["Body"]
        return {}


def test_handler_writes_postmortem_memory_and_audit(
    store: IncidentStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("POSTMORTEM_BUCKET", "pm-bucket")
    s3 = FakeS3()
    monkeypatch.setattr(postmortem, "_s3", lambda: s3)
    reply = '{"went_well": ["Fast"], "went_badly": ["Late"], "action_items": ["Add a test"]}'
    model = fake_provider.FakeProvider([reply])
    monkeypatch.setattr(
        postmortem, "build_chain", lambda *a, **k: ProviderChain([model], sleep=lambda s: None)
    )
    incident = store.assign_number(store.open_incident(_alert(), None, T0))
    store.client.update_item(
        TableName=TABLE,
        Key={"PK": {"S": "TENANT#default"}, "SK": {"S": f"INC#{incident.id}"}},
        UpdateExpression="SET #st = :r, resolved_at = :t",
        ExpressionAttributeNames={"#st": "status"},
        ExpressionAttributeValues={":r": {"S": "resolved"}, ":t": {"S": to_iso(T0)}},
    )

    out = postmortem.handler({"incident_id": incident.id}, None)
    assert out == {
        "incident_id": incident.id,
        "written": True,
        "key": f"postmortems/default/{incident.label}.md",
    }
    body = s3.objects[f"pm-bucket/postmortems/default/{incident.label}.md"].decode()
    assert "- [ ] Add a test" in body
    assert "*Lessons drafted by scripted. Review them before acting.*" in body
    written = store.get_incident(incident.id)
    assert written is not None
    assert written.postmortem_key == out["key"]
    assert [m.label for m in MemoryStore(TABLE, "default").all("shoplite")] == [incident.label]
    events = [e["event"] for e in AuditLog(TABLE, "default", "t").for_incident(incident.id)]
    assert "postmortem_written" in events

    open_one = store.assign_number(
        store.open_incident(_alert("checkout-errors", "checkout"), None, T0)
    )
    assert postmortem.handler({"incident_id": open_one.id}, None)["written"] is False


def _alert(alarm: str = "shoplite-lambda-errors", service: str = "shoplite") -> Any:
    from tests.unit.nightwatch.conftest import make_alert

    return make_alert(alarm, service=service)
