"""Daily usage of a service's resources, from free CloudWatch data (spec F17).

What costs money on this stack is usage past a free tier: Lambda requests and compute, and log
ingestion. A spike shows in this usage a day before it shows on a bill, as a resource whose
usage yesterday was several times its usual day. Only complete UTC days count; today is
still running.
"""

from __future__ import annotations

import calendar
import statistics
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from nightwatch.agent.services import ServiceInfo
from nightwatch.predict.trend import since_first_use

JUMP = 3.0  # a day at least this many times its usual day is a spike
USUAL_DAYS = 7  # the usual day: the median of up to this many days before it
MIN_HISTORY = 3  # days of use before a day can be called unusual: an empty past says nothing
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
    def usual(self) -> float | None:
        """The usual day before yesterday; None while there is too little history."""
        return _usual(self.values, len(self.values) - 1)

    @property
    def ratio(self) -> float:
        """Yesterday against the usual day (or the noise floor, whichever is larger)."""
        usual = self.usual
        return 0.0 if usual is None else self.yesterday / max(usual, FLOORS[self.measure])

    @property
    def jumped(self) -> bool:
        return self.ratio >= JUMP

    def jump_day(self) -> date | None:
        """The day with the biggest jump over its usual day, if that jump was a spike."""
        best, when = JUMP, None
        for index in range(1, len(self.values)):
            usual = _usual(self.values, index)
            if usual is None:
                continue
            ratio = self.values[index] / max(usual, FLOORS[self.measure])
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


def month_pace(daily: dict[date, float], today: date) -> tuple[float, float, float]:
    """This month so far, the usual day lately, and where the month ends at that pace.

    The usual day is the median of the last week's complete days since first use, so one busy
    day can't move it the way it tilts a straight line.
    """
    so_far = sum(
        v for day, v in daily.items() if (day.year, day.month) == (today.year, today.month)
    )
    week = [daily.get(today - timedelta(days=back), 0.0) for back in range(USUAL_DAYS, 0, -1)]
    used = since_first_use(week)
    usual = float(statistics.median(used)) if used else 0.0
    days_left = calendar.monthrange(today.year, today.month)[1] - today.day
    return so_far, usual, so_far + usual * days_left


def _usual(values: tuple[float, ...], index: int) -> float | None:
    """The median of up to a week before ``index``, counting only days since first use."""
    before = since_first_use(list(values[:index]))[-USUAL_DAYS:]
    return float(statistics.median(before)) if len(before) >= MIN_HISTORY else None
