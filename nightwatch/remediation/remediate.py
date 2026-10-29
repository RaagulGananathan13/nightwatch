"""Lambda entry point for the "Remediate" step: run one approved runbook action (spec F5, F7).

Runs as the remediator role, which may only make the allow-listed calls, and only on the
watched service's resources (spec section 11). Before changing anything it checks:
1. the kill switch is off (spec F14);
2. a human approved this action (or its runbook is low-risk and marked auto_approve);
3. the stored call is exactly what the runbook renders from the stored params, and it targets
   the service's own resources, so an edited database item can't aim it anywhere else;
4. the resource is still in the state the approver saw.

Mode ``revert`` puts things back with the runbook's rollback when verification finds that the
fix made things worse.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.services import service_info
from nightwatch.agent.tools import AwsClients
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.log import emit
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.actions import Action, ActionStore
from nightwatch.remediation.calls import (
    CallError,
    check_target,
    describe,
    execute,
    kill_switch_on,
    read_state,
)
from nightwatch.remediation.runbooks import load_runbooks

METRIC_DELAY = timedelta(seconds=60)  # Lambda metrics reach CloudWatch about a minute late

_aws = AwsClients()


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    actions = ActionStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-remediate")
    action = actions.get(str(event["incident_id"]), str(event["action_id"]))
    if action is None:
        raise ValueError(f"action {event['action_id']} not found")
    if event.get("mode") == "revert":
        return revert(action, actions, audit, settings, _aws)
    return fix(action, actions, audit, settings, _aws)


def fix(
    action: Action, actions: ActionStore, audit: AuditLog, settings: Settings, aws: Any
) -> dict[str, Any]:
    if action.status == "executed":  # a retried invocation; never run a fix twice
        return _reply(action, utcnow())
    if not (action.status == "approved" or (action.auto_approve and action.status == "proposed")):
        return _reply(action, utcnow())
    allowed = {"approved", "proposed"}
    if kill_switch_on(aws, settings.kill_switch_param):
        why = "The kill switch was turned on, so the fix did not run."
        return _stop(action, actions, audit, allowed, "blocked", why)
    try:
        _check(action, action.call, "action")
        current = read_state(aws, action.call)
    except (CallError, KeyError, ValueError, BotoCoreError, ClientError) as exc:
        return _stop(action, actions, audit, allowed, "failed", f"Refused: {exc}")
    if current != action.before:
        why = f"It changed since the fix was proposed ({_diff(action.before, current)})."
        return _stop(action, actions, audit, allowed, "failed", why)

    action = actions.transition(action, allowed, status="executing")
    try:
        execute(aws, action.call)
    except (BotoCoreError, ClientError, CallError) as exc:
        return _stop(action, actions, audit, {"executing"}, "failed", f"AWS said: {_error(exc)}")
    executed_at = utcnow()
    try:
        after = read_state(aws, action.call)
    except (BotoCoreError, ClientError):
        after = {}
    action = actions.transition(
        action,
        {"executing"},
        status="executed",
        executed_at=executed_at,
        after=after,
        result=action.preview,
    )
    audit.record(
        "remediation_executed",
        action.incident_id,
        action_id=action.id,
        runbook=action.runbook_id,
        call=describe(action.call),
        before=action.before,
        after=after,
        approved_by=action.decided_by or "auto_approve",
    )
    emit("INFO", "fix executed", action=action.id, runbook=action.runbook_id)
    return _reply(action, executed_at)


def revert(
    action: Action, actions: ActionStore, audit: AuditLog, settings: Settings, aws: Any
) -> dict[str, Any]:
    """Undo the fix with the runbook's rollback (spec F7: auto-revert if worse)."""
    rollback = action.rollback
    if action.status != "worse" or not rollback:
        return _reply(action, utcnow())
    if kill_switch_on(aws, settings.kill_switch_param):
        why = "The kill switch is on, so the fix was not undone."
        return _stop(action, actions, audit, {"worse"}, "revert_failed", why)
    try:
        _check(action, rollback, "rollback")
        current = read_state(aws, action.call)
        if action.after and current != action.after:
            raise CallError(f"it changed after the fix ({_diff(action.after, current)})")
        execute(aws, rollback)
    except (CallError, KeyError, ValueError, BotoCoreError, ClientError) as exc:
        return _stop(action, actions, audit, {"worse"}, "revert_failed", f"{_error(exc)}.")
    action = actions.transition(
        action, {"worse"}, status="reverted", result=f"Ran {describe(rollback)}."
    )
    audit.record(
        "remediation_reverted",
        action.incident_id,
        action_id=action.id,
        call=describe(rollback),
    )
    return _reply(action, utcnow())


def _check(action: Action, call: dict[str, Any], part: str) -> None:
    """The stored call must be what the reviewed runbook gives for the stored params."""
    book = load_runbooks().get(action.runbook_id)
    if book is None:
        raise CallError(f"runbook {action.runbook_id} no longer exists")
    template = book.action if part == "action" else book.rollback
    if template is None or book.render(template.model_dump(), action.params) != call:
        raise CallError(f"the stored {part} doesn't match runbook {book.id}")
    check_target(service_info(action.service), call)


def _stop(
    action: Action,
    actions: ActionStore,
    audit: AuditLog,
    allowed: set[str],
    status: str,
    why: str,
) -> dict[str, Any]:
    action = actions.transition(action, allowed, status=status, result=why)
    audit.record(f"remediation_{status}", action.incident_id, action_id=action.id, why=why)
    emit("WARNING", "fix stopped", action=action.id, status=status, why=why)
    return _reply(action, utcnow())


def _reply(action: Action, now: datetime) -> dict[str, Any]:
    """The step's output. verify_after_seconds is how long to wait before checking the fix."""
    wait = 0
    if action.status == "executed" and action.executed_at:
        minutes = int(action.verify.get("minutes", 2))
        ready = first_full_minute(action.executed_at) + timedelta(minutes=minutes) + METRIC_DELAY
        wait = max(30, min(900, int((ready - now).total_seconds())))
    return {"status": action.status, "verify_after_seconds": wait}


def first_full_minute(moment: datetime) -> datetime:
    """CloudWatch's per-minute datapoints: the first one that is entirely after ``moment``."""
    return moment.replace(second=0, microsecond=0) + timedelta(minutes=1)


def _diff(before: dict[str, str], now: dict[str, str]) -> str:
    changed = [
        f"{key} was {before.get(key)}, now {value}"
        for key, value in sorted(now.items())
        if before.get(key) != value
    ]
    return "; ".join(changed) or "unknown"


def _error(exc: Exception) -> str:
    if isinstance(exc, ClientError):
        error = exc.response.get("Error", {})
        return f"{error.get('Code', 'error')}: {str(error.get('Message', ''))[:150]}"
    return str(exc)[:200]
