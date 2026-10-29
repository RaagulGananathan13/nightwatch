"""get_metric_series: a metric before and during the incident, summarised in code (spec 10.4).

Uses GetMetricStatistics, which counts toward CloudWatch's free API allowance; GetMetricData
is always billed (ADR 0007). The LLM gets the summary (baseline, now, change, when it moved),
not raw datapoints.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from statistics import fmean
from typing import Literal

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolError, ToolResult

ALLOWED_NAMESPACES = {"AWS/Lambda", "AWS/DynamoDB", "AWS/States", "AWS/SQS", "AWS/Events"}
Stat = Literal["Sum", "Average", "Maximum", "Minimum", "SampleCount", "p50", "p90", "p95", "p99"]


class Args(BaseModel):
    namespace: str
    metric: str = Field(max_length=255)
    dimensions: dict[str, str] = Field(default_factory=dict, max_length=4)
    stat: Stat = "Sum"
    minutes: int = Field(default=45, ge=10, le=180)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    if args.namespace not in ALLOWED_NAMESPACES:
        raise ToolError(f"namespace {args.namespace} is not one NightWatch reads")
    start = ctx.incident.opened_at - timedelta(minutes=args.minutes)
    request = {
        "Namespace": args.namespace,
        "MetricName": args.metric,
        "Dimensions": [{"Name": k, "Value": v} for k, v in args.dimensions.items()],
        "StartTime": start,
        "EndTime": ctx.now,
        "Period": 60,
    }
    if args.stat.startswith("p"):
        request["ExtendedStatistics"] = [args.stat]
    else:
        request["Statistics"] = [args.stat]
    points = sorted(
        ctx.aws.cloudwatch.get_metric_statistics(**request)["Datapoints"],
        key=lambda p: p["Timestamp"],
    )
    series = [(p["Timestamp"], _value(p, args.stat)) for p in points]
    label = f"{args.namespace}/{args.metric} ({args.stat} per minute"
    label += f", {', '.join(f'{k}={v}' for k, v in args.dimensions.items())})"
    if not series:
        return ToolResult(f"{label}: no datapoints since {start:%H:%M} UTC.", {"points": []})
    return _summarise(label, series, ctx.incident.opened_at)


def _value(point: dict[str, object], stat: str) -> float:
    if stat.startswith("p"):
        extended = point.get("ExtendedStatistics") or {}
        return float(extended.get(stat, 0.0)) if isinstance(extended, dict) else 0.0
    return float(point.get(stat, 0.0))  # type: ignore[arg-type]


def _summarise(label: str, series: list[tuple[datetime, float]], opened: datetime) -> ToolResult:
    split = opened - timedelta(minutes=5)
    before = [v for t, v in series if t < split]
    during = [v for t, v in series if t >= split]
    base = fmean(before) if before else 0.0
    now = fmean(during) if during else 0.0
    peak_at, peak = max(series, key=lambda item: item[1])
    rise = next(
        (t for t, v in series if t >= split and v > max(base * 1.5, base + 1)),
        None,
    )
    if base:
        change = f"{(now - base) / base * 100:+.0f}%"
    elif now:
        change = "from zero"
    else:
        change = "no change"
    text = (
        f"{label}: baseline {base:.2f} before {split:%H:%M} UTC, {now:.2f} since "
        f"({change}); peak {peak:.2f} at {peak_at:%H:%M} UTC"
    )
    if rise is not None:
        text += f"; first rise at {rise:%H:%M} UTC"
    data = {
        "baseline": round(base, 3),
        "recent": round(now, 3),
        "peak": round(peak, 3),
        "peak_at": peak_at.isoformat(),
        "first_rise_at": rise.isoformat() if rise else None,
        "points": [[f"{t:%H:%M}", round(v, 3)] for t, v in series[-40:]],
    }
    return ToolResult(text + ".", data)


TOOL = Tool(
    name="get_metric_series",
    description=(
        "A CloudWatch metric around the incident (namespaces AWS/Lambda, AWS/DynamoDB, "
        "AWS/States, AWS/SQS, AWS/Events), summarised as baseline vs now."
    ),
    args=Args,
    run=run,
)
