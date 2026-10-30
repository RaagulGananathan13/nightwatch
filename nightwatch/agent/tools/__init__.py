"""Read-only investigation tools (spec F3), one module per tool, each with an argument schema.

The registry below is the agent's entire reach. There are no write tools at all, and every
tool checks its arguments against the service catalogue. A prompt injected through a log line
can therefore name a tool or a resource, but it can't make NightWatch change anything or read
outside the incident's service.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from typing import TYPE_CHECKING, Any

import boto3
from pydantic import BaseModel, ValidationError

from nightwatch.agent.services import ServiceInfo
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident

if TYPE_CHECKING:
    from mypy_boto3_cloudtrail import CloudTrailClient
    from mypy_boto3_cloudwatch import CloudWatchClient
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_lambda import LambdaClient
    from mypy_boto3_logs import CloudWatchLogsClient
    from mypy_boto3_ssm import SSMClient


class ToolError(Exception):
    """The request was refused (unknown tool, bad arguments, resource out of scope)."""


@dataclass(frozen=True)
class ToolResult:
    summary: str  # compact text for the LLM and the chat card
    data: Any = None  # structured result for the guard, the rules and the dashboard
    is_log: bool = False  # summary is raw log text: wrapped in <log> tags for the LLM


class AwsClients:
    """boto3 clients, created on first use (tests swap in fakes)."""

    @cached_property
    def cloudwatch(self) -> CloudWatchClient:
        return boto3.client("cloudwatch")

    @cached_property
    def logs(self) -> CloudWatchLogsClient:
        return boto3.client("logs")

    @cached_property
    def lambda_(self) -> LambdaClient:
        return boto3.client("lambda")

    @cached_property
    def dynamodb(self) -> DynamoDBClient:
        return boto3.client("dynamodb")

    @cached_property
    def ssm(self) -> SSMClient:
        return boto3.client("ssm")

    @cached_property
    def cloudtrail(self) -> CloudTrailClient:
        return boto3.client("cloudtrail")


@dataclass
class ToolContext:
    incident: Incident
    service: ServiceInfo
    now: datetime
    store: IncidentStore
    aws: Any = field(default_factory=AwsClients)
    sleep: Callable[[float], None] = field(default=lambda seconds: None)
    http_get: Callable[[str], dict[str, Any] | None] | None = None  # GitHub lookups
    question: str | None = None  # set for /nw ask: answer this instead of an alarm


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[ToolContext, Any], ToolResult]


def run_tool(name: str, raw_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
    tool = registry().get(name)
    if tool is None:
        raise ToolError(f"unknown tool '{name[:60]}'; only the read-only tools listed exist")
    try:
        args = tool.args.model_validate(raw_args)
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        raise ToolError(f"bad arguments for {name}: {problems[:300]}") from None
    return tool.run(ctx, args)


def catalog() -> str:
    """One line per tool, with its JSON argument schema, for the system prompt."""
    lines = []
    for tool in registry().values():
        props = tool.args.model_json_schema().get("properties", {})
        args = {key: value.get("type", "string") for key, value in props.items()}
        lines.append(f"- {tool.name}({json.dumps(args)}): {tool.description}")
    return "\n".join(lines)


def registry() -> dict[str, Tool]:
    from nightwatch.agent.tools import alarm, changes, logs, metrics, resource, service, similar

    tools = [
        alarm.TOOL,
        changes.TOOL,
        logs.TOOL,
        metrics.TOOL,
        resource.TOOL,
        service.TOOL,
        similar.TOOL,
    ]
    return {tool.name: tool for tool in tools}
