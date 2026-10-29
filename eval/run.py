"""Run every chaos scenario against the deployed stack and score NightWatch (spec F16).

    python -m eval.run                                  # all 7 scenarios, about 2.5 hours
    python -m eval.run --only lambda-timeout bad-deploy # re-run some; the rest are kept

Each scenario: wait until a new incident can open (the 10-minute flap window), inject the fault,
send traffic, undo the fault, wait for the alarms to recover, then score the incident.
The kill switch is on for the whole run, so NightWatch proposes fixes but never runs them and
pages nobody. It is put back as it was at the end, even after Ctrl+C.

Results go to eval/results.json (raw, per scenario) and eval/results.md (tables). Both are
written after every scenario, so a long run that stops halfway keeps what it measured.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from chaos.runner import LabLike, execute
from chaos.scenarios import SCENARIOS, ScenarioLab
from eval.score import EvalLab, render, score, summarize

RESULTS = Path(__file__).resolve().parent


class FullLab(LabLike, ScenarioLab, EvalLab, Protocol):
    def kill_switch(self) -> str: ...
    def set_kill_switch(self, value: str) -> None: ...


def evaluate(
    lab: FullLab,
    names: list[str],
    commit: str,
    out: Callable[[str], None] = print,
    folder: Path = RESULTS,
    run_one: Callable[..., Any] = execute,
) -> dict[str, Any]:
    runs = load(folder)
    previous = lab.kill_switch()
    lab.set_kill_switch("true")
    out("Kill switch on: fixes are proposed and recorded, never run, and nobody is paged.")
    try:
        for number, name in enumerate(names, 1):
            scenario = SCENARIOS[name](lab)
            out(f"\n[{number}/{len(names)}] {scenario.name}")
            outcome = run_one(lab, scenario, out=out)
            runs[name] = score(lab, scenario, outcome, commit)
            save(folder, runs)
    finally:
        lab.set_kill_switch(previous)
        out(f"Kill switch set back to {previous!r}.")
    return summarize(ordered(runs))


def load(folder: Path) -> dict[str, Any]:
    path = folder / "results.json"
    if not path.exists():
        return {}
    runs: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")).get("runs", {})
    return runs


def save(folder: Path, runs: dict[str, Any]) -> None:
    (folder / "results.json").write_text(
        json.dumps({"runs": runs}, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    (folder / "results.md").write_text(render(ordered(runs)), encoding="utf-8", newline="\n")


def ordered(runs: dict[str, Any]) -> list[dict[str, Any]]:
    """Runs in the catalogue's order, so the tables read the same every time."""
    return [runs[name] for name in SCENARIOS if name in runs]


def main() -> None:
    from chaos.lab import Lab, head_commit, utf8_console

    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", nargs="+", choices=list(SCENARIOS), help="scenarios to run")
    parser.add_argument("--profile", default="nightwatch", help="AWS CLI profile to use")
    args = parser.parse_args()
    names = args.only or list(SCENARIOS)
    summary = evaluate(Lab.connect(args.profile), names, head_commit())
    print(  # noqa: T201
        f"\nTop-1 {summary['top1']}/{summary['scenarios']}, top-3 {summary['top3']}/"
        f"{summary['scenarios']}, mean time to diagnosis {summary['mean_diagnosis_seconds']} s, "
        f"evidence validity {summary['verified']}/{summary['hypotheses']}. "
        "Full results: eval/results.md"
    )
    sys.exit(0)


if __name__ == "__main__":
    main()
