"""Run one chaos scenario: inject a fault, send traffic, watch NightWatch, always undo, judge.

The verdict is the acceptance check for Phases 2 and 3:
- exactly one incident opens, with exactly one Discord message;
- an investigation finishes, and its diagnosis names what the scenario really broke (for
  example the deploy's commit SHA), with a top hypothesis the evidence guard verified.

``run_fix`` is the Phase 4 check. It leaves the fault in place until NightWatch's proposed fix
ends: Approve it in Discord and it must run, ShopLite must recover and verification must pass;
Reject it and nothing may change. The audit log must show the whole chain.

``execute`` is what the evaluation harness (``eval/``) builds on: the same run, returning
what happened instead of only a pass or fail.

The fault is always undone at the end, even after Ctrl+C or an error.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol

from nightwatch.chat.cards import duration
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.actions import FINAL, Action

SERVICE = "shoplite"
OPEN_INCIDENT_WAIT = timedelta(minutes=15)
# What a fix must leave in the audit log, in this order (spec F14).
FIX_CHAIN = [
    "remediation_proposed",
    "approval_requested",
    "approval_decided",
    "remediation_executed",
    "remediation_verified",
]


class LabLike(Protocol):
    def get_flags(self) -> dict[str, Any]: ...
    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> int: ...
    def incidents_since(self, service: str, since: datetime) -> list[Incident]: ...
    def incident_open(self, service: str) -> bool: ...
    def audit(self, incident_id: str) -> list[dict[str, Any]]: ...
    def seconds_until_new_incident(self, service: str) -> float: ...


class FixLabLike(LabLike, Protocol):
    def actions(self, incident_id: str) -> list[Action]: ...


@dataclass
class Scenario:
    name: str
    ground_truth: str
    # Applies the fault (reporting through the given output function) and returns the undo.
    inject: Callable[[Callable[[str], None]], Callable[[], None]]
    # The diagnosis must mention one of these. "a+b" means both a and b (any case).
    expect: list[str] = field(default_factory=list)
    # True while the fault is still in place; used to show that rejecting a fix changes nothing.
    still_broken: Callable[[], bool] | None = None
    slug: str = ""  # short name, e.g. "bad-deploy"
    fix: str | None = None  # the runbook that fixes it
    minutes: float = 4.0  # how long the fault stays on
    parallel: int = 1  # requests sent at once in each round (load, for throttling)
    pause: float = 2.0  # seconds between rounds


@dataclass
class Outcome:
    code: int  # 0 pass, 1 fail, 2 didn't start
    started: datetime  # just before the fault was injected
    faulted: datetime | None  # when the fault was in place
    incidents: list[Incident] = field(default_factory=list)
    statuses: Counter[int] = field(default_factory=Counter)


def mentions(text: str, expect: list[str]) -> str | None:
    """The first expected phrase found in ``text``; "a+b" needs both a and b."""
    lowered = text.lower()
    for phrase in expect:
        if all(part.strip().lower() in lowered for part in phrase.split("+")):
            return phrase
    return None


def run(
    lab: LabLike,
    scenario: Scenario,
    minutes: float | None = None,
    recovery_minutes: float = 6.0,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
) -> int:
    return execute(lab, scenario, minutes, recovery_minutes, out, clock, sleep, now).code


def execute(
    lab: LabLike,
    scenario: Scenario,
    minutes: float | None = None,
    recovery_minutes: float = 6.0,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
) -> Outcome:
    if not _ready(lab, out, sleep, clock):
        return Outcome(2, now(), None)

    started = now()
    out(f"Injecting fault: {scenario.name}. Ground truth: {scenario.ground_truth}")
    undo = scenario.inject(out)
    faulted = now()
    statuses: Counter[int] = Counter()
    shown: tuple[str, int, str | None] | None = None
    try:
        deadline = clock() + (minutes or scenario.minutes) * 60
        next_check = clock()
        with _Traffic(lab, scenario.parallel) as traffic:
            while clock() < deadline:
                statuses.update(traffic.round())
                if clock() >= next_check:
                    shown = _progress(lab, started, faulted, shown, out)
                    next_check = clock() + 10
                sleep(scenario.pause)
    except KeyboardInterrupt:
        out("Stopping early (Ctrl+C).")
    finally:
        undo()
        out("Fault removed: ShopLite is back to normal.")

    out(f"Requests sent: {sum(statuses.values())} ({_status_summary(statuses)})")
    incidents = _wait_for_recovery(lab, started, recovery_minutes, out, clock, sleep)
    code = _verdict(lab, incidents, faulted, scenario, out)
    return Outcome(code, started, faulted, incidents, statuses)


def run_fix(
    lab: FixLabLike,
    scenario: Scenario,
    wait_minutes: float = 20.0,
    recovery_minutes: float = 6.0,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
) -> int:
    """Phase 4: keep the fault (and traffic) going until NightWatch's fix is approved and
    verified, or rejected, or ``wait_minutes`` pass. Then undo the fault regardless."""
    if not _ready(lab, out, sleep, clock):
        return 2

    started = now()
    out(f"Injecting fault: {scenario.name}. Ground truth: {scenario.ground_truth}")
    undo = scenario.inject(out)
    faulted = now()
    out(
        "The fault stays in place. When the approval card appears in Discord, click Approve "
        "(or Reject, to check that nothing changes)."
    )
    statuses: Counter[int] = Counter()
    shown: tuple[str, int, str | None] | None = None
    seen: str | None = None
    still_broken: bool | None = None
    try:
        deadline = clock() + wait_minutes * 60
        next_check = clock()
        with _Traffic(lab, scenario.parallel) as traffic:
            while clock() < deadline:
                statuses.update(traffic.round())
                if clock() >= next_check:
                    shown = _progress(lab, started, faulted, shown, out)
                    action = _latest_action(lab, started)
                    if action and action.status != seen:
                        seen = action.status
                        out(f"  Fix '{action.title}' ({action.preview}): {action.status}")
                    if action and action.status in FINAL:
                        break
                    next_check = clock() + 10
                sleep(scenario.pause)
            else:
                out(f"Gave up after {wait_minutes:g} min without a final answer on the fix.")
        if scenario.still_broken:
            still_broken = scenario.still_broken()
    except KeyboardInterrupt:
        out("Stopping early (Ctrl+C).")
    finally:
        undo()
        out("Lab clean-up done: ShopLite is back to normal either way.")

    out(f"Requests sent: {sum(statuses.values())} ({_status_summary(statuses)})")
    incidents = _wait_for_recovery(lab, started, recovery_minutes, out, clock, sleep)
    code = _verdict(lab, incidents, faulted, scenario, out)
    return max(code, _fix_verdict(lab, incidents, still_broken, out))


class _Traffic:
    """Sends GET /products: one request per round, or ``parallel`` at once for load."""

    def __init__(self, lab: LabLike, parallel: int) -> None:
        self._lab = lab
        self._parallel = max(1, parallel)
        self._pool: ThreadPoolExecutor | None = None

    def __enter__(self) -> _Traffic:
        if self._parallel > 1:
            self._lab.request("GET", "/products")  # warm up credentials and the URL first
            self._pool = ThreadPoolExecutor(self._parallel)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._pool:
            self._pool.shutdown(wait=True)

    def round(self) -> list[int]:
        if self._pool is None:
            return [self._lab.request("GET", "/products")]
        futures = [
            self._pool.submit(self._lab.request, "GET", "/products") for _ in range(self._parallel)
        ]
        return [future.result() for future in futures]


def _ready(
    lab: LabLike,
    out: Callable[[str], None],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> bool:
    """Start only from a clean slate: no flags set, no open incident, past the flap window."""
    flags = lab.get_flags()
    if flags:
        out(f"ShopLite already has flags set ({flags}). Clear them first; stopping.")
        return False
    deadline = clock() + OPEN_INCIDENT_WAIT.total_seconds()
    if lab.incident_open(SERVICE):
        out("ShopLite still has an open incident; waiting for its alarms to recover first...")
        while lab.incident_open(SERVICE):
            if clock() > deadline:
                out("The open incident didn't resolve in 15 minutes; stopping.")
                return False
            sleep(30)
    wait = lab.seconds_until_new_incident(SERVICE)
    if wait:
        out(
            "The last incident resolved recently, so new alarms would reopen it (flap window). "
            f"Waiting {duration(timedelta(seconds=wait))} so this run gets its own incident..."
        )
        sleep(wait)
    return True


def _wait_for_recovery(
    lab: LabLike,
    started: datetime,
    minutes: float,
    out: Callable[[str], None],
    clock: Callable[[], float],
    sleep: Callable[[float], None],
) -> list[Incident]:
    out(f"Waiting up to {minutes:g} min for the alarms to recover...")
    deadline = clock() + minutes * 60
    incidents = lab.incidents_since(SERVICE, started)
    while clock() < deadline and not (incidents and incidents[-1].status == "resolved"):
        sleep(15)
        incidents = lab.incidents_since(SERVICE, started)
    return incidents


def _progress(
    lab: LabLike,
    started: datetime,
    faulted: datetime,
    shown: tuple[str, int, str | None] | None,
    out: Callable[[str], None],
) -> tuple[str, int, str | None] | None:
    incidents = lab.incidents_since(SERVICE, started)
    if not incidents:
        return shown
    latest = incidents[-1]
    state = (latest.id, latest.alert_count, latest.investigation)
    if state != shown:
        after = duration(latest.opened_at - faulted)
        line = f"  {latest.label} {latest.status}: {latest.alert_count} alert(s), opened {after} after injection"
        if latest.investigation:
            line += f", investigation {latest.investigation}"
        out(line)
    return state


def _verdict(
    lab: LabLike,
    incidents: list[Incident],
    faulted: datetime,
    scenario: Scenario,
    out: Callable[[str], None],
) -> int:
    if not incidents:
        out("FAIL: no incident was opened. Check the alarms and the nightwatch-ingest logs.")
        return 1
    problems: list[str] = []
    for incident in incidents:
        sent = sum(1 for e in lab.audit(incident.id) if e["event"] == "notification_sent")
        out(
            f"{incident.label}: {incident.status}, {incident.alert_count} alerts grouped "
            f"({', '.join(sorted(incident.alarms))}), opened "
            f"{duration(incident.opened_at - faulted)} after injection, {sent} Discord message(s)"
        )
        if sent != 1:
            problems.append(f"{incident.label} posted {sent} Discord messages, expected 1")
    if len(incidents) != 1:
        problems.append(f"expected exactly 1 incident, got {len(incidents)}")

    result = incidents[0].investigation_result or {}
    diagnosis = result.get("diagnosis") or {}
    hypotheses = diagnosis.get("hypotheses") or []
    if incidents[0].investigation != "done" or not hypotheses:
        problems.append(f"no diagnosis (investigation: {incidents[0].investigation})")
    else:
        top = hypotheses[0]
        how = f"{result.get('method')} via {result.get('model') or 'rules'}"
        out(f"Diagnosis ({how}): {diagnosis.get('summary')}")
        out(
            f"  Top hypothesis ({round(float(top.get('confidence', 0)) * 100)}%, "
            f"{'verified' if top.get('verified') else 'UNVERIFIED'}, evidence "
            f"{', '.join(top.get('evidence_ids', []))}): {top.get('statement')}"
        )
        text = f"{diagnosis.get('summary', '')} {top.get('statement', '')}"
        if scenario.expect and not mentions(text, scenario.expect):
            problems.append(f"the diagnosis does not mention {' or '.join(scenario.expect)}")
        if not top.get("verified") or not top.get("evidence_ids"):
            problems.append("the top hypothesis is not backed by verified evidence")

    if problems:
        for problem in problems:
            out(f"FAIL: {problem}")
        return 1
    out("PASS: one incident, one Discord message, and a verified diagnosis naming the cause.")
    return 0


def _latest_action(lab: FixLabLike, started: datetime) -> Action | None:
    incidents = lab.incidents_since(SERVICE, started)
    actions = lab.actions(incidents[0].id) if incidents else []
    return actions[-1] if actions else None


def _fix_verdict(
    lab: FixLabLike,
    incidents: list[Incident],
    still_broken: bool | None,
    out: Callable[[str], None],
) -> int:
    if not incidents:
        return 1
    actions = lab.actions(incidents[0].id)
    if not actions:
        out("FAIL: NightWatch did not propose a fix.")
        return 1
    action = actions[-1]
    events = [entry["event"] for entry in lab.audit(incidents[0].id)]
    chain = [event for event in events if event.startswith(("remediation_", "approval_"))]
    out(f"Fix: {action.title} ({action.preview}) ended as '{action.status}'. {action.result}")
    if action.verification:
        out(f"  Verification: {action.verification.get('detail')}")
    out(f"  Audit chain: {' -> '.join(chain) or 'empty'}")

    if action.status == "verified":
        positions = [chain.index(event) if event in chain else -1 for event in FIX_CHAIN]
        if -1 in positions or positions != sorted(positions):
            out(f"FAIL: the audit log should show {' -> '.join(FIX_CHAIN)}.")
            return 1
        out(
            f"PASS: approved by {action.decided_by_name}, the fix ran, ShopLite recovered and "
            "verification passed."
        )
        return 0
    if action.status == "rejected":
        if "remediation_executed" in chain or still_broken is False:
            out("FAIL: the fix was rejected but something changed anyway.")
            return 1
        out(
            f"PASS: rejected by {action.decided_by_name}, and nothing changed (the fault was "
            "still in place until the lab removed it)."
        )
        return 0
    out(f"FAIL: the fix ended as {action.status}, not verified or rejected.")
    return 1


def _status_summary(statuses: Counter[int]) -> str:
    return ", ".join(f"HTTP {code}: {count}" for code, count in sorted(statuses.items())) or "none"
