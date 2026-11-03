"""The emergency stop: when spend or usage runs away, NightWatch switches itself and ShopLite
off, so nothing more is charged until a person has looked. The cost guardian's last line.

What trips it (handler.py):
- AWS's budget reports real spend at or past the stop line this month ($0.50, about 50 times
  NightWatch's normal month);
- Lambda calls run away, such as a flood on the public chat endpoint: the
  nightwatch-runaway-invocations alarm calls the cost Lambda directly, within minutes;
- the morning check finds a free tier already used up, or capacity past the free tier.

What it does, in this order, so the most important part happens even if a step fails:
1. tells Discord and Slack why;
2. turns the kill switch on, so no fix runs after a resume until someone turns it off;
3. shrinks DynamoDB tables provisioned past the free 25 units to 1 unit each;
4. gives every nightwatch-* and shoplite-* Lambda a reserved concurrency of 0. AWS then refuses
   every invocation, from any caller, and doesn't bill refused ones. This Lambda goes last.

`make resume` undoes 2 and 4; `make apply` sets table capacity from Terraform again.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.cost.signals import FREE_UNITS, capacity

OWNED = ("nightwatch-", "shoplite-")  # the functions NightWatch may switch off
ONE_UNIT = {"ReadCapacityUnits": 1, "WriteCapacityUnits": 1}


@dataclass(frozen=True)
class Stopped:
    functions: list[str]
    tables: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def notice(reason: str) -> dict[str, Any]:
    """What Discord and Slack are told, before the lights go out."""
    return {
        "content": (
            f"🛑 **NightWatch emergency stop.** {reason}\n"
            "Every NightWatch and ShopLite function is switched off and fixes are disabled, so "
            "nothing more is charged. Check the bill, then run `make resume`."
        )
    }


def stop(
    reason: str,
    lambda_: Any,
    ssm: Any,
    dynamodb: Any,
    kill_switch_param: str,
    tables: list[str],
    tell: Callable[[dict[str, Any]], None],
    me: str = "",
) -> Stopped:
    """Switch everything off. Each step is tried even if an earlier one failed."""
    problems: list[str] = []
    tell(notice(reason))
    try:
        ssm.put_parameter(Name=kill_switch_param, Value="true", Type="String", Overwrite=True)
    except (BotoCoreError, ClientError) as exc:
        problems.append(f"kill switch: {type(exc).__name__}")

    shrunk: list[str] = []
    try:
        read, write = capacity(dynamodb, tables)
        if read > FREE_UNITS or write > FREE_UNITS:
            shrunk = [name for name in dict.fromkeys(tables) if _shrink(dynamodb, name, problems)]
    except (BotoCoreError, ClientError) as exc:
        problems.append(f"capacity: {type(exc).__name__}")

    names = sorted(
        (
            function["FunctionName"]
            for page in lambda_.get_paginator("list_functions").paginate()
            for function in page.get("Functions", [])
            if function["FunctionName"].startswith(OWNED)
        ),
        key=lambda name: (name == me, name),  # this function last
    )
    for name in names:
        try:
            lambda_.put_function_concurrency(FunctionName=name, ReservedConcurrentExecutions=0)
        except (BotoCoreError, ClientError) as exc:
            problems.append(f"{name}: {type(exc).__name__}")
    return Stopped(names, shrunk, problems)


def _shrink(dynamodb: Any, name: str, problems: list[str]) -> bool:
    """Set a provisioned table and its indexes to 1 unit each. False if nothing changed."""
    try:
        table = dynamodb.describe_table(TableName=name)["Table"]
        update: dict[str, Any] = {"TableName": name}
        if _above_one(table):
            update["ProvisionedThroughput"] = ONE_UNIT
        indexes = [
            {"Update": {"IndexName": index["IndexName"], "ProvisionedThroughput": ONE_UNIT}}
            for index in table.get("GlobalSecondaryIndexes", [])
            if _above_one(index)
        ]
        if indexes:
            update["GlobalSecondaryIndexUpdates"] = indexes
        if len(update) == 1:
            return False
        dynamodb.update_table(**update)
    except (BotoCoreError, ClientError) as exc:
        problems.append(f"{name}: {type(exc).__name__}")
        return False
    return True


def _above_one(part: dict[str, Any]) -> bool:
    throughput = part.get("ProvisionedThroughput") or {}
    return (
        int(throughput.get("ReadCapacityUnits", 0)) > 1
        or int(throughput.get("WriteCapacityUnits", 0)) > 1
    )
