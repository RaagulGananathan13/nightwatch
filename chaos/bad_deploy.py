"""Chaos scenario: a buggy commit is deployed and breaks product listings (Phase 3 acceptance).

    python -m chaos.bad_deploy                # deploy the bug, 4 minutes of traffic, roll back

What happens:
1. In a throwaway git worktree, a real commit renames the price field ShopLite reads from
   DynamoDB, so listing products and placing orders raise KeyError. The commit is never pushed.
2. That build ships exactly like CI ships code: a new Lambda version described as
   ``sha=<commit>``, and the ``live`` alias moves to it.
3. NightWatch should open one incident and name that commit as the cause.
4. The alias always moves back to the previous version afterwards, even after Ctrl+C.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Protocol

from chaos.runner import Scenario, run

SUBJECT = "read product prices from the new price field"


class DeployLab(Protocol):
    def build_buggy_shoplite(self, subject: str) -> tuple[str, bytes]: ...
    def deploy(self, code: bytes, description: str) -> dict[str, str]: ...
    def set_live_version(self, version: str) -> None: ...


def scenario(lab: DeployLab) -> Scenario:
    expect: list[str] = []

    def inject(out: Callable[[str], None]) -> Callable[[], None]:
        sha, code = lab.build_buggy_shoplite(SUBJECT)
        result = lab.deploy(code, f"sha={sha} by=chaos-lab run=bad-deploy: {SUBJECT}")
        expect.append(sha[:7])  # the diagnosis must name the commit
        previous = result["previous_version"]
        out(f"Deployed commit {sha} as version {result['version']} (was version {previous}).")
        return lambda: lab.set_live_version(previous)

    return Scenario(
        name="bad deploy",
        ground_truth=(
            f"Commit '{SUBJECT}' was deployed and makes product listings and orders raise "
            "KeyError: 'price'."
        ),
        inject=inject,
        expect=expect,
    )


def main() -> None:
    from chaos.lab import Lab

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--minutes", type=float, default=4.0, help="how long the bad version stays live"
    )
    parser.add_argument("--profile", default="nightwatch", help="AWS CLI profile to use")
    args = parser.parse_args()
    lab = Lab.connect(args.profile)
    sys.exit(run(lab, scenario(lab), args.minutes))


if __name__ == "__main__":
    main()
