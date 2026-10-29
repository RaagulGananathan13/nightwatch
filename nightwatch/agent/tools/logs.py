"""query_logs: CloudWatch Logs Insights over the service's own log groups.

Windows are capped at one hour and results at 20 rows, so queries stay cheap. Logs Insights
is billed per GB scanned, and these log groups hold megabytes (ADR 0007).
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolError, ToolResult

MAX_ROWS = 20
POLL_SECONDS = 1.0
MAX_WAIT_SECONDS = 20.0

# The deterministic queries every investigation runs first (spec 10.4: aggregate before raw).
ERRORS_BY_VERSION = (
    'filter level = "ERROR" | stats count(*) as errors by error_type, version '
    "| sort errors desc | limit 10"
)
REQUESTS_BY_VERSION = (
    'filter msg = "request" | stats count(*) as requests by version, level '
    "| sort requests desc | limit 10"
)
RUNTIME_ERRORS = (
    r"filter @message like /\[ERROR\]|Task timed out|Runtime exited|Runtime\.OutOfMemory/ "
    r"| parse @message /(?<error>\[ERROR\][^\r\n]*|Task timed out[^\r\n]*|Runtime exited[^\r\n]*"
    r"|Runtime\.OutOfMemory[^\r\n]*)/ "
    "| stats count(*) as count by error | sort count desc | limit 5"
)
LAMBDA_REPORTS = (
    # Lambda's own REPORT line per invocation: duration and memory, per function version
    # (the version is in the log stream name). Shows timeouts, slowness and memory limits.
    'filter @type = "REPORT" '
    r"| parse @logStream /\[(?<lambda_version>[^\]]+)\]/ "
    "| stats count(*) as invocations, pct(@duration, 95) as p95_ms, max(@duration) as max_ms, "
    "max(@maxMemoryUsed / 1000 / 1000) as max_memory_mb, max(@memorySize / 1000 / 1000) "
    "as memory_mb by lambda_version | sort lambda_version desc | limit 10"
)
# Columns that don't add up across rows (a sum of maxima or percentiles means nothing).
_NOT_ADDITIVE = re.compile(
    r"max|min|avg|mean|pct|p\d|_ms|_mb|latency|duration|memory|version", re.I
)


class Args(BaseModel):
    log_group: str = Field(max_length=512)
    query: str = Field(min_length=3, max_length=1000)
    minutes: int = Field(default=30, ge=1, le=60)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    if args.log_group not in ctx.service.log_groups:
        raise ToolError(f"log group '{args.log_group}' does not belong to {ctx.service.name}")
    query = args.query if "limit" in args.query.lower() else f"{args.query} | limit {MAX_ROWS}"
    end = ctx.now
    start = max(
        end - timedelta(minutes=60), ctx.incident.opened_at - timedelta(minutes=args.minutes)
    )
    query_id = ctx.aws.logs.start_query(
        logGroupName=args.log_group,
        startTime=int(start.timestamp()),
        endTime=int(end.timestamp()),
        queryString=query,
        limit=MAX_ROWS,
    )["queryId"]

    waited = 0.0
    response: dict[str, Any] = {"status": "Running", "results": []}
    while response["status"] in ("Scheduled", "Running") and waited < MAX_WAIT_SECONDS:
        ctx.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        response = ctx.aws.logs.get_query_results(queryId=query_id)
    if response["status"] != "Complete":
        ctx.aws.logs.stop_query(queryId=query_id)
        raise ToolError(f"query did not finish in {MAX_WAIT_SECONDS:.0f}s ({response['status']})")

    rows = [_row(fields) for fields in response.get("results", [])][:MAX_ROWS]
    window = f"{start:%H:%M}-{end:%H:%M} UTC"
    if not rows:
        return ToolResult(f"No matching log lines in {args.log_group} ({window}).", {"rows": []})
    lines = [f"{len(rows)} row(s) from {args.log_group} ({window}):"]
    lines += [" | ".join(f"{k}={v}" for k, v in row.items()) for row in rows]
    totals = _totals(rows)
    if totals:
        lines.append("Totals: " + ", ".join(f"{k}={v:g}" for k, v in totals.items()))
    data = {"rows": rows, "totals": totals, "query": query}
    return ToolResult("\n".join(lines), data, is_log=True)


def _totals(rows: list[dict[str, str]]) -> dict[str, float]:
    """Sums of numeric count columns, so counts like "120 errors in all" are in the evidence."""
    totals: dict[str, float] = {}
    for column in rows[0]:
        if column.startswith("@") or _NOT_ADDITIVE.search(column):
            continue
        try:
            totals[column] = sum(float(row[column]) for row in rows)
        except (KeyError, ValueError):
            continue
    return totals


def _row(fields: list[dict[str, Any]]) -> dict[str, str]:
    return {f["field"]: str(f.get("value", ""))[:300] for f in fields if f["field"] != "@ptr"}


TOOL = Tool(
    name="query_logs",
    description=(
        "Run a CloudWatch Logs Insights query on one of the service's log groups "
        "(window up to 60 minutes, at most 20 rows). Prefer stats over raw lines."
    ),
    args=Args,
    run=run,
)
