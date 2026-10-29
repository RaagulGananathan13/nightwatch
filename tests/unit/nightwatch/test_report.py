"""The Report step puts the diagnosis on the incident's message; ingest starts the workflow."""

from typing import Any

import pytest
from botocore.exceptions import ClientError

from nightwatch.chat import report
from nightwatch.chat.cards import incident_message
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.ingest import handler as ingest
from nightwatch.ingest.correlate import correlate
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert

RESULT = {
    "method": "llm",
    "provider": "groq",
    "model": "openai/gpt-oss-120b",
    "llm_calls": 1,
    "tokens": 4321,
    "duration_ms": 9000,
    "prompt_version": "investigate_v1",
    "diagnosis": {
        "summary": "Version 5 broke product listings.",
        "hypotheses": [
            {
                "statement": "Deploy 3f2a9c1e4b5d raises KeyError.",
                "confidence": 0.9,
                "evidence_ids": ["E4", "E6"],
                "verified": True,
            },
            {
                "statement": "Made-up cause.",
                "confidence": 0.3,
                "evidence_ids": ["E9"],
                "verified": False,
            },
        ],
        "impact": "Listings fail.",
        "suggested_actions": [{"description": "Roll back to version 4.", "risk": "low"}],
        "unknowns": ["Lost orders?"],
    },
    "evidence": [{"id": "E4", "tool": "recent_changes", "brief": "version 5 published"}],
}


class FakeWebhook:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    def send(self, message: dict[str, Any]) -> str:
        self.sent.append(message)
        return "555"

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        self.edits.append((message_id, message))


def opened(store: IncidentStore) -> Incident:
    outcome = correlate(store, make_alert(), Settings(table_name=TABLE), T0)
    assert outcome.incident is not None
    return store.assign_number(outcome.incident)


def test_card_shows_the_diagnosis(store: IncidentStore) -> None:
    incident = opened(store)
    store.set_investigation(incident.id, "done", RESULT)
    message = incident_message(store.get_incident(incident.id), T0)  # type: ignore[arg-type]
    assert message["content"].startswith("🔍 **INC-001** for **shoplite**: likely cause: Deploy")
    _, diagnosis = message["embeds"]
    assert diagnosis["title"] == "🔍 Diagnosis"
    assert "**1.** Deploy 3f2a9c1e4b5d raises KeyError. (90%) [E4] [E6]" in diagnosis["description"]
    assert "⚠️ *unverified:* Made-up cause." in diagnosis["description"]
    assert "openai/gpt-oss-120b via groq" in diagnosis["footer"]["text"]
    assert sum(len(str(embed)) for embed in message["embeds"]) < 6000  # Discord's limit


@pytest.fixture
def lambda_env(aws: None, monkeypatch: pytest.MonkeyPatch) -> FakeWebhook:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("DISCORD_WEBHOOK_PARAM", "/nightwatch/discord/webhook_url")
    hook = FakeWebhook()
    monkeypatch.setattr(report, "webhook_from_ssm", lambda param, secret: hook)
    return hook


def test_report_edits_the_existing_message(lambda_env: FakeWebhook) -> None:
    store = IncidentStore(TABLE, "default")
    incident = opened(store)
    store.set_discord_message(incident.id, "777")
    store.set_investigation(incident.id, "done", RESULT)
    assert report.handler({"incident_id": incident.id}, None)["posted"]
    message_id, message = lambda_env.edits[0]
    assert message_id == "777"
    assert message["embeds"][1]["title"] == "🔍 Diagnosis"
    events = [e["event"] for e in AuditLog(TABLE, "default", "t").for_incident(incident.id)]
    assert "diagnosis_posted" in events


def test_report_posts_when_there_was_no_message_and_explains_failures(
    lambda_env: FakeWebhook,
) -> None:
    store = IncidentStore(TABLE, "default")
    incident = opened(store)
    failure = {"Error": "States.Timeout", "Cause": "took too long"}
    report.handler({"incident_id": incident.id, "error": failure}, None)
    message = lambda_env.sent[0]
    assert message["embeds"][1]["title"] == "Investigation failed"
    assert "States.Timeout" in message["embeds"][1]["description"]
    stored = store.get_incident(incident.id)
    assert stored is not None
    assert stored.discord_message_id == "555"
    assert stored.investigation == "failed"


class FakeStepFunctions:
    def __init__(self, error: str | None = None) -> None:
        self.started: list[dict[str, Any]] = []
        self.error = error

    def start_execution(self, **kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "x"}}, "StartExecution")
        self.started.append(kwargs)
        return {"executionArn": "arn:x"}


@pytest.mark.parametrize(
    ("error", "event"),
    [(None, "investigation_queued"), ("InvalidArn", "investigation_start_failed")],
)
def test_ingest_starts_one_workflow_per_incident(
    aws: None, monkeypatch: pytest.MonkeyPatch, error: str | None, event: str
) -> None:
    store = IncidentStore(TABLE, "default")
    incident = opened(store)
    audit = AuditLog(TABLE, "default", "t")
    sfn = FakeStepFunctions(error)
    monkeypatch.setattr(ingest, "_stepfunctions", lambda: sfn)
    settings = Settings(
        table_name=TABLE,
        state_machine_arn="arn:aws:states:us-east-2:1:stateMachine:nightwatch-incident",
    )
    ingest.start_investigation(settings, incident, audit)
    if error is None:
        assert sfn.started[0]["name"] == incident.id
    assert event in [e["event"] for e in audit.for_incident(incident.id)]


def test_a_retried_start_is_harmless(aws: None, monkeypatch: pytest.MonkeyPatch) -> None:
    store = IncidentStore(TABLE, "default")
    incident = opened(store)
    monkeypatch.setattr(
        ingest, "_stepfunctions", lambda: FakeStepFunctions("ExecutionAlreadyExists")
    )
    settings = Settings(table_name=TABLE, state_machine_arn="arn:x")
    ingest.start_investigation(settings, incident, AuditLog(TABLE, "default", "t"))
    events = [e["event"] for e in AuditLog(TABLE, "default", "t").for_incident(incident.id)]
    assert "investigation_start_failed" not in events
