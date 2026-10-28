"""The ingest Lambda end to end: events in, one incident and one edited Discord message out."""

from datetime import timedelta
from typing import Any

import boto3
import pytest

from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.ingest import handler as ingest
from tests.unit.nightwatch.conftest import T0, TABLE, alarm_event

TAGS = {"nightwatch:service": "shoplite", "nightwatch:severity": "high"}


class FakeWebhook:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    def send(self, message: dict[str, Any]) -> str:
        self.sent.append(message)
        return str(1000 + len(self.sent))

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        self.edits.append((message_id, message))


@pytest.fixture
def lambda_env(aws: None, monkeypatch: pytest.MonkeyPatch) -> FakeWebhook:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("DISCORD_WEBHOOK_PARAM", "/nightwatch/discord/webhook_url")
    monkeypatch.setattr(ingest, "alarm_tags", lambda arn: TAGS)
    # Freeze "now" next to the events' timestamps, so results don't depend on when tests run.
    monkeypatch.setattr(ingest, "utcnow", lambda: T0 + timedelta(minutes=1))
    hook = FakeWebhook()
    monkeypatch.setattr(ingest, "_webhook", lambda settings: hook)
    return hook


def run(event: dict[str, Any]) -> dict[str, Any]:
    return ingest.handler(event, None)


def test_storm_of_alarms_makes_one_incident_and_one_message(lambda_env: FakeWebhook) -> None:
    assert run(alarm_event("shoplite-5xx-rate")) == {"outcome": "opened", "incident": "INC-001"}
    assert run(alarm_event("shoplite-lambda-errors"))["outcome"] == "grouped"
    assert run(alarm_event("shoplite-latency-p95"))["outcome"] == "grouped"

    assert len(lambda_env.sent) == 1
    assert "INC-001 opened" in lambda_env.sent[0]["content"]
    assert lambda_env.edits[-1][0] == "1001"
    assert "3 alerts grouped into 1 incident" in lambda_env.edits[-1][1]["content"]

    later = T0 + timedelta(minutes=5)
    for alarm in ("shoplite-5xx-rate", "shoplite-lambda-errors", "shoplite-latency-p95"):
        result = run(alarm_event(alarm, "OK", at=later))
    assert result["outcome"] == "resolved"
    assert "INC-001 resolved" in lambda_env.edits[-1][1]["content"]
    assert len(lambda_env.sent) == 1

    store = IncidentStore(TABLE, "default")
    (incident,) = store.list_incidents("resolved")
    events = [entry["event"] for entry in AuditLog(TABLE, "default", "t").for_incident(incident.id)]
    assert events.count("notification_sent") == 1
    assert events[0] == "incident_opened"
    assert "incident_resolved" in events


def test_redelivered_event_changes_nothing(lambda_env: FakeWebhook) -> None:
    event = alarm_event()
    run(event)
    assert run(event)["outcome"] == "duplicate"
    assert len(lambda_env.sent) == 1


def test_unmanaged_alarms_are_ignored(
    lambda_env: FakeWebhook, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(ingest, "alarm_tags", lambda arn: {})
    assert run(alarm_event()) == {"outcome": "ignored"}
    assert lambda_env.sent == []


def test_discord_failure_does_not_lose_the_incident(
    lambda_env: FakeWebhook, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nightwatch.chat.discord import DiscordError

    def broken(message: dict[str, Any]) -> str:
        raise DiscordError("Discord answered HTTP 500")

    monkeypatch.setattr(lambda_env, "send", broken)
    assert run(alarm_event())["outcome"] == "opened"
    (incident,) = IncidentStore(TABLE, "default").list_incidents("open")
    events = [entry["event"] for entry in AuditLog(TABLE, "default", "t").for_incident(incident.id)]
    assert "notification_failed" in events


def test_alarm_tags_are_read_from_cloudwatch_and_cached(aws: None) -> None:
    cloudwatch = boto3.client("cloudwatch")
    cloudwatch.put_metric_alarm(
        AlarmName="shoplite-5xx-rate",
        MetricName="Url5xxCount",
        Namespace="AWS/Lambda",
        Statistic="Sum",
        Period=60,
        EvaluationPeriods=1,
        Threshold=1,
        ComparisonOperator="GreaterThanThreshold",
        Tags=[{"Key": "nightwatch:service", "Value": "shoplite"}],
    )
    arn = cloudwatch.describe_alarms()["MetricAlarms"][0]["AlarmArn"]
    ingest._tag_cache.clear()
    ingest._cloudwatch.cache_clear()
    assert ingest.alarm_tags(arn) == {"nightwatch:service": "shoplite"}
    assert arn in ingest._tag_cache
    assert ingest.alarm_tags("arn:aws:cloudwatch:us-east-2:123456789012:alarm:missing") == {}
