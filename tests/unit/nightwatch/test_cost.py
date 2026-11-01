"""The cost guardian (F17): usage jumps, free tiers, capacity and budget alerts as incidents."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

import pytest

from nightwatch.agent.loop import initial_plan
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext, run_tool
from nightwatch.common.incidents import IncidentStore
from nightwatch.cost import handler as cost
from nightwatch.cost.signals import Signal, budget, daily
from nightwatch.cost.usage import Usage
from nightwatch.ingest import handler as ingest
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert

NOW = datetime(2026, 11, 1, 7, 30, tzinfo=UTC)
OCTOBER = [date(2026, 10, day) for day in range(17, 32)]  # 17 to 31 October
GROUP = "/aws/lambda/shoplite-api"


class Watch:
    """CloudWatch with a daily Sum per (metric, first dimension value); "" for none."""

    def __init__(self, data: dict[tuple[str, str], dict[date, float]]) -> None:
        self.data = data

    def get_metric_statistics(self, **kw: Any) -> dict[str, Any]:
        key = (kw["MetricName"], kw["Dimensions"][0]["Value"] if kw["Dimensions"] else "")
        points = []
        for day, value in self.data.get(key, {}).items():
            at = datetime.combine(day, time(), UTC)
            if kw["StartTime"] <= at < kw["EndTime"]:
                points.append({"Timestamp": at, "Sum": value})
        return {"Datapoints": points}


class Pages:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.pages


class Lambda:
    def get_function_configuration(self, **kwargs: Any) -> dict[str, Any]:
        return {"MemorySize": 256}

    def get_paginator(self, name: str) -> Pages:
        if name == "list_functions":
            return Pages([{"Functions": [{"FunctionName": "shoplite-api", "MemorySize": 256}]}])
        versions = [
            {"Version": "$LATEST", "LastModified": "2026-10-31T09:00:00.000+0000"},
            {"Version": "8", "LastModified": "2026-09-01T09:00:00.000+0000", "Description": "old"},
            {
                "Version": "9",
                "LastModified": "2026-10-30T22:15:00.000+0000",
                "Description": "sha=abc1234",
            },
        ]
        return Pages([{"Versions": versions}])


class Dynamo:
    def describe_table(self, TableName: str) -> dict[str, Any]:  # noqa: N803 - boto3's name
        if TableName == TABLE:
            index = {"ProvisionedThroughput": {"ReadCapacityUnits": 10, "WriteCapacityUnits": 5}}
            throughput = {"ReadCapacityUnits": 20, "WriteCapacityUnits": 5}
            return {
                "Table": {"ProvisionedThroughput": throughput, "GlobalSecondaryIndexes": [index]}
            }
        return {
            "Table": {"ProvisionedThroughput": {"ReadCapacityUnits": 5, "WriteCapacityUnits": 5}}
        }


def busy_october() -> Watch:
    requests = dict.fromkeys(OCTOBER[:-1], 2000.0) | {OCTOBER[-1]: 9_000.0}
    return Watch(
        {
            ("Invocations", "shoplite-api"): requests,
            ("Duration", "shoplite-api"): dict.fromkeys(OCTOBER, 100_000.0),
            ("IncomingBytes", GROUP): dict.fromkeys(OCTOBER, 1024.0**2),
            ("Invocations", ""): dict.fromkeys(OCTOBER, 40_000.0),  # the whole account
        }
    )


def test_a_jump_is_three_times_a_usual_day_above_a_floor() -> None:
    days = tuple(OCTOBER[-5:])
    jumped = Usage("f", "requests", days, (2_000.0, 2_000.0, 2_000.0, 2_000.0, 9_000.0))
    assert (jumped.usual, jumped.jumped, jumped.jump_day()) == (2_000.0, True, OCTOBER[-1])
    tiny = Usage("f", "requests", days, (1.0, 1.0, 1.0, 1.0, 900.0))  # 900 times, but tiny
    assert not tiny.jumped
    assert tiny.jump_day() is None


def test_the_daily_check_finds_jumps_free_tiers_and_capacity() -> None:
    signals = daily(busy_october(), Lambda(), Dynamo(), NOW, TABLE)
    assert [(s.alarm, s.service, s.severity) for s in signals] == [
        ("cost-guard:shoplite-api:requests", "shoplite", "medium"),
        ("cost-guard:free-tier:lambda-requests", "shoplite", "medium"),
        ("cost-guard:dynamodb-capacity", "shoplite", "high"),
    ]
    assert signals[0].summary == (
        "shoplite-api used 9,000 requests yesterday, 4.5 times a usual day (2,000 requests)."
    )
    assert "It would pass the free tier on 26 November." in signals[1].summary
    assert "provisioned with 35 read and 15 write units" in signals[2].summary


BUDGET = """AWS Budget Notification November 01, 2026
AWS Account 123456789012

Dear AWS Customer,

You requested that we alert you when the ACTUAL Cost associated with your
nightwatch-monthly-cost budget is greater than $0.01 for the current month. The ACTUAL Cost
associated with this budget is $0.02.

Budget Name: nightwatch-monthly-cost
Budget Type: Cost
Budgeted Amount: $1.00
Alert Type: ACTUAL
Alert Threshold: > $0.01
ACTUAL Amount: $0.02
"""


def test_budget_alerts_read_like_the_email() -> None:
    signal = budget(BUDGET, "AWS Budgets: alert", "shoplite")
    assert (signal.alarm, signal.severity) == ("cost-guard:budget:actual", "high")
    assert signal.summary == (
        "AWS Budget nightwatch-monthly-cost: actual spend is $0.02, past the alert at > $0.01 "
        "(budget $1.00)."
    )
    odd = budget("something new", "AWS Budgets: forecast over", "shoplite")
    assert (odd.alarm, odd.summary) == (
        "cost-guard:budget:forecasted",
        "AWS Budget alert: AWS Budgets: forecast over",
    )


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    now = [NOW]
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setattr(cost, "utcnow", lambda: now[0])
    monkeypatch.setattr(ingest, "utcnow", lambda: now[0])
    monkeypatch.setattr(cost, "_clients", lambda: (None, None, None))
    return now


def test_a_cost_signal_is_an_incident_until_the_check_stops_seeing_it(
    store: IncidentStore, clock: list[datetime], monkeypatch: pytest.MonkeyPatch
) -> None:
    firing = [Signal("cost-guard:shoplite-api:requests", "shoplite", "medium", "Many requests.")]
    monkeypatch.setattr(cost, "daily", lambda *args: list(firing))
    raised = ["cost-guard:shoplite-api:requests: INC-001"]
    assert cost.handler({}, None) == {"signals": raised, "cleared": []}
    incident = store.find(1)
    assert incident is not None
    assert (incident.status, incident.alarms) == (
        "open",
        {"cost-guard:shoplite-api:requests": "ALARM"},
    )
    assert cost.handler({}, None)["signals"] == raised  # the same morning: the same alert

    firing.clear()
    clock[0] = NOW + timedelta(days=1)
    cleared = ["INC-001 cost-guard:shoplite-api:requests"]
    assert cost.handler({}, None) == {"signals": [], "cleared": cleared}
    resolved = store.find(1)
    assert resolved is not None
    assert resolved.status == "resolved"


def test_a_budget_alert_opens_a_cost_incident(
    store: IncidentStore, clock: list[datetime], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cost, "service_usage", lambda *args, **kwargs: [])
    note = {"MessageId": "m-1", "Subject": "AWS Budgets: alert", "Message": BUDGET}
    event = {"Records": [{"EventSource": "aws:sns", "Sns": note}]}
    assert cost.handler(event, None) == {"budget_alerts": ["cost-guard:budget:actual: INC-001"]}
    incident = store.find(1)
    assert incident is not None
    assert incident.severity == "high"
    assert "actual spend is $0.02" in incident.title


def test_cost_incidents_are_investigated_from_daily_usage(store: IncidentStore) -> None:
    cost_alert = make_alert(alarm="cost-guard:shoplite-api:requests")
    incident = store.assign_number(store.open_incident(cost_alert, None, T0))
    ctx = ToolContext(incident=incident, service=service_info("shoplite"), now=T0, store=store)
    tools = [tool for tool, _, _ in initial_plan(ctx)]
    assert tools[:3] == ["get_service_info", "usage_history", "recent_changes"]
    assert "get_alarm_context" not in tools
    assert "query_logs" not in tools  # minute-scale logs say nothing about a day's usage

    class Aws:
        cloudwatch = busy_october()
        lambda_ = Lambda()

    ctx.aws, ctx.now = Aws(), NOW
    result = run_tool("usage_history", {"days": 14}, ctx)
    assert (
        "- shoplite-api requests: yesterday 9,000 requests, usual day 2,000 requests; "
        "jumped on 2026-10-31"
    ) in result.summary
    assert "- shoplite-api version 9 at 2026-10-30 22:15 UTC: sha=abc1234" in result.summary
    assert "version 8" not in result.summary  # published before the window
