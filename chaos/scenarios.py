"""Every chaos scenario, each with the ground truth NightWatch is scored against (spec F16).

    python -m chaos --list
    python -m chaos lambda-timeout                      # run one, then PASS or FAIL
    python -m chaos broken-env-var --let-nightwatch-fix # keep it broken until you approve

A scenario's ``expect`` phrases decide whether a diagnosis names the cause. They name the
cause, not the symptom: for a timeout, "the hang flag" counts, "requests time out" alone
doesn't. "a+b" means both a and b must appear.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, Protocol

from chaos import bad_deploy, error_flag
from chaos.runner import Scenario

BROKEN_TABLE = "shoplite-catalogue"  # a table that doesn't exist


class ScenarioLab(bad_deploy.DeployLab, error_flag.FlagLab, Protocol):
    def deploy_settings(self, changes: dict[str, str], description: str) -> dict[str, Any]: ...
    def restore_settings(self, variables: dict[str, str]) -> None: ...
    def set_live_version(self, version: str) -> None: ...
    def read_capacity(self) -> int: ...
    def set_read_capacity(self, units: int) -> None: ...
    def load(self, seconds: float, parallel: int) -> list[int]: ...


def flag_fault(
    lab: ScenarioLab,
    *,
    slug: str,
    name: str,
    flags: dict[str, Any],
    ground_truth: str,
    expect: list[str],
    minutes: float = 4.0,
) -> Scenario:
    """A fault switched on through ShopLite's feature flags, which it re-reads every 10 s."""

    def inject(out: Callable[[str], None]) -> Callable[[], None]:
        lab.set_flags(flags)
        out(f"Set /shoplite/flags to {json.dumps(flags)}.")
        return lambda: lab.set_flags({})

    return Scenario(
        name=name,
        ground_truth=ground_truth,
        inject=inject,
        expect=expect,
        still_broken=lambda: bool(lab.get_flags()),
        slug=slug,
        fix="reset-feature-flags",
        minutes=minutes,
    )


def lambda_timeout(lab: ScenarioLab) -> Scenario:
    return flag_fault(
        lab,
        slug="lambda-timeout",
        name="Lambda timeout",
        flags={"hang": True},
        ground_truth=(
            "The hang flag in /shoplite/flags makes every request run past ShopLite's 5-second "
            "timeout, so Lambda stops it ('Task timed out')."
        ),
        expect=["hang", "flag+timeout", "flag+timed out"],
    )


def memory_exhaustion(lab: ScenarioLab) -> Scenario:
    return flag_fault(
        lab,
        slug="memory-exhaustion",
        name="memory exhaustion",
        flags={"memory_hog_mb": 400},
        ground_truth=(
            "The memory_hog_mb flag makes every request allocate 400 MB, more than the "
            "function's 256 MB, so the runtime runs out of memory and is killed."
        ),
        expect=["memory_hog", "flag+memory"],
    )


def dependency_latency(lab: ScenarioLab) -> Scenario:
    return flag_fault(
        lab,
        slug="dependency-latency",
        name="slow dependency",
        flags={"extra_latency_ms": 1500},
        ground_truth=(
            "The extra_latency_ms flag adds 1.5 s to every request, like a slow dependency, so "
            "p95 latency stays above the 1-second alarm."
        ),
        expect=["extra_latency", "flag+latency", "flag+slow", "flag+delay"],
        minutes=5.0,  # the latency alarm needs 2 bad minutes in a row
    )


def dynamodb_throttle(lab: ScenarioLab) -> Scenario:
    before: list[int] = []

    def inject(out: Callable[[str], None]) -> Callable[[], None]:
        before.append(lab.read_capacity())
        lab.set_read_capacity(1)
        out(f"Lowered the shoplite table to 1 read unit (was {before[0]}).")
        return lambda: lab.set_read_capacity(before[0])

    return Scenario(
        name="DynamoDB throttling",
        ground_truth=(
            "The shoplite table's read capacity was lowered from 5 units to 1, so product "
            "reads are throttled (ProvisionedThroughputExceededException)."
        ),
        inject=inject,
        expect=["capacity+throttl", "capacity+lower", "ProvisionedThroughputExceeded"],
        still_broken=lambda: lab.read_capacity() == 1,
        slug="dynamodb-throttle",
        fix="raise-dynamodb-capacity",
        # DynamoDB banks up to 5 minutes of unused capacity as burst credit, so it takes steady
        # load and a minute or two before reads throttle. The canary sends it from inside AWS.
        traffic=lambda: lab.load(seconds=20, parallel=4),
        pause=1.0,
        minutes=5.0,
    )


def broken_env_var(lab: ScenarioLab) -> Scenario:
    deployed: dict[str, Any] = {}

    def inject(out: Callable[[str], None]) -> Callable[[], None]:
        deployed.update(
            lab.deploy_settings(
                {"TABLE_NAME": BROKEN_TABLE},
                "by=chaos-lab run=broken-env: point the store at the new catalogue table",
            )
        )
        out(
            f"Deployed version {deployed['version']}: same code, TABLE_NAME={BROKEN_TABLE} "
            f"(was version {deployed['previous_version']})."
        )

        def undo() -> None:
            lab.set_live_version(deployed["previous_version"])
            lab.restore_settings(deployed["original"])

        return undo

    return Scenario(
        name="broken environment variable",
        ground_truth=(
            "A new version changed only the TABLE_NAME environment variable, to a table that "
            "doesn't exist and that ShopLite's role isn't allowed to use, so every DynamoDB call "
            "fails with AccessDeniedException (IAM refuses the call before DynamoDB looks for "
            "the table)."
        ),
        inject=inject,
        expect=[
            "TABLE_NAME",
            "environment variable",
            "AccessDenied+table",
            "table+not found",
            "table+does not exist",
            "table+doesn't exist",
        ],
        still_broken=lambda: bool(deployed) and lab.live_version() == deployed["version"],
        slug="broken-env-var",
        fix="rollback-lambda-alias",
    )


# In the order the evaluation runs them.
SCENARIOS: dict[str, Callable[[ScenarioLab], Scenario]] = {
    "error-flag": lambda lab: error_flag.scenario(lab, 0.8),
    "bad-deploy": bad_deploy.scenario,
    "dependency-latency": dependency_latency,
    "lambda-timeout": lambda_timeout,
    "memory-exhaustion": memory_exhaustion,
    "dynamodb-throttle": dynamodb_throttle,
    "broken-env-var": broken_env_var,
}
