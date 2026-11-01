"""usage_history: the service's daily usage over the last weeks, and when it jumped (spec F17).

For cost incidents, which are measured in days rather than minutes: each function's requests
and GB-seconds and each log group's bytes per UTC day, the day each one jumped, and the Lambda
versions published in that time, so a jump can be lined up with a deploy.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolResult
from nightwatch.cost.usage import amount, service_usage


class Args(BaseModel):
    days: int = Field(default=14, ge=3, le=30)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    found = service_usage(ctx.aws.cloudwatch, ctx.aws.lambda_, ctx.service, ctx.now, args.days)
    if not found:
        return ToolResult(f"{ctx.service.name} has no functions or log groups to measure.")
    first, last = found[0].days[0], found[0].days[-1]
    lines = [f"Daily usage of {ctx.service.name}, {first} to {last} (complete UTC days):"]
    for usage in found:
        jump = usage.jump_day()
        lines.append(
            f"- {usage.resource} {usage.measure}: yesterday "
            f"{amount(usage.yesterday, usage.measure)}, usual day "
            + (amount(usage.usual, usage.measure) if usage.usual is not None else "unknown yet")
            + "; "
            + (f"jumped on {jump.isoformat()}" if jump else "no jump")
        )
    versions = _versions(ctx, first)
    lines.append(
        "Lambda versions published in that time:"
        if versions
        else "No Lambda versions were published in that time."
    )
    lines += [
        f"- {v['function']} version {v['version']} at {v['at']}: {v['about']}" for v in versions
    ]
    data = {
        "usage": [
            {
                "resource": u.resource,
                "measure": u.measure,
                "days": [day.isoformat() for day in u.days],
                "values": [round(value, 2) for value in u.values],
            }
            for u in found
        ],
        "versions": versions,
    }
    return ToolResult("\n".join(lines), data)


def _versions(ctx: ToolContext, since: date) -> list[dict[str, Any]]:
    """Versions of the service's functions published on or after ``since``, oldest first."""
    published: list[dict[str, Any]] = []
    for function in ctx.service.lambda_functions:
        pages = ctx.aws.lambda_.get_paginator("list_versions_by_function").paginate(
            FunctionName=function
        )
        for page in pages:
            for version in page.get("Versions", []):
                at = str(version.get("LastModified", ""))[:16]
                if version.get("Version") != "$LATEST" and at[:10] >= since.isoformat():
                    published.append(
                        {
                            "function": function,
                            "version": str(version.get("Version")),
                            "at": at.replace("T", " ") + " UTC",
                            "about": str(version.get("Description", ""))[:120],
                        }
                    )
    return sorted(published, key=lambda v: v["at"])[-20:]


TOOL = Tool(
    name="usage_history",
    description=(
        "The service's daily usage for the last weeks (requests, GB-seconds, log bytes), the "
        "day each jumped, and the Lambda versions published meanwhile. For cost incidents."
    ),
    args=Args,
    run=run,
)
