"""Chaos scenario: a feature flag makes ShopLite fail most requests (the Phase 2 scenario).

    python -m chaos.error_flag                     # 80% errors for 4 minutes, then clean up
    python -m chaos.error_flag --error-rate 0.5 --minutes 3

NightWatch should open one incident and trace it to the flag change on /shoplite/flags.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable
from typing import Any, Protocol

from chaos.runner import Scenario, run, run_fix


class FlagLab(Protocol):
    def get_flags(self) -> dict[str, Any]: ...
    def set_flags(self, flags: dict[str, Any]) -> None: ...


def scenario(lab: FlagLab, error_rate: float) -> Scenario:
    def inject(out: Callable[[str], None]) -> Callable[[], None]:
        lab.set_flags({"error_rate": error_rate})
        return lambda: lab.set_flags({})

    return Scenario(
        name=f"error flag (error_rate={error_rate})",
        ground_truth=(
            "ShopLite's error flag (/shoplite/flags) was switched on, so requests fail with "
            "InjectedFaultError."
        ),
        inject=inject,
        expect=["/shoplite/flags", "error_rate", "feature flag"],
        still_broken=lambda: bool(lab.get_flags()),
    )


def main() -> None:
    from chaos.lab import Lab, utf8_console

    utf8_console()

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--error-rate", type=float, default=0.8, help="share of requests to fail")
    parser.add_argument("--minutes", type=float, default=4.0, help="how long the fault stays on")
    parser.add_argument("--profile", default="nightwatch", help="AWS CLI profile to use")
    parser.add_argument(
        "--let-nightwatch-fix",
        action="store_true",
        help="keep the flag on until NightWatch's approved fix is verified (Phase 4)",
    )
    args = parser.parse_args()
    if not 0 < args.error_rate <= 1:
        parser.error("--error-rate must be between 0 and 1")
    lab = Lab.connect(args.profile)
    if args.let_nightwatch_fix:
        sys.exit(run_fix(lab, scenario(lab, args.error_rate)))
    sys.exit(run(lab, scenario(lab, args.error_rate), args.minutes))


if __name__ == "__main__":
    main()
