"""Undo NightWatch's emergency stop: ``python -m scripts.resume`` (``make resume``).

It removes the zero concurrency from every nightwatch-* and shoplite-* function and turns the
kill switch off. Check the bill first: the stop happened for a reason. If the stop shrank any
DynamoDB table, `make apply` sets its capacity from Terraform again.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from typing import Any

import boto3

from nightwatch.cost.stop import OWNED

KILL_SWITCH = "/nightwatch/kill_switch"


def resume(lambda_: Any, ssm: Any, say: Callable[[str], None] = print) -> list[str]:
    """Switch every stopped NightWatch and ShopLite function back on, and fixes too."""
    restored: list[str] = []
    for page in lambda_.get_paginator("list_functions").paginate():
        for function in page.get("Functions", []):
            name = function["FunctionName"]
            if not name.startswith(OWNED):
                continue
            reserved = lambda_.get_function_concurrency(FunctionName=name)
            if reserved.get("ReservedConcurrentExecutions") == 0:
                lambda_.delete_function_concurrency(FunctionName=name)
                restored.append(name)
    ssm.put_parameter(Name=KILL_SWITCH, Value="false", Type="String", Overwrite=True)
    say(f"Switched {len(restored)} function(s) back on, and fixes are allowed again.")
    say("If the stop shrank a table, run `make plan` and `make apply` to restore its capacity.")
    return restored


def main() -> None:
    os.environ.setdefault("AWS_PROFILE", "nightwatch")
    session = boto3.Session(region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-2"))
    restored = resume(session.client("lambda"), session.client("ssm"))
    sys.stdout.write("\n".join(restored) + ("\n" if restored else ""))


if __name__ == "__main__":
    main()
