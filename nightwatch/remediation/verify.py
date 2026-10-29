"""Lambda entry point for the "Verify" step: did the fix work? (spec F7)

Runs as the read-only investigator role. It reads the runbook's metric for every full minute
after the fix and for the 5 minutes before it:
- verified: every minute after the fix is at or below the runbook's limit;
- worse: higher than at any point before the fix; the workflow then undoes the fix, if the
  runbook says how;
- not_fixed: still above the limit, but not worse;
- no_data: no datapoints at all, usually because nothing called the service.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from nightwatch.agent.tools import AwsClients
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.actions import Action, ActionStore
from nightwatch.remediation.remediate import first_full_minute

BEFORE = timedelta(minutes=5)

_aws = AwsClients()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    actions = ActionStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-verify")
    action = actions.get(str(event["incident_id"]), str(event["action_id"]))
    if action is None:
        raise ValueError(f"action {event['action_id']} not found")
    return verify(action, actions, audit, _aws.cloudwatch, utcnow())


def verify(
    action: Action, actions: ActionStore, audit: AuditLog, cloudwatch: Any, now: datetime
) -> dict[str, Any]:
    if action.status != "executed" or action.executed_at is None:
        return {"verdict": action.status}
    spec = action.verify
    start = first_full_minute(action.executed_at)
    after = _values(cloudwatch, spec, start, now)
    before = _values(cloudwatch, spec, start - timedelta(minutes=1) - BEFORE, start)
    limit = float(spec.get("max_value", 0))
    metric = f"{spec['metric']} ({spec.get('stat', 'Sum')} per minute)"
    was = f"before it, up to {max(before):g}" if before else "no data before it"

    if not after:
        verdict, detail = "no_data", f"No {spec['metric']} datapoints since the fix."
    elif max(after) <= limit:
        verdict = "verified"
        detail = f"{metric} after the fix: {_listed(after)} (limit {limit:g}); {was}."
    elif before and max(after) > max(before):
        verdict = "worse" if action.rollback else "not_fixed"
        detail = f"{metric} after the fix: {_listed(after)}, higher than {was}."
    else:
        verdict = "not_fixed"
        detail = f"{metric} after the fix: {_listed(after)}, above the limit of {limit:g}; {was}."
    verification = {"verdict": verdict, "detail": detail, "after": after, "before": before}
    action = actions.transition(action, {"executed"}, status=verdict, verification=verification)
    audit.record(
        "remediation_verified",
        action.incident_id,
        action_id=action.id,
        verdict=verdict,
        detail=detail,
    )
    return {"verdict": verdict}


def _values(cloudwatch: Any, spec: dict[str, Any], start: datetime, end: datetime) -> list[float]:
    if end <= start:
        return []
    stat = str(spec.get("stat", "Sum"))
    reply = cloudwatch.get_metric_statistics(
        Namespace=spec["namespace"],
        MetricName=spec["metric"],
        Dimensions=[{"Name": k, "Value": str(v)} for k, v in spec.get("dimensions", {}).items()],
        StartTime=start,
        EndTime=end,
        Period=60,
        Statistics=[stat],
    )
    points = sorted(reply.get("Datapoints", []), key=lambda point: point["Timestamp"])
    return [float(point[stat]) for point in points]


def _listed(values: list[float]) -> str:
    shown = ", ".join(f"{value:g}" for value in values[:10])
    return shown + (" …" if len(values) > 10 else "")
