"""Daily usage of a service's resources, from free CloudWatch data (spec F17).

What costs money on this stack is usage past a free tier: Lambda requests and compute, and log
ingestion. A spike shows in this usage a day before it shows on a bill, as a resource whose
usage yesterday was several times its usual day. Only complete UTC days count; today is
still running.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from nightwatch.agent.services import ServiceInfo

JUMP = 3.0  # a day at least this many times its usual day is a spike
USUAL_DAYS = 7  # the usual day: the median of up to this many days before it
FLOORS = {"requests": 1_000.0, "GB-seconds": 100.0, "log bytes": 10 * 1024**2}  # below: noise


@dataclass(frozen=True)
class Usage:
    resource: str
    measure: str  # "requests", "GB-seconds" or "log bytes"
    days: tuple[date, ...]  # complete UTC days, oldest first, ending yesterday
    values: tuple[float, ...]

    @property
    def yesterday(self) -> float:
        return self.values[-1] if self.values else 0.0

    @property
    def usual(self) -> float:
        return _usual(self.values, len(self.values) - 1)

    @property
    def ratio(self) -> float:
        """Yesterday against the usual day (or the noise floor, whichever is larger)."""
        return self.yesterday / max(self.usual, FLOORS[self.measure])

    @property
    def jumped(self) -> bool:
        return self.ratio >= JUMP

    def jump_day(self) -> date | None:
        """The day with the biggest jump over its usual day, if that jump was a spike."""
        best, when = JUMP, None
        for index in range(1, len(self.values)):
            ratio = self.values[index] / max(_usual(self.values, index), FLOORS[self.measure])
            if ratio >= best:
                best, when = ratio, self.days[index]
        return when


def service_usage(
    cloudwatch: Any, lambda_: Any, service: ServiceInfo, now: datetime, days: int = 15
) -> list[Usage]:
    """Each function's requests and GB-seconds and each log group's bytes, per complete day."""
    end = datetime.combine(now.date(), datetime.min.time(), UTC)
    start = end - timedelta(days=days)
    calendar = tuple(start.date() + timedelta(days=offset) for offset in range(days))

    def daily(namespace: str, metric: str, dimensions: dict[str, str]) -> tuple[float, ...]:
        reply = cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
            StartTime=start,
            EndTime=end,
            Period=86400,
            Statistics=["Sum"],
        )
        sums = {p["Timestamp"].date(): float(p["Sum"]) for p in reply.get("Datapoints", [])}
        return tuple(sums.get(day, 0.0) for day in calendar)

    found: list[Usage] = []
    for function in service.lambda_functions:
        named = {"FunctionName": function}
        found.append(
            Usage(function, "requests", calendar, daily("AWS/Lambda", "Invocations", named))
        )
        memory = lambda_.get_function_configuration(FunctionName=function).get("MemorySize", 128)
        milliseconds = daily("AWS/Lambda", "Duration", named)
        seconds = tuple(ms / 1000 * memory / 1024 for ms in milliseconds)
        found.append(Usage(function, "GB-seconds", calendar, seconds))
    for group in service.log_groups:
        named = {"LogGroupName": group}
        found.append(Usage(group, "log bytes", calendar, daily("AWS/Logs", "IncomingBytes", named)))
    return found


def amount(value: float, measure: str) -> str:
    if measure == "log bytes":
        return f"{value / 1024**2:,.1f} MB"
    return f"{value:,.0f} {measure}"


def _usual(values: tuple[float, ...], index: int) -> float:
    before = values[max(0, index - USUAL_DAYS) : index]
    return float(statistics.median(before)) if before else 0.0
