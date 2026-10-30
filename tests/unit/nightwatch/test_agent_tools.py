"""Each read-only tool, its allow-list checks, and the summaries it writes."""

from datetime import timedelta
from typing import Any

import pytest

from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext, ToolError, run_tool
from nightwatch.common.models import Incident
from tests.unit.nightwatch.fakes import (
    BAD_SHA,
    T0,
    FakeAws,
    FakeDynamo,
    FakeLambda,
    FakeLogs,
    FakeSsm,
)


def context(aws: Any = None, store: Any = None) -> ToolContext:
    incident = Incident.model_validate(
        {
            "id": "01JB0000000000000000000001",
            "tenant_id": "default",
            "number": 3,
            "service": "shoplite",
            "title": "shoplite-lambda-errors",
            "first_alarm": "shoplite-lambda-errors",
            "opened_at": T0,
            "updated_at": T0,
            "last_alert_at": T0,
            "alarms": {"shoplite-lambda-errors": "ALARM"},
        }
    )
    return ToolContext(
        incident=incident,
        service=service_info("shoplite"),
        now=T0 + timedelta(minutes=2),
        store=store,
        aws=aws or FakeAws(),
    )


def test_unknown_tools_and_bad_arguments_are_refused() -> None:
    with pytest.raises(ToolError, match="unknown tool"):
        run_tool("delete_table", {}, context())
    with pytest.raises(ToolError, match="bad arguments"):
        run_tool("query_logs", {"log_group": "/aws/lambda/shoplite-api"}, context())


def test_logs_stay_inside_the_service_and_get_limits() -> None:
    with pytest.raises(ToolError, match="does not belong"):
        run_tool(
            "query_logs", {"log_group": "/aws/lambda/other", "query": "stats count()"}, context()
        )
    logs = FakeLogs(
        extra_rows=[
            {"route": "GET /products", "errors": "40"},
            {"route": "POST /orders", "errors": "17"},
        ]
    )
    result = run_tool(
        "query_logs",
        {"log_group": "/aws/lambda/shoplite-api", "query": "stats count() as errors by route"},
        context(FakeAws(logs=logs)),
    )
    assert logs.queries[0].endswith("| limit 20")
    assert result.is_log
    assert "Totals: errors=57" in result.summary


def test_metric_summary_compares_baseline_and_now() -> None:
    result = run_tool(
        "get_metric_series",
        {
            "namespace": "AWS/Lambda",
            "metric": "Errors",
            "dimensions": {"FunctionName": "shoplite-api"},
        },
        context(),
    )
    assert "baseline 0.00" in result.summary
    assert "15.00 since" in result.summary
    assert "first rise at 09:59 UTC" in result.summary
    with pytest.raises(ToolError, match="namespace"):
        run_tool(
            "get_metric_series", {"namespace": "AWS/EC2", "metric": "CPUUtilization"}, context()
        )


def test_recent_changes_finds_the_deploy_with_its_commit() -> None:
    commits = {"message": "read prices from the new price field", "author": {"login": "someone"}}
    ctx = context()
    ctx.http_get = lambda url: (
        {"commit": commits, "author": {"login": "someone"}} if BAD_SHA in url else None
    )
    result = run_tool("recent_changes", {"window_minutes": 60}, ctx)
    assert (
        "-2m 00s (before the incident): shoplite-api version 5 published and now live"
        in result.summary
    )
    assert "previous version 4" in result.summary
    deploy = result.data["changes"][0]
    assert deploy["sha"] == BAD_SHA
    assert deploy["live"] is True
    assert deploy["commit"]["author"] == "someone"
    assert "version 4" not in result.data["changes"][0]["summary"].split(";")[0]


def test_recent_changes_names_the_version_that_was_live_before_the_deploy() -> None:
    # Version 4 is the previous number, but version 3 was live: 4 may be a bad build too.
    result = run_tool("recent_changes", {}, context(FakeAws(lambda_=FakeLambda(prev="3"))))
    assert "replaced version 3, which was live before it" in result.summary
    assert "previous version 4" not in result.summary
    assert result.data["changes"][0]["rollback_to"] == "3"
    assert result.data["changes"][0]["sha"] == BAD_SHA


def test_recent_changes_says_what_a_deploy_changed_without_values() -> None:
    code = run_tool("recent_changes", {}, context(FakeAws(lambda_=FakeLambda(prev="4"))))
    assert "new code vs version 4, same environment variables" in code.summary
    settings_only = FakeLambda(prev="4", table="shoplite-catalogue", same_code=True)
    result = run_tool("recent_changes", {}, context(FakeAws(lambda_=settings_only)))
    assert "same code as version 4, but environment variables changed: TABLE_NAME" in result.summary
    assert result.data["changes"][0]["env_changed"] == ["TABLE_NAME"]
    assert result.data["changes"][0]["code_changed"] is False
    assert "shoplite-catalogue" not in result.summary + str(result.data)  # values stay hidden


def test_recent_changes_sees_flags_and_capacity() -> None:
    history = [
        {
            "Version": 7,
            "Type": "String",
            "Value": '{"error_rate": 0.8}',
            "LastModifiedDate": T0 - timedelta(minutes=1),
            "LastModifiedUser": "arn:aws:iam::111122223333:user/nightwatch/nightwatch-deployer",
        }
    ]
    aws = FakeAws(ssm=FakeSsm(history), dynamodb=FakeDynamo(lowered_at=T0 - timedelta(minutes=4)))
    summary = run_tool("recent_changes", {}, context(aws)).summary
    assert (
        'parameter /shoplite/flags set to {"error_rate": 0.8} (version 7, by nightwatch-deployer)'
        in summary
    )
    assert "DynamoDB table shoplite capacity lowered, now 5 read / 1 write units" in summary
    assert 'Now: /shoplite/flags = {"error_rate": 0.8}' in summary  # the value it holds today


def test_describe_resource_never_shows_secret_values() -> None:
    result = run_tool("describe_resource", {"name": "shoplite-api"}, context())
    assert "API_TOKEN" in result.summary
    assert "not-a-real-token" not in result.summary + str(result.data)
    with pytest.raises(ToolError, match="not a resource"):
        run_tool("describe_resource", {"name": "billing-api"}, context())


def test_alarm_context_only_for_this_incidents_alarms() -> None:
    result = run_tool("get_alarm_context", {"alarm_name": "shoplite-lambda-errors"}, context())
    assert "fires when GreaterThanOrEqualToThreshold 2.0" in result.summary
    with pytest.raises(ToolError, match="not part of this incident"):
        run_tool("get_alarm_context", {"alarm_name": "billing-errors"}, context())


def test_service_info_from_the_catalogue() -> None:
    result = run_tool("get_service_info", {"service": "shoplite"}, context())
    assert "shoplite-api" in result.summary
    assert "slo" not in result.data  # a target, not an observation: it can't back a claim
    with pytest.raises(ToolError, match="no service"):
        run_tool("get_service_info", {"service": "nope"}, context())
