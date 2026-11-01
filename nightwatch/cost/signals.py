"""What the cost guardian raises (spec F17): one signal per thing that costs, or will cost, money.

- A resource whose usage jumped yesterday (usage.py).
- A free tier the month is heading past at the usual day's pace. Not the forecast's straight
  line (F12): an incident needs a signal that one busy day can't tilt.
- Provisioned DynamoDB capacity beyond the free 25 read and 25 write units.
- An AWS Budget alert: real spend, measured by AWS itself.

Each signal becomes an alert on a made-up alarm, ``cost-guard:<what>``, so it travels the same
path as a CloudWatch alarm: incident, Discord, investigation, postmortem.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nightwatch.agent.services import ServiceInfo, load_services
from nightwatch.common.models import Severity
from nightwatch.cost import PREFIX
from nightwatch.cost.usage import Usage, amount, month_pace, service_usage
from nightwatch.predict.handler import (
    FREE_GB_SECONDS,
    FREE_LOG_BYTES,
    FREE_REQUESTS,
    Metrics,
    gb_seconds,
)

FREE_UNITS = 25  # provisioned DynamoDB read units, and write units, in the free tier

_FIELD = re.compile(
    r"^\s*(Budget Name|Budgeted Amount|Alert Type|Alert Threshold|ACTUAL Amount|"
    r"FORECASTED Amount)\s*:\s*(.+?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_SENTENCE = re.compile(r"The (ACTUAL|FORECASTED) Cost associated with this budget is (\$[\d,.]+)")


@dataclass(frozen=True)
class Signal:
    alarm: str  # cost-guard:<what>, the same every day the condition lasts
    service: str  # the catalogue service it is investigated under
    severity: Severity
    summary: str


def daily(
    cloudwatch: Any, lambda_: Any, dynamodb: Any, now: datetime, own_table: str
) -> list[Signal]:
    """The morning check: usage jumps, free tiers and provisioned capacity."""
    services = list(load_services().values())
    if not services:
        return []
    usage = {s.name: service_usage(cloudwatch, lambda_, s, now) for s in services}
    signals = [
        Signal(
            f"{PREFIX}{u.resource}:{_slug(u.measure)}",
            name,
            "medium",
            f"{u.resource} used {amount(u.yesterday, u.measure)} yesterday, {u.ratio:.1f} times "
            f"a usual day ({amount(u.usual or 0.0, u.measure)}).",
        )
        for name, found in usage.items()
        for u in found
        if u.jumped
    ]

    metrics = Metrics(cloudwatch, now)
    tiers = [
        ("Lambda requests", "requests", FREE_REQUESTS, metrics.daily("AWS/Lambda", "Invocations")),
        ("Lambda compute", "GB-seconds", FREE_GB_SECONDS, gb_seconds(metrics, lambda_)),
        (
            "CloudWatch log ingestion",
            "log bytes",
            FREE_LOG_BYTES,
            metrics.daily("AWS/Logs", "IncomingBytes"),
        ),
    ]
    for name, measure, limit, values in tiers:
        so_far, usual, projected = month_pace(values, now.date())
        if projected < limit:
            continue
        severity: Severity = "high" if so_far >= limit else "medium"
        signals.append(
            Signal(
                f"{PREFIX}free-tier:{_slug(name)}",
                owner(usage, measure, services),
                severity,
                f"{name}: {amount(so_far, measure)} used this month; at the usual day's pace "
                f"({amount(usual, measure)}) the month ends at {amount(projected, measure)}, "
                f"past the free {amount(limit, measure)}.",
            )
        )

    tables = [own_table, *[t for s in services for t in s.dynamodb_tables]]
    read, write = capacity(dynamodb, tables)
    if read > FREE_UNITS or write > FREE_UNITS:
        holder = next((s.name for s in services if s.dynamodb_tables), services[0].name)
        signals.append(
            Signal(
                f"{PREFIX}dynamodb-capacity",
                holder,
                "high",
                f"DynamoDB tables are provisioned with {read} read and {write} write units; the "
                f"free tier covers {FREE_UNITS} of each.",
            )
        )
    return signals


def budget(message: str, subject: str, service: str) -> Signal:
    """An AWS Budget notification (the same text AWS emails) as a signal."""
    fields = {key.lower(): value for key, value in _FIELD.findall(message)}
    sentence = _SENTENCE.search(message)
    kind = (fields.get("alert type") or (sentence.group(1) if sentence else "")).upper()
    if kind not in ("ACTUAL", "FORECASTED"):
        kind = "FORECASTED" if "forecast" in f"{subject} {message}".lower() else "ACTUAL"
    spent = fields.get(f"{kind.lower()} amount") or (sentence.group(2) if sentence else "?")
    name = fields.get("budget name", "the budget")
    if fields or sentence:
        summary = (
            f"AWS Budget {name}: {kind.lower()} spend is {spent}, past the alert at "
            f"{fields.get('alert threshold', '?')} (budget {fields.get('budgeted amount', '?')})."
        )
    else:
        summary = f"AWS Budget alert: {(subject or message)[:200]}"
    severity: Severity = "high" if kind == "ACTUAL" else "medium"
    return Signal(f"{PREFIX}budget:{kind.lower()}", service, severity, summary[:300])


def owner(usage: dict[str, list[Usage]], measure: str, services: list[ServiceInfo]) -> str:
    """The service that used the most of ``measure`` yesterday (the first, if none did)."""
    totals = {
        name: sum(u.yesterday for u in found if u.measure == measure)
        for name, found in usage.items()
    }
    busiest = max(totals, key=lambda name: totals[name], default="")
    return busiest if totals.get(busiest) else services[0].name


def capacity(dynamodb: Any, tables: list[str]) -> tuple[int, int]:
    """Provisioned read and write units across the tables and their indexes."""
    read = write = 0
    for name in dict.fromkeys(tables):
        table = dynamodb.describe_table(TableName=name)["Table"]
        for part in [table, *table.get("GlobalSecondaryIndexes", [])]:
            throughput = part.get("ProvisionedThroughput") or {}
            read += int(throughput.get("ReadCapacityUnits", 0))
            write += int(throughput.get("WriteCapacityUnits", 0))
    return read, write


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
