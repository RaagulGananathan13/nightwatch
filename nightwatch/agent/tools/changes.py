"""recent_changes: what changed shortly before the incident (spec F3, "what changed?").

CloudTrail's event history usually lags a few minutes, so it can miss a deploy made just
before the alarm. The primary sources are therefore read straight from the resources, which
is instant:
- Lambda versions published in the window. CI writes the commit SHA into each description.
- SSM parameter history: feature flags and settings, with who changed them.
- DynamoDB capacity changes.
CloudTrail then adds anything else it already has.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolResult

_SHA = re.compile(r"sha=([0-9a-f]{7,40})")
_PREV = re.compile(r"\bprev=(\d+)\b")  # the version live before this deploy
MAX_LINES = 12


class Args(BaseModel):
    window_minutes: int = Field(default=60, ge=10, le=180)


@dataclass(frozen=True)
class Change:
    at: datetime
    kind: str  # deploy, config, capacity, api_call
    resource: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    opened = ctx.incident.opened_at
    since, until = opened - timedelta(minutes=args.window_minutes), ctx.now
    changes: list[Change] = []
    live: dict[str, Any] = {}
    for function in ctx.service.lambda_functions:
        found, live[function] = _deploys(ctx, function, since, until)
        changes += found
    parameters: dict[str, str] = {}
    for name in ctx.service.ssm_parameters:
        found, now = _parameter_changes(ctx, name, since, until)
        changes += found
        if now is not None:
            parameters[name] = now
    for table in ctx.service.dynamodb_tables:
        changes += _capacity_changes(ctx, table, since, until)
    trail = _cloudtrail(ctx, since, until)
    changes += trail
    changes.sort(key=lambda change: change.at)
    changes = changes[-MAX_LINES:]

    header = (
        f"Changes to {ctx.service.name} from {since:%H:%M} to {until:%H:%M} UTC "
        f"(incident opened {opened:%H:%M:%S} UTC):"
    )
    if changes:
        lines = [header] + [f"{_offset(c.at, opened)}: {c.summary}" for c in changes]
    else:
        lines = [header, "No deploys, configuration or capacity changes found."]
    if not trail:
        lines.append("(CloudTrail had no matching events; its history can lag about 5 minutes.)")
    for function, info in live.items():
        if info:
            lines.append(f"Live now: {function} version {info['version']} ({info['description']}).")
    for name, value in parameters.items():
        lines.append(f"Now: {name} = {value}")
    data = {
        "changes": [
            {
                "at": c.at.isoformat(),
                "kind": c.kind,
                "resource": c.resource,
                "summary": c.summary,
                **c.details,
            }
            for c in changes
        ],
        "live": live,
        "parameters": parameters,
    }
    return ToolResult("\n".join(lines), data)


def _deploys(
    ctx: ToolContext, function: str, since: datetime, until: datetime
) -> tuple[list[Change], dict[str, Any]]:
    alias_version = None
    try:
        alias = ctx.aws.lambda_.get_alias(FunctionName=function, Name=ctx.service.lambda_alias)
        alias_version = alias["FunctionVersion"]
    except ClientError:
        pass
    versions: list[dict[str, Any]] = []
    for page in ctx.aws.lambda_.get_paginator("list_versions_by_function").paginate(
        FunctionName=function
    ):
        versions += [v for v in page["Versions"] if v["Version"] != "$LATEST"]
    by_number = sorted(versions, key=lambda v: int(v["Version"]))

    changes = []
    for index, version in enumerate(by_number):
        at = _lambda_time(version["LastModified"])
        if not since <= at <= until:
            continue
        description = version.get("Description", "") or "no description"
        is_live = version["Version"] == alias_version
        previous = by_number[index - 1]["Version"] if index else None
        summary = f"{function} version {version['Version']} published"
        summary += " and now live" if is_live else ""
        summary += f" ({description})"
        rollback_to = _PREV.search(description)
        if rollback_to:
            summary += f"; replaced version {rollback_to.group(1)}, which was live before it"
        elif previous:
            summary += f"; previous version {previous}"
        details: dict[str, Any] = {
            "version": version["Version"],
            "description": description,
            "live": is_live,
            "previous_version": previous,
        }
        sha = _SHA.search(description)
        if sha:
            details["sha"] = sha.group(1)
            commit = _commit(ctx, sha.group(1))
            if commit:
                details["commit"] = commit
                summary += f"; commit '{commit['message']}' by {commit['author']}"
        if rollback_to:
            details["rollback_to"] = rollback_to.group(1)
            replaced = next((v for v in versions if v["Version"] == rollback_to.group(1)), None)
            if replaced:
                difference = _difference(replaced, version)
                details.update(difference)
                summary += "; " + _describe(difference, rollback_to.group(1))
        changes.append(Change(at, "deploy", function, summary, details))

    live_info: dict[str, Any] = {}
    if alias_version:
        current = next((v for v in versions if v["Version"] == alias_version), None)
        live_info = {
            "version": alias_version,
            "description": (current or {}).get("Description", "") or "no description",
        }
    return changes, live_info


def _difference(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """What a deploy changed: the code, and which environment variables (names only, since
    values can be secrets)."""
    old = (before.get("Environment") or {}).get("Variables") or {}
    new = (after.get("Environment") or {}).get("Variables") or {}
    return {
        "code_changed": before.get("CodeSha256") != after.get("CodeSha256"),
        "env_changed": sorted(
            name for name in old.keys() | new.keys() if old.get(name) != new.get(name)
        ),
    }


def _describe(difference: dict[str, Any], replaced: str) -> str:
    names = ", ".join(difference["env_changed"])
    if difference["code_changed"] and names:
        return f"new code and changed environment variables ({names}) vs version {replaced}"
    if difference["code_changed"]:
        return f"new code vs version {replaced}, same environment variables"
    if names:
        return f"same code as version {replaced}, but environment variables changed: {names}"
    return f"same code and settings as version {replaced}"


def _commit(ctx: ToolContext, sha: str) -> dict[str, str] | None:
    """Commit message and author from GitHub, when the repo is public and reachable."""
    if not ctx.http_get or not ctx.service.repository:
        return None
    body = ctx.http_get(f"https://api.github.com/repos/{ctx.service.repository}/commits/{sha}")
    if not body or "commit" not in body:
        return None
    message = str(body["commit"].get("message", "")).splitlines()[0][:120]
    author = (body.get("author") or {}).get("login") or body["commit"].get("author", {}).get(
        "name", "?"
    )
    return {"message": message, "author": str(author)}


def _parameter_changes(
    ctx: ToolContext, name: str, since: datetime, until: datetime
) -> tuple[list[Change], str | None]:
    """Changes in the window, and the value it holds now (the newest version)."""
    changes = []
    newest: tuple[int, str] | None = None
    paginator = ctx.aws.ssm.get_paginator("get_parameter_history")
    for page in paginator.paginate(Name=name, WithDecryption=False):
        for item in page["Parameters"]:
            value = (
                item.get("Value", "") if item.get("Type") == "String" else "(secure value hidden)"
            )
            if newest is None or int(item["Version"]) > newest[0]:
                newest = (int(item["Version"]), value[:200])
            at = item["LastModifiedDate"]
            if not since <= at <= until:
                continue
            who = str(item.get("LastModifiedUser", "unknown")).rsplit("/", 1)[-1]
            summary = f"parameter {name} set to {value[:200]} (version {item['Version']}, by {who})"
            changes.append(Change(at, "config", name, summary, {"value": value[:500], "by": who}))
    return changes, newest[1] if newest else None


def _capacity_changes(
    ctx: ToolContext, table: str, since: datetime, until: datetime
) -> list[Change]:
    description = ctx.aws.dynamodb.describe_table(TableName=table)["Table"]
    throughput = description.get("ProvisionedThroughput", {})
    units = (
        f"{throughput.get('ReadCapacityUnits')} read / "
        f"{throughput.get('WriteCapacityUnits')} write units"
    )
    changes = []
    for key, verb in (("LastDecreaseDateTime", "lowered"), ("LastIncreaseDateTime", "raised")):
        at = throughput.get(key)
        if at and since <= at <= until:
            changes.append(
                Change(
                    at,
                    "capacity",
                    table,
                    f"DynamoDB table {table} capacity {verb}, now {units}",
                    {
                        "read": throughput.get("ReadCapacityUnits"),
                        "write": throughput.get("WriteCapacityUnits"),
                    },
                )
            )
    return changes


def _cloudtrail(ctx: ToolContext, since: datetime, until: datetime) -> list[Change]:
    try:
        events = ctx.aws.cloudtrail.lookup_events(
            LookupAttributes=[{"AttributeKey": "ReadOnly", "AttributeValue": "false"}],
            StartTime=since,
            EndTime=until,
            MaxResults=50,
        )["Events"]
    except (BotoCoreError, ClientError):
        return []
    names = ctx.service.resources | set(ctx.service.ssm_parameters)
    changes = []
    for event in events:
        resources = [str(r.get("ResourceName", "")) for r in event.get("Resources", [])]
        if not any(name in resource for resource in resources for name in names):
            continue
        who = event.get("Username", "unknown")
        summary = f"API call {event['EventName']} on {', '.join(resources)[:150]} by {who}"
        changes.append(Change(event["EventTime"], "api_call", resources[0], summary))
    return changes[:10]


def _lambda_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%f%z")


def _offset(at: datetime, opened: datetime) -> str:
    seconds = int((at - opened).total_seconds())
    sign = "-" if seconds < 0 else "+"
    minutes, rest = divmod(abs(seconds), 60)
    when = "before" if seconds < 0 else "after"
    return f"{sign}{minutes}m {rest:02d}s ({when} the incident)"


TOOL = Tool(
    name="recent_changes",
    description=(
        "Deploys (Lambda versions with commit SHA), configuration and capacity changes, and "
        "CloudTrail write events for the service in the last N minutes."
    ),
    args=Args,
    run=run,
)
