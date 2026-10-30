"""Per-alarm statistics and a noise score (spec F11). Pure functions over stored records.

For each alarm over the period:
- **fired**: how many times it went to ALARM.
- **acted on**: incidents it took part in where someone acted. That means a fix that ran or
  was decided on, an acknowledgement, or a fix pull request.
- **cleared by itself**: incidents that resolved within 15 minutes with nobody acting. These
  are the pages that woke someone for nothing.
- **flaps**: reopened incidents (the alarm fired again soon after recovering).

The noise score is the share of its incidents that cleared by itself, plus 10 points per flap,
up to 100. A suggestion is only offered where the alarm's own settings point to a fix.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from statistics import median
from typing import Any

from nightwatch.common.models import Incident
from nightwatch.remediation.actions import Action

QUICK = timedelta(minutes=15)
ACTED = {"approved", "rejected", "executing", "executed", "verified", "not_fixed", "no_data"} | {
    "worse",
    "reverted",
    "revert_failed",
    "failed",
}


@dataclass
class AlarmStats:
    alarm: str
    fired: int = 0
    incidents: int = 0
    acted_on: int = 0
    cleared_by_itself: int = 0
    flaps: int = 0
    ack_minutes: list[float] = field(default_factory=list)
    noise: int = 0
    suggestion: str = ""

    def as_dict(self) -> dict[str, Any]:
        summary = asdict(self)
        summary["median_ack_minutes"] = (
            round(median(self.ack_minutes), 1) if self.ack_minutes else None
        )
        del summary["ack_minutes"]
        return summary


def acted_on(incident: Incident, actions: list[Action]) -> bool:
    """Did anyone (a person, or NightWatch with a person's approval) act on this incident?"""
    return bool(
        incident.acked_by
        or incident.fix_pr_url
        or any(action.status in ACTED for action in actions)
    )


def alarm_stats(
    records: list[tuple[Incident, list[dict[str, Any]], list[Action]]],
) -> dict[str, AlarmStats]:
    """``records``: each incident with its alerts and actions."""
    stats: dict[str, AlarmStats] = {}
    for incident, alerts, actions in records:
        acted = acted_on(incident, actions)
        took = (incident.resolved_at - incident.opened_at) if incident.resolved_at else None
        quiet = not acted and took is not None and took <= QUICK
        for name in sorted(
            {str(alert.get("alarm_name")) for alert in alerts if alert.get("alarm_name")}
        ):
            entry = stats.setdefault(name, AlarmStats(alarm=name))
            entry.fired += sum(
                1 for a in alerts if a.get("alarm_name") == name and a.get("state") == "ALARM"
            )
            entry.incidents += 1
            entry.acted_on += acted
            entry.cleared_by_itself += quiet
            entry.flaps += incident.reopen_count
            if incident.acked_at:
                entry.ack_minutes.append(
                    (incident.acked_at - incident.opened_at).total_seconds() / 60
                )
    for entry in stats.values():
        share = entry.cleared_by_itself / entry.incidents if entry.incidents else 0.0
        entry.noise = min(100, round(100 * share) + 10 * entry.flaps)
    return stats


def suggest(entry: AlarmStats, config: dict[str, Any] | None) -> str:
    """A concrete tuning step from the alarm's own settings, or an honest "keep it"."""
    if not config:
        return "Not a CloudWatch alarm NightWatch can read; check it by hand."
    periods = int(config.get("EvaluationPeriods") or 1)
    needed = int(config.get("DatapointsToAlarm") or periods)
    threshold = config.get("Threshold")
    if entry.incidents and entry.acted_on / entry.incidents >= 0.5:
        return "Useful: most of its pages led to action. Keep it."
    if entry.noise < 50:
        return "Mostly fine. Watch it next week."
    if needed == 1:
        return (
            "It pages on a single bad minute. Require 2 of 3 (evaluation_periods = 3, "
            "datapoints_to_alarm = 2), so a one-minute blip doesn't wake anyone."
        )
    if entry.flaps:
        return (
            f"It flaps. Lengthen the window (evaluation_periods {periods} → {periods * 2}) so it "
            "stays quiet until a problem lasts."
        )
    return (
        f"Its pages cleared by themselves. Consider a higher threshold than {threshold}, or "
        "whether this signal should page at all."
    )


def digest(stats: dict[str, AlarmStats]) -> dict[str, Any]:
    """The report: totals, and the alarms behind most of the noise."""
    ranked = sorted(stats.values(), key=lambda s: (s.noise * s.incidents, s.fired), reverse=True)
    noise_pages = sum(s.cleared_by_itself for s in ranked)
    noisy = [s for s in ranked if s.noise >= 50][:3]
    covered = sum(s.cleared_by_itself for s in noisy)
    return {
        "alarms": [s.as_dict() for s in ranked],
        "fired": sum(s.fired for s in ranked),
        "noise_pages": noise_pages,
        "noisy": [s.alarm for s in noisy],
        "noisy_share": round(100 * covered / noise_pages) if noise_pages else 0,
    }


def within(incident: Incident, since: datetime) -> bool:
    return incident.opened_at >= since
