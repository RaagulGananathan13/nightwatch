"""The emergency stop: what trips it, what it switches off, and how `make resume` undoes it."""

from datetime import UTC, datetime
from typing import Any

import pytest
from botocore.exceptions import ClientError

from nightwatch.common.incidents import IncidentStore
from nightwatch.cost import handler as cost
from nightwatch.cost.signals import Signal, budget_spend
from nightwatch.cost.stop import stop
from nightwatch.ingest import handler as ingest
from scripts.resume import resume
from tests.unit.nightwatch.conftest import TABLE

NOW = datetime(2026, 11, 3, 7, 30, tzinfo=UTC)
FUNCTIONS = ["nightwatch-cost", "nightwatch-ingest", "shoplite-api", "geniusbees-web"]


class Pages:
    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages

    def paginate(self, **kwargs: Any) -> list[dict[str, Any]]:
        return self.pages


class Lambda:
    def __init__(self, reserved: dict[str, int] | None = None, broken: str = "") -> None:
        self.set: list[str] = []
        self.deleted: list[str] = []
        self.reserved = reserved or {}
        self.broken = broken

    def get_paginator(self, name: str) -> Pages:
        return Pages([{"Functions": [{"FunctionName": name} for name in FUNCTIONS]}])

    def put_function_concurrency(self, **kwargs: Any) -> None:
        assert kwargs["ReservedConcurrentExecutions"] == 0
        if kwargs["FunctionName"] == self.broken:
            raise ClientError({"Error": {"Code": "Throttled", "Message": "x"}}, "Put")
        self.set.append(kwargs["FunctionName"])

    def get_function_concurrency(self, FunctionName: str) -> dict[str, Any]:  # noqa: N803
        if FunctionName in self.reserved:
            return {"ReservedConcurrentExecutions": self.reserved[FunctionName]}
        return {}

    def delete_function_concurrency(self, FunctionName: str) -> None:  # noqa: N803
        self.deleted.append(FunctionName)


class Ssm:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put_parameter(self, **kwargs: Any) -> None:
        self.values[kwargs["Name"]] = kwargs["Value"]


class Dynamo:
    def __init__(self, units: int) -> None:
        self.units = units
        self.updates: list[dict[str, Any]] = []

    def describe_table(self, TableName: str) -> dict[str, Any]:  # noqa: N803
        throughput = {"ReadCapacityUnits": self.units, "WriteCapacityUnits": 5}
        index = {"IndexName": "GSI1", "ProvisionedThroughput": throughput}
        return {"Table": {"ProvisionedThroughput": throughput, "GlobalSecondaryIndexes": [index]}}

    def update_table(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


def test_the_stop_tells_chat_first_and_switches_itself_off_last() -> None:
    told: list[dict[str, Any]] = []
    lambda_, ssm, dynamo = Lambda(broken="nightwatch-ingest"), Ssm(), Dynamo(units=5)
    stopped = stop(
        "Too much.", lambda_, ssm, dynamo, "/nightwatch/kill_switch", ["nightwatch"],
        told.append, me="nightwatch-cost",
    )  # fmt: skip
    assert "NightWatch emergency stop" in told[0]["content"]
    assert ssm.values == {"/nightwatch/kill_switch": "true"}
    assert lambda_.set == ["shoplite-api", "nightwatch-cost"]  # itself last; not someone else's
    assert stopped.problems == ["nightwatch-ingest: ClientError"]  # one failure stops nothing
    assert (stopped.tables, dynamo.updates) == ([], [])  # inside the free tier: left alone


def test_tables_past_the_free_tier_shrink_to_one_unit() -> None:
    dynamo = Dynamo(units=20)  # 20 + 20 read units on two tables: 40, past the free 25
    stopped = stop("x", Lambda(), Ssm(), dynamo, "/k", ["nightwatch", "shoplite"], lambda m: None)
    assert stopped.tables == ["nightwatch", "shoplite"]
    update = dynamo.updates[0]
    assert update["ProvisionedThroughput"] == {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}
    assert update["GlobalSecondaryIndexUpdates"][0]["Update"]["IndexName"] == "GSI1"


def test_budget_alerts_say_how_much_was_spent() -> None:
    assert budget_spend("Alert Type: ACTUAL\nACTUAL Amount: $0.62") == ("ACTUAL", 0.62)
    assert budget_spend("Alert Type: FORECASTED\nFORECASTED Amount: $1,204.50") == (
        "FORECASTED",
        1204.5,
    )
    assert budget_spend("something new") == ("ACTUAL", None)


@pytest.fixture
def stops(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    reasons: list[str] = []
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setattr(cost, "utcnow", lambda: NOW)
    monkeypatch.setattr(ingest, "utcnow", lambda: NOW)
    monkeypatch.setattr(cost, "_clients", lambda: (None, None, None))
    monkeypatch.setattr(cost, "service_usage", lambda *args, **kwargs: [])
    monkeypatch.setattr(cost, "emergency_stop", lambda settings, reason: reasons.append(reason))
    return reasons


def budget_event(kind: str, amount: str) -> dict[str, Any]:
    message = f"Alert Type: {kind}\n{kind} Amount: {amount}\nBudget Name: nightwatch-monthly-cost"
    note = {"MessageId": f"m-{kind}-{amount}", "Subject": "AWS Budgets", "Message": message}
    return {"Records": [{"EventSource": "aws:sns", "Sns": note}]}


def test_only_real_spend_past_the_stop_line_stops_everything(stops: list[str]) -> None:
    cost.handler(budget_event("ACTUAL", "$0.02"), None)  # the usual penny: an incident only
    cost.handler(budget_event("FORECASTED", "$5.00"), None)  # a forecast: an incident only
    assert stops == []
    cost.handler(budget_event("ACTUAL", "$0.61"), None)
    assert stops == ["AWS reports $0.61 of real spend this month, past the $0.50 stop line."]


def test_the_runaway_alarm_stops_everything(stops: list[str]) -> None:
    alarm = {"alarmName": "nightwatch-runaway-invocations", "state": {"value": "ALARM"}}
    assert cost.handler({"source": "aws.cloudwatch", "alarmData": alarm}, None) == {"stopped": None}
    calm = {**alarm, "state": {"value": "OK"}}
    assert cost.handler({"source": "aws.cloudwatch", "alarmData": calm}, None) == {
        "alarm": "not firing"
    }
    assert len(stops) == 1
    assert stops[0].startswith("Lambda calls ran away")


def test_the_morning_check_stops_only_for_money_already_spent(
    stops: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    jump = Signal("cost-guard:shoplite-api:requests", "shoplite", "medium", "A jump.")
    heading = Signal("cost-guard:free-tier:lambda-requests", "shoplite", "medium", "Heading.")
    monkeypatch.setattr(cost, "daily", lambda *args: [jump, heading])
    cost.handler({}, None)
    assert stops == []  # warnings, not money
    used_up = Signal("cost-guard:free-tier:lambda-requests", "shoplite", "high", "Used up.")
    monkeypatch.setattr(cost, "daily", lambda *args: [used_up])
    cost.handler({}, None)
    assert stops == ["The morning check found real cost. Used up."]


def test_resume_switches_back_on_only_what_the_stop_switched_off() -> None:
    lambda_ = Lambda(reserved={"nightwatch-cost": 0, "shoplite-api": 0, "nightwatch-ingest": 5})
    ssm = Ssm()
    assert resume(lambda_, ssm, say=lambda line: None) == ["nightwatch-cost", "shoplite-api"]
    assert lambda_.deleted == ["nightwatch-cost", "shoplite-api"]
    assert ssm.values == {"/nightwatch/kill_switch": "false"}
