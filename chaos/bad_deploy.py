"""Chaos scenario: a bad change makes ShopLite fail most requests.

Phase 2 version, as the spec asks: the bad deploy is simulated by turning on ShopLite's error
flag. Phase 3 replaces it with a real broken build shipped as a new Lambda version, so the
diagnosis can name the commit.

    python -m chaos.bad_deploy                      # 80% errors for 4 minutes, then clean up
    python -m chaos.bad_deploy --minutes 3 --error-rate 0.5

While the fault is on, it sends a request every 2 seconds and watches for NightWatch's
incident. The flag is always restored, even if you press Ctrl+C. Afterwards it waits for the
alarms to recover. Exit code 0 means exactly one new incident opened, with exactly one Discord
message: the Phase 2 acceptance check.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Protocol

from nightwatch.chat.cards import duration
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import utcnow

SERVICE = "shoplite"
GROUND_TRUTH = "ShopLite's error flag was switched on, so requests fail with InjectedFaultError."


class LabLike(Protocol):
    def get_flags(self) -> dict[str, object]: ...
    def set_flags(self, flags: dict[str, object]) -> None: ...
    def request(self, method: str, path: str, body: dict[str, object] | None = None) -> int: ...
    def incidents_since(self, service: str, since: datetime) -> list[Incident]: ...
    def audit(self, incident_id: str) -> list[dict[str, object]]: ...
    def seconds_until_new_incident(self, service: str) -> float: ...


def run(
    lab: LabLike,
    error_rate: float = 0.8,
    minutes: float = 4.0,
    recovery_minutes: float = 6.0,
    out: Callable[[str], None] = print,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
) -> int:
    original = lab.get_flags()
    if original:
        out(f"ShopLite already has flags set ({original}). Clear them first; stopping.")
        return 2

    wait = lab.seconds_until_new_incident(SERVICE)
    if wait:
        out(
            "The last incident resolved recently, so new alarms would reopen it (flap window). "
            f"Waiting {duration(timedelta(seconds=wait))} so this run gets its own incident..."
        )
        sleep(wait)

    started = now()
    out(
        f"Injecting fault: error_rate={error_rate} for {minutes:g} min. Ground truth: {GROUND_TRUTH}"
    )
    lab.set_flags({"error_rate": error_rate})
    statuses: Counter[int] = Counter()
    shown: tuple[str, int] | None = None
    try:
        deadline = clock() + minutes * 60
        next_check = clock()
        while clock() < deadline:
            statuses[lab.request("GET", "/products")] += 1
            if clock() >= next_check:
                shown = _report(lab, started, now(), shown, out)
                next_check = clock() + 10
            sleep(2)
    except KeyboardInterrupt:
        out("Stopping early (Ctrl+C).")
    finally:
        lab.set_flags(original)
        out("Fault removed: ShopLite's flags are back to normal.")

    out(f"Requests sent: {sum(statuses.values())} ({_status_summary(statuses)})")
    out(f"Waiting up to {recovery_minutes:g} min for the alarms to recover...")
    deadline = clock() + recovery_minutes * 60
    incidents = lab.incidents_since(SERVICE, started)
    while clock() < deadline and not (incidents and incidents[-1].status == "resolved"):
        sleep(15)
        incidents = lab.incidents_since(SERVICE, started)
    return _verdict(lab, incidents, started, out)


def _report(
    lab: LabLike,
    started: datetime,
    moment: datetime,
    shown: tuple[str, int] | None,
    out: Callable[[str], None],
) -> tuple[str, int] | None:
    incidents = lab.incidents_since(SERVICE, started)
    if not incidents:
        return shown
    latest = incidents[-1]
    if shown != (latest.id, latest.alert_count):
        after = duration(latest.opened_at - started)
        out(
            f"  {latest.label} is {latest.status}: {latest.alert_count} alert(s) grouped, opened {after} after injection"
        )
        return latest.id, latest.alert_count
    return shown


def _verdict(
    lab: LabLike, incidents: list[Incident], started: datetime, out: Callable[[str], None]
) -> int:
    if not incidents:
        out("FAIL: no incident was opened. Check the alarms and the nightwatch-ingest logs.")
        return 1
    for incident in incidents:
        sent = sum(1 for entry in lab.audit(incident.id) if entry["event"] == "notification_sent")
        took = duration(incident.opened_at - started)
        out(
            f"{incident.label}: {incident.status}, {incident.alert_count} alerts grouped "
            f"({', '.join(sorted(incident.alarms))}), opened {took} after injection, "
            f"{sent} Discord message(s) posted"
        )
    if len(incidents) != 1:
        out(f"FAIL: expected exactly 1 incident, got {len(incidents)}.")
        return 1
    sent = sum(1 for e in lab.audit(incidents[0].id) if e["event"] == "notification_sent")
    if sent != 1:
        out(f"FAIL: expected exactly 1 Discord message, got {sent}.")
        return 1
    out("PASS: one incident and one Discord message.")
    return 0


def _status_summary(statuses: Counter[int]) -> str:
    return ", ".join(f"HTTP {code}: {count}" for code, count in sorted(statuses.items())) or "none"


def main() -> None:
    from chaos.lab import Lab

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--error-rate", type=float, default=0.8, help="share of requests to fail")
    parser.add_argument("--minutes", type=float, default=4.0, help="how long the fault stays on")
    parser.add_argument("--profile", default="nightwatch", help="AWS CLI profile to use")
    args = parser.parse_args()
    if not 0 < args.error_rate <= 1:
        parser.error("--error-rate must be between 0 and 1")
    sys.exit(run(Lab.connect(args.profile), args.error_rate, args.minutes))


if __name__ == "__main__":
    main()
