"""Forecasts (F12): the trend arithmetic, each forecast's status, and when Discord hears."""

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from nightwatch.predict import handler as forecast
from nightwatch.predict.trend import crossing_day, fit, month

TODAY = date(2026, 10, 20)
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


def test_a_straight_line_through_the_points() -> None:
    line = fit([1.0, 3.0, 5.0, 7.0])
    assert (round(line.slope, 6), round(line.intercept, 6)) == (2.0, 1.0)
    assert fit([4.0, 6.0]).slope == 0.0  # too few points: the average, flat
    assert fit([]).at(5) == 0.0


def test_month_projection_counts_today_but_trends_on_complete_days() -> None:
    daily = {date(2026, 10, day): 1000.0 for day in range(1, 21)}  # 1 to 20 October
    result = month(daily, TODAY, limit=40_000)
    assert result.so_far == 20 * 1000  # 1st to 20th of October
    assert result.projected == pytest.approx(31_000)  # 11 more days at 1,000
    assert result.crossing is None
    tight = month(daily, TODAY, limit=25_000)
    assert tight.crossing == date(2026, 10, 25)
    over = month(daily, TODAY, limit=10_000)
    assert over.crossing == TODAY


def test_crossing_day_for_a_rising_series() -> None:
    assert crossing_day([1.0, 2.0, 3.0, 4.0], limit=6.0, today=TODAY) == TODAY + timedelta(days=2)
    assert crossing_day([5.0, 4.0, 3.0], limit=6.0, today=TODAY) is None
    assert crossing_day([0.0, 0.0, 7.0], limit=6.0, today=TODAY) == TODAY  # already there


class Metrics:
    def __init__(self, daily: dict[tuple[str, str], dict[date, float]], peaks: list[float]) -> None:
        self._daily = daily
        self._peaks = peaks

    def daily(self, namespace: str, metric: str, dimensions: Any = None) -> dict[date, float]:
        return self._daily.get((namespace, metric), {})

    def hourly_peaks(self, *args: Any) -> list[float]:
        return self._peaks

    def daily_peaks(self, *args: Any) -> list[float]:
        return self._peaks


def test_statuses_for_free_tiers_and_error_budgets() -> None:
    steady = {TODAY - timedelta(days=b): 50_000.0 for b in range(14, -1, -1)}
    assert (
        forecast.free_tier("Lambda requests", "requests", 1_000_000, steady, NOW)["status"]
        == "warn"
    )
    small = dict.fromkeys(steady, 10.0)
    assert (
        forecast.free_tier("Lambda requests", "requests", 1_000_000, small, NOW)["status"] == "ok"
    )

    metrics = Metrics(
        {("AWS/Lambda", "Invocations"): {TODAY: 10_000.0}, ("AWS/Lambda", "Errors"): {TODAY: 60.0}},
        [],
    )
    budget = forecast.error_budget("shoplite", "shoplite-api", 99.5, metrics)  # type: ignore[arg-type]
    assert budget["status"] == "over"  # 60 failures of 50 allowed
    assert "120% of the error budget spent" in budget["sentence"]


def test_headroom_for_read_capacity_and_concurrency() -> None:
    class Dynamo:
        def __init__(self, reads: int) -> None:
            self.reads = reads

        def describe_table(self, **kwargs: Any) -> dict[str, Any]:
            return {"Table": {"ProvisionedThroughput": {"ReadCapacityUnits": self.reads}}}

    rising = Metrics({}, [1.0, 2.0, 3.0, 4.0])
    reads = forecast.read_headroom("shoplite", rising, Dynamo(5), NOW)  # type: ignore[arg-type]
    assert reads is not None
    assert (reads["status"], reads["crossing"]) == ("warn", "2026-10-21")
    # An on-demand table reports 0 provisioned reads: nothing to run out of, so no forecast.
    assert forecast.read_headroom("orders", rising, Dynamo(0), NOW) is None  # type: ignore[arg-type]

    class Lambda:
        def get_account_settings(self) -> dict[str, Any]:
            return {"AccountLimit": {"ConcurrentExecutions": 400}}

    busier = Metrics({}, [300.0, 330.0, 360.0])  # 30 more a day: past 400 in two days
    running = forecast.concurrency(busier, Lambda(), NOW)  # type: ignore[arg-type]
    assert (running["status"], running["crossing"]) == ("warn", "2026-10-22")
    assert running["sentence"].startswith("At most 360 functions ran at once")


def test_discord_hears_only_when_a_forecast_gets_worse() -> None:
    now = [
        {"name": "a", "status": "over"},
        {"name": "b", "status": "warn"},
        {"name": "c", "status": "ok"},
    ]
    assert [f["name"] for f in forecast.news(None, now)] == ["a", "b"]
    before = {"forecasts": [{"name": "a", "status": "over"}, {"name": "b", "status": "watch"}]}
    assert [f["name"] for f in forecast.news(before, now)] == ["b"]  # "a" was already over
    message = forecast.message([{**now[0], "sentence": "Too much."}])
    assert message["content"] == "🔮 **NightWatch forecast**\n🔴 **a**: Too much."
