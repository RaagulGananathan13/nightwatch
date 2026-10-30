"""Phase 6 acceptance: run one scenario twice. NightWatch must remember the first incident.

    python -m chaos.memory_check bad-deploy

It passes when:
- the first incident's postmortem is in S3 (written when the incident resolved);
- the second incident's diagnosis cites the first one. Its "Looks like" list comes from incident
  memory; the model is also asked to name a matching earlier incident, and the check reports
  whether it did.

Takes about 30 minutes: two runs, with the 10-minute flap window between them.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any, Protocol

from chaos.runner import LabLike, execute
from chaos.scenarios import SCENARIOS, ScenarioLab
from nightwatch.common.models import Incident

POSTMORTEM_WAIT_SECONDS = 180


class MemoryLab(LabLike, ScenarioLab, Protocol):
    def incident(self, incident_id: str) -> Incident | None: ...
    def postmortem(self, key: str) -> str | None: ...


def check(
    lab: MemoryLab,
    name: str,
    out: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    run_one: Callable[..., Any] = execute,
) -> int:
    out(f"Run 1 of 2: {name}")
    first = run_one(lab, SCENARIOS[name](lab), out=out)
    if first.code != 0 or not first.incidents:
        out("FAIL: the first run didn't produce a clean incident, so there is nothing to remember.")
        return 1
    one: Incident = first.incidents[0]

    waited, written = 0, None
    while waited <= POSTMORTEM_WAIT_SECONDS:
        current = lab.incident(one.id)
        if current and current.postmortem_key:
            written = lab.postmortem(current.postmortem_key)
            if written:
                break
        sleep(15)
        waited += 15
    if not written:
        out(f"FAIL: no postmortem for {one.label} in S3 after {POSTMORTEM_WAIT_SECONDS} s.")
        return 1
    out(f"Postmortem for {one.label} is in S3 ({len(written):,} characters):")
    out("  " + "\n  ".join(written.splitlines()[:12]))

    out(f"\nRun 2 of 2: {name} again")
    second = run_one(lab, SCENARIOS[name](lab), out=out)
    if not second.incidents:
        out("FAIL: the second run opened no incident.")
        return 1
    two: Incident = second.incidents[0]
    result = two.investigation_result or {}
    similar = [str(s.get("label")) for s in result.get("similar") or []]
    diagnosis = result.get("diagnosis") or {}
    text = " ".join(
        [str(diagnosis.get("summary", ""))]
        + [str(h.get("statement", "")) for h in diagnosis.get("hypotheses") or []]
    )
    out(f"{two.label}'s diagnosis says it looks like: {', '.join(similar) or 'nothing'}")
    out(f"The model's own words name {one.label}: {'yes' if one.label in text else 'no'}")
    if one.label not in similar:
        out(f"FAIL: the second diagnosis doesn't cite {one.label}.")
        return 1
    out(f"PASS: {two.label}'s diagnosis cites {one.label}, whose postmortem is in S3.")
    return 0


def main() -> None:
    from chaos.lab import Lab, utf8_console

    utf8_console()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("scenario", choices=sorted(SCENARIOS))
    parser.add_argument("--profile", default="nightwatch")
    args = parser.parse_args()
    sys.exit(check(Lab.connect(args.profile), args.scenario))


if __name__ == "__main__":
    main()
