"""Run one chaos scenario by name: ``python -m chaos <scenario> [--let-nightwatch-fix]``."""

from __future__ import annotations

import argparse
import sys

from chaos.runner import run, run_fix
from chaos.scenarios import SCENARIOS


def main() -> None:
    from chaos.lab import Lab, utf8_console

    utf8_console()
    parser = argparse.ArgumentParser(prog="python -m chaos", description=__doc__)
    parser.add_argument("scenario", nargs="?", choices=sorted(SCENARIOS))
    parser.add_argument("--list", action="store_true", help="list the scenarios and stop")
    parser.add_argument("--minutes", type=float, help="how long the fault stays on")
    parser.add_argument(
        "--let-nightwatch-fix",
        action="store_true",
        help="keep the fault until NightWatch's approved fix is verified (Phase 4)",
    )
    parser.add_argument("--profile", default="nightwatch", help="AWS CLI profile to use")
    args = parser.parse_args()

    lab = Lab.connect(args.profile)
    if args.list or not args.scenario:
        for name, build in SCENARIOS.items():
            scenario = build(lab)
            print(f"{name:20} {scenario.ground_truth}")  # noqa: T201
        return
    scenario = SCENARIOS[args.scenario](lab)
    if args.let_nightwatch_fix:
        sys.exit(run_fix(lab, scenario))
    sys.exit(run(lab, scenario, args.minutes))


if __name__ == "__main__":
    main()
