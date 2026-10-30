"""The arithmetic behind the forecasts: a least-squares line, and when it crosses a limit.

Plain statistics on purpose (spec F12): no paid anomaly detection, and every forecast can be
checked by hand. The daily values come from CloudWatch, one point per UTC day. Today's point
is incomplete, so it counts toward what has been used so far, never toward the trend.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta

MIN_POINTS = 3  # fewer complete days than this: use their average instead of a line


@dataclass(frozen=True)
class Line:
    slope: float  # change per day
    intercept: float  # value on day 0

    def at(self, day: float) -> float:
        return self.slope * day + self.intercept


def fit(values: list[float]) -> Line:
    """Least squares through (0, v0), (1, v1), ... A flat line at the mean when too short."""
    n = len(values)
    if n == 0:
        return Line(0.0, 0.0)
    mean_y = sum(values) / n
    if n < MIN_POINTS:
        return Line(0.0, mean_y)
    mean_x = (n - 1) / 2
    spread = sum((x - mean_x) ** 2 for x in range(n))
    slope = sum((x - mean_x) * (y - mean_y) for x, y in enumerate(values)) / spread
    return Line(slope, mean_y - slope * mean_x)


@dataclass(frozen=True)
class MonthForecast:
    so_far: float  # used this month, today included
    projected: float  # by the end of the month at the current trend
    crossing: date | None  # the day the limit is reached, if it will be this month


def month(daily: dict[date, float], today: date, limit: float, history: int = 14) -> MonthForecast:
    """For allowances that reset monthly (free tiers): this month so far, and where it ends."""
    so_far = sum(
        value for day, value in daily.items() if (day.year, day.month) == (today.year, today.month)
    )
    complete = [daily.get(today - timedelta(days=back), 0.0) for back in range(history, 0, -1)]
    series = _since_first_use(complete)
    line = fit(series)
    last_day = date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])
    total, crossing = so_far, (today if so_far >= limit else None)
    # The series ends yesterday; today is the next step, so tomorrow is the one after.
    future = len(series) + 1
    day = today
    while day < last_day:
        day += timedelta(days=1)
        total += max(0.0, line.at(future))
        future += 1
        if crossing is None and total >= limit:
            crossing = day
    return MonthForecast(so_far, total, crossing)


def crossing_day(values: list[float], limit: float, today: date, horizon: int = 30) -> date | None:
    """When a daily series (e.g. peak reads) reaches ``limit`` within ``horizon`` days."""
    series = _since_first_use(values)
    if series and series[-1] >= limit:
        return today
    line = fit(series)
    if line.slope <= 0:
        return None
    for ahead in range(1, horizon + 1):
        if line.at(len(series) - 1 + ahead) >= limit:
            return today + timedelta(days=ahead)
    return None


def _since_first_use(values: list[float]) -> list[float]:
    """Drop the leading days before anything ran, so a new service's empty past doesn't
    flatten its trend."""
    for index, value in enumerate(values):
        if value:
            return values[index:]
    return []
