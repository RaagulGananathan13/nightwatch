"""Lambda entry point: daily forecasts and warnings (spec F12).

Each forecast compares a trend with a limit:
- **Lambda requests this month** against the 1,000,000-request free tier.
- **Lambda compute this month** against the 400,000 GB-second free tier.
- **CloudWatch log ingestion this month** against the 5 GB free tier.
- **Each service's error budget**: failed requests over the last 14 days against its availability
  target in services.yaml.
- **Lambda concurrency**: the most functions running at once each day, against the account's
  limit.
- **Each watched table's read headroom**: the busiest hour's reads against its provisioned
  read capacity. On-demand tables are skipped: they have none to run out of.

The report is stored every day for the dashboard. Discord hears only about a forecast whose
status got worse since the last report, so a known problem doesn't post every morning.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from functools import cache
from typing import Any

import boto3

from nightwatch.agent.services import load_services
from nightwatch.chat.discord import DiscordError, webhook_from_ssm
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import to_iso, utcnow
from nightwatch.predict.trend import crossing_day, month

FREE_REQUESTS = 1_000_000
FREE_GB_SECONDS = 400_000
FREE_LOG_BYTES = 5 * 1024**3
HISTORY_DAYS = 14
RANK = {"ok": 0, "watch": 1, "warn": 2, "over": 3}
_secrets = SecretCache()


@cache
def _clients() -> tuple[Any, Any, Any]:
    return boto3.client("cloudwatch"), boto3.client("lambda"), boto3.client("dynamodb")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    now = utcnow()
    cloudwatch, lambda_, dynamodb = _clients()
    metrics = Metrics(cloudwatch, now)
    forecasts = [
        free_tier(
            "Lambda requests",
            "requests",
            FREE_REQUESTS,
            metrics.daily("AWS/Lambda", "Invocations"),
            now,
        ),
        free_tier(
            "Lambda compute", "GB-seconds", FREE_GB_SECONDS, gb_seconds(metrics, lambda_), now
        ),
        free_tier(
            "CloudWatch log ingestion",
            "bytes",
            FREE_LOG_BYTES,
            metrics.daily("AWS/Logs", "IncomingBytes"),
            now,
        ),
        concurrency(metrics, lambda_, now),
    ]
    for service in load_services().values():
        if service.slo and service.function:
            forecasts.append(error_budget(service.name, service.function, service.slo, metrics))
        for table in service.dynamodb_tables:
            reads = read_headroom(table, metrics, dynamodb, now)
            if reads:
                forecasts.append(reads)

    store = IncidentStore(settings.table_name, settings.tenant_id)
    previous = store.latest_report("forecast")
    store.save_report("forecast", {"at": to_iso(now), "forecasts": forecasts}, now, keep_days=90)
    worse = news(previous, forecasts)
    if worse:
        _post(settings, worse)
    return {"forecasts": len(forecasts), "warnings": [f["name"] for f in worse]}


class Metrics:
    """Daily values from CloudWatch for the last 14 days (free GetMetricStatistics calls)."""

    def __init__(self, cloudwatch: Any, now: datetime) -> None:
        self.cloudwatch = cloudwatch
        self.today = now.date()
        self.start = datetime.combine(
            self.today - timedelta(days=HISTORY_DAYS), datetime.min.time(), UTC
        )
        self.end = now

    def daily(
        self,
        namespace: str,
        metric: str,
        dimensions: dict[str, str] | None = None,
        statistic: str = "Sum",
    ) -> dict[date, float]:
        reply = self.cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in (dimensions or {}).items()],
            StartTime=self.start,
            EndTime=self.end,
            Period=86400,
            Statistics=[statistic],
        )
        return {p["Timestamp"].date(): float(p[statistic]) for p in reply.get("Datapoints", [])}

    def daily_peaks(self, namespace: str, metric: str) -> list[float]:
        """Per day, the highest value seen (e.g. functions running at once)."""
        return self._days(self.daily(namespace, metric, statistic="Maximum"))

    def hourly_peaks(self, namespace: str, metric: str, dimensions: dict[str, str]) -> list[float]:
        """Per day, the busiest hour's average per second (for capacity, which is per second)."""
        reply = self.cloudwatch.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
            StartTime=self.start,
            EndTime=self.end,
            Period=3600,
            Statistics=["Sum"],
        )
        peaks: dict[date, float] = {}
        for point in reply.get("Datapoints", []):
            day = point["Timestamp"].date()
            peaks[day] = max(peaks.get(day, 0.0), float(point["Sum"]) / 3600)
        return self._days(peaks)

    def _days(self, values: dict[date, float]) -> list[float]:
        """One value per day, oldest first and today last; 0 for a day without data."""
        return [
            values.get(self.today - timedelta(days=back), 0.0)
            for back in range(HISTORY_DAYS, -1, -1)
        ]


def gb_seconds(metrics: Metrics, lambda_: Any) -> dict[date, float]:
    """Daily GB-seconds across the region's functions: duration times configured memory."""
    total: dict[date, float] = {}
    for page in lambda_.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            gigabytes = function.get("MemorySize", 128) / 1024
            durations = metrics.daily(
                "AWS/Lambda", "Duration", {"FunctionName": function["FunctionName"]}
            )
            for day, milliseconds in durations.items():
                total[day] = total.get(day, 0.0) + milliseconds / 1000 * gigabytes
    return total


def free_tier(
    name: str, unit: str, limit: float, daily: dict[date, float], now: datetime
) -> dict[str, Any]:
    result = month(daily, now.date(), limit)
    share = result.projected / limit
    status = (
        "over"
        if result.so_far >= limit
        else "warn" if result.crossing else "watch" if share >= 0.8 else "ok"
    )
    sentence = (
        f"{_amount(result.so_far, unit)} used this month of the free {_amount(limit, unit)}; at the "
        f"current trend the month ends at {_amount(result.projected, unit)} ({share:.0%})."
    )
    if result.crossing and status != "over":
        sentence += f" It would pass the free tier on {_day(result.crossing)}."
    return {
        "name": name,
        "status": status,
        "so_far": round(result.so_far, 1),
        "projected": round(result.projected, 1),
        "limit": limit,
        "unit": unit,
        "crossing": result.crossing.isoformat() if result.crossing else None,
        "sentence": sentence,
    }


def error_budget(service: str, function: str, slo: float, metrics: Metrics) -> dict[str, Any]:
    """Failed requests over the window against what the availability target allows."""
    requests = sum(metrics.daily("AWS/Lambda", "Invocations", {"FunctionName": function}).values())
    errors = sum(metrics.daily("AWS/Lambda", "Errors", {"FunctionName": function}).values())
    allowed = requests * (1 - slo / 100)
    spent = errors / allowed if allowed else 0.0
    status = (
        "over" if spent >= 1 else "warn" if spent >= 0.75 else "watch" if spent >= 0.5 else "ok"
    )
    return {
        "name": f"{service} error budget",
        "status": status,
        "so_far": errors,
        "projected": round(spent * 100, 1),
        "limit": round(allowed, 1),
        "unit": "failed requests",
        "crossing": None,
        "sentence": (
            f"{errors:,.0f} failed requests of the {allowed:,.0f} that a {slo}% target allows over "
            f"the last {HISTORY_DAYS} days ({spent:.0%} of the error budget spent)."
        ),
    }


def read_headroom(
    table: str, metrics: Metrics, dynamodb: Any, now: datetime
) -> dict[str, Any] | None:
    """The busiest hour's reads against provisioned capacity. None for an on-demand table,
    which has no provisioned capacity to run out of (it reports 0)."""
    provisioned = float(
        dynamodb.describe_table(TableName=table)["Table"]["ProvisionedThroughput"][
            "ReadCapacityUnits"
        ]
    )
    if not provisioned:
        return None
    peaks = metrics.hourly_peaks("AWS/DynamoDB", "ConsumedReadCapacityUnits", {"TableName": table})
    return headroom(
        f"{table} read capacity",
        peaks,
        provisioned,
        "reads per second",
        f"The busiest hour averaged {max(peaks, default=0.0):.1f} reads a second against "
        f"{provisioned:g} provisioned.",
        now,
    )


def concurrency(metrics: Metrics, lambda_: Any, now: datetime) -> dict[str, Any]:
    """The most functions running at once each day, against the account's limit."""
    limit = float(lambda_.get_account_settings()["AccountLimit"]["ConcurrentExecutions"])
    peaks = metrics.daily_peaks("AWS/Lambda", "ConcurrentExecutions")
    return headroom(
        "Lambda concurrency",
        peaks,
        limit,
        "functions at once",
        f"At most {max(peaks, default=0.0):.0f} functions ran at once, of the account's limit "
        f"of {limit:g}.",
        now,
    )


def headroom(
    name: str, peaks: list[float], limit: float, unit: str, sentence: str, now: datetime
) -> dict[str, Any]:
    """A daily peak against a fixed limit, and whether its trend reaches it within 30 days."""
    peak = max(peaks, default=0.0)
    when = crossing_day(peaks, limit, now.date())
    status = (
        "over" if peak >= limit else "warn" if when else "watch" if peak >= 0.8 * limit else "ok"
    )
    if when and status == "warn":
        sentence += f" At the current trend it reaches the limit around {_day(when)}."
    return {
        "name": name,
        "status": status,
        "so_far": round(peak, 2),
        "projected": round(peak, 2),
        "limit": limit,
        "unit": unit,
        "crossing": when.isoformat() if when else None,
        "sentence": sentence,
    }


def news(previous: dict[str, Any] | None, forecasts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Forecasts at warn or over whose status got worse since the last report."""
    before = {f["name"]: f["status"] for f in (previous or {}).get("forecasts", [])}
    return [
        f
        for f in forecasts
        if RANK[f["status"]] >= RANK["warn"]
        and RANK[f["status"]] > RANK.get(before.get(f["name"], "ok"), 0)
    ]


def message(worse: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [
        f"{'🔴' if f['status'] == 'over' else '🟠'} **{f['name']}**: {f['sentence']}" for f in worse
    ]
    return {"content": "🔮 **NightWatch forecast**\n" + "\n".join(lines)[:1900]}


def _post(settings: Settings, worse: list[dict[str, Any]]) -> None:
    hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
    if hook is None:
        return
    try:
        hook.send(message(worse))
    except DiscordError as exc:
        emit("WARNING", "could not post the forecast", error=str(exc)[:200])


def _amount(value: float, unit: str) -> str:
    if unit == "bytes":
        return f"{value / 1024**3:.2f} GB" if value >= 1024**3 else f"{value / 1024**2:.0f} MB"
    return f"{value:,.0f} {unit}"


def _day(day: date) -> str:
    return f"{day.day} {day:%B}"  # "3 November"; the %-d directive is not portable
