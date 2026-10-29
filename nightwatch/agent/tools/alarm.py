"""get_alarm_context: the alarm's definition and its recent state history."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolError, ToolResult


class Args(BaseModel):
    alarm_name: str = Field(max_length=255)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    if args.alarm_name not in ctx.incident.alarms:
        raise ToolError(f"alarm '{args.alarm_name}' is not part of this incident")
    found = ctx.aws.cloudwatch.describe_alarms(AlarmNames=[args.alarm_name])["MetricAlarms"]
    if not found:
        raise ToolError(f"alarm '{args.alarm_name}' no longer exists")
    alarm = found[0]
    history = ctx.aws.cloudwatch.describe_alarm_history(
        AlarmName=args.alarm_name, HistoryItemType="StateUpdate", MaxRecords=5
    )["AlarmHistoryItems"]

    watches = _what_it_watches(alarm)
    lines = [
        f"Alarm {args.alarm_name}: {alarm.get('AlarmDescription', '')}".strip(),
        f"Watches {watches}; fires when {alarm.get('ComparisonOperator')} "
        f"{alarm.get('Threshold')} for {alarm.get('DatapointsToAlarm', 1)} of "
        f"{alarm.get('EvaluationPeriods')} periods.",
        f"Now {alarm.get('StateValue')}: {str(alarm.get('StateReason', ''))[:300]}",
        "Recent changes: "
        + "; ".join(
            f"{item['Timestamp']:%H:%M:%S} {item['HistorySummary']}" for item in history[:5]
        ),
    ]
    data = {
        "alarm_name": args.alarm_name,
        "state": alarm.get("StateValue"),
        "threshold": alarm.get("Threshold"),
        "watches": watches,
        "history": [
            {"at": item["Timestamp"].isoformat(), "summary": item["HistorySummary"]}
            for item in history[:5]
        ],
    }
    return ToolResult("\n".join(lines), data)


def _what_it_watches(alarm: dict[str, Any]) -> str:
    if alarm.get("MetricName"):
        dims = ",".join(f"{d['Name']}={d['Value']}" for d in alarm.get("Dimensions", []))
        stat = alarm.get("Statistic") or alarm.get("ExtendedStatistic")
        return f"{alarm.get('Namespace')}/{alarm['MetricName']} ({stat}, {dims})"
    parts = []
    for query in alarm.get("Metrics", []):
        if query.get("Expression"):
            parts.append(f"{query['Id']} = {query['Expression']}")
        elif query.get("MetricStat"):
            metric = query["MetricStat"]["Metric"]
            parts.append(f"{query['Id']} = {metric.get('Namespace')}/{metric.get('MetricName')}")
    return "; ".join(parts) or "a metric"


TOOL = Tool(
    name="get_alarm_context",
    description="An alarm's definition, threshold and recent state changes.",
    args=Args,
    run=run,
)
