"""Lambda entry point for the workflow's reporting steps: everything NightWatch says in Discord.

The workflow passes a ``mode``:
- ``diagnosis`` (default): put the diagnosis on the incident's message (Phase 3). The channel
  keeps one living message per incident; if the investigation failed, it says so instead.
- ``request_approval``: post the fix's approval card and park the workflow's task token on the
  action. The workflow then waits, for free, until a button click or the timeout (Phase 4).
- ``expired``: nobody clicked in time. Mark the card and ping the approvers (escalation).
- ``outcome``: show how the fix ended: rejected, blocked, verified, not fixed or undone.
"""

from __future__ import annotations

import contextlib
import json
from datetime import timedelta
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3

from nightwatch.agent.services import service_info
from nightwatch.agent.tools import AwsClients
from nightwatch.chat.cards import approval_message, escalation_message, incident_message
from nightwatch.chat.discord import DiscordBot, DiscordWebhook, bot_from_ssm, webhook_from_ssm
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.postmortem import start as start_postmortem
from nightwatch.remediation.actions import ALL, FINAL, Action, ActionStore, TransitionError
from nightwatch.remediation.calls import kill_switch_on

if TYPE_CHECKING:
    from mypy_boto3_stepfunctions import SFNClient

_secrets = SecretCache()
_aws = AwsClients()


@cache
def _states() -> SFNClient:
    return boto3.client("stepfunctions")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    mode = event.get("mode", "diagnosis")
    if mode == "diagnosis":
        return _diagnosis(event, settings)

    state = event.get("state") or event  # expired/outcome get the whole workflow state
    incident_id = str(state["incident_id"])
    action_id = str(event.get("action_id") or state["action"]["action_id"])
    store = IncidentStore(settings.table_name, settings.tenant_id)
    actions = ActionStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-report")
    incident = store.get_incident(incident_id)
    action = actions.get(incident_id, action_id)
    if incident is None or action is None:
        raise ValueError(f"incident {incident_id} or action {action_id} not found")
    chat = _Chat(settings, actions, incident)

    if mode == "request_approval":
        action = _request_approval(settings, chat, action, str(event["task_token"]), audit)
    elif mode == "expired":
        action = _expire(chat, action, audit)
    elif mode == "outcome":
        action = _outcome(chat, action, state.get("error"), audit)
        current = store.get_incident(incident_id)
        if current and current.status == "resolved":  # rewrite it with how the fix ended
            start_postmortem(settings.postmortem_function, incident_id)
    else:
        raise ValueError(f"unknown mode {mode}")
    return {"incident_id": incident_id, "action_id": action_id, "status": action.status}


def _request_approval(
    settings: Settings, chat: _Chat, action: Action, token: str, audit: AuditLog
) -> Action:
    blocked = None
    if kill_switch_on(_aws, settings.kill_switch_param):
        blocked = "The kill switch is on."
    elif chat.bot is None:
        blocked = "No Discord bot is set up to ask for approval."
    elif not chat.approvers:
        blocked = f"{action.service} has no approvers in services.yaml."
    if blocked:
        action = chat.actions.transition(
            action, {"proposed", "awaiting_approval"}, status="blocked", result=blocked
        )
        audit.record("remediation_blocked", action.incident_id, action_id=action.id, why=blocked)
        chat.show(action)
        _states().send_task_success(taskToken=token, output=json.dumps({"decision": "blocked"}))
        return action

    # Store the token before posting, so a fast click always finds it.
    action = chat.actions.transition(
        action,
        {"proposed", "awaiting_approval"},  # a retried request replaces the old token
        status="awaiting_approval",
        task_token=token,
        expires_at=utcnow() + timedelta(minutes=settings.approval_timeout_minutes),
    )
    action = chat.show(action)
    audit.record(
        "approval_requested",
        action.incident_id,
        action_id=action.id,
        approvers=chat.approvers,
        expires_at=action.expires_at,
        message_id=action.message_id,
    )
    emit("INFO", "approval requested", incident=chat.incident.label, runbook=action.runbook_id)
    return action


def _expire(chat: _Chat, action: Action, audit: AuditLog) -> Action:
    with contextlib.suppress(TransitionError):  # if a click won the race, show its decision
        action = chat.actions.transition(
            action, {"proposed", "awaiting_approval", "approved"}, status="expired"
        )
    chat.show(action)
    if action.status == "expired":
        if chat.bot:
            chat.bot.send(
                escalation_message(action, chat.incident, chat.approvers),
                reply_to=chat.incident.discord_message_id,
            )
        audit.record("approval_expired", action.incident_id, action_id=action.id)
    return action


def _outcome(chat: _Chat, action: Action, error: Any, audit: AuditLog) -> Action:
    if error and action.status not in FINAL:
        status = "revert_failed" if action.status == "worse" else "failed"
        action = chat.actions.transition(
            action, ALL - FINAL, status=status, result=f"The workflow stopped: {_reason(error)}"
        )
    chat.show(action)
    audit.record(
        "remediation_outcome_posted", action.incident_id, action_id=action.id, status=action.status
    )
    emit("INFO", "remediation outcome", incident=chat.incident.label, status=action.status)
    return action


class _Chat:
    """Where the approval card lives: posted by the bot, or by the webhook as a last resort."""

    def __init__(self, settings: Settings, actions: ActionStore, incident: Incident) -> None:
        self.actions = actions
        self.incident = incident
        self.approvers = service_info(incident.service).approvers
        self.bot: DiscordBot | None = bot_from_ssm(
            settings.discord_bot_token_param, settings.discord_channel_id, _secrets.get
        )
        self._settings = settings

    def show(self, action: Action) -> Action:
        """Post or update the card. Returns the action with its card's message id."""
        message = approval_message(action, self.incident, self.approvers)
        if self.bot and action.message_id:
            self.bot.edit(action.message_id, message)
        elif self.bot:
            message_id = self.bot.send(message, reply_to=self.incident.discord_message_id)
            action = self.actions.transition(action, ALL, message_id=message_id)
        else:
            hook = self._webhook()
            if hook:
                hook.send(message)
        return action

    def _webhook(self) -> DiscordWebhook | None:
        return webhook_from_ssm(self._settings.discord_webhook_param, _secrets.get)


def _diagnosis(event: dict[str, Any], settings: Settings) -> dict[str, Any]:
    incident_id = str(event["incident_id"])
    store = IncidentStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-report")

    failure = event.get("error")
    if failure:
        reason = _reason(failure)
        store.set_investigation(incident_id, "failed", {"error": reason})
        audit.record("investigation_failed", incident_id, error=reason)

    incident = store.get_incident(incident_id)
    if incident is None:
        raise ValueError(f"incident {incident_id} not found")
    hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
    if hook is None:
        return {"incident_id": incident_id, "posted": False}

    message = incident_message(incident, utcnow())
    if incident.discord_message_id:
        hook.edit(incident.discord_message_id, message)
    else:  # the first post failed earlier; post now
        store.set_discord_message(incident_id, hook.send(message))
    event_name = "investigation_failure_posted" if failure else "diagnosis_posted"
    audit.record(event_name, incident_id)
    emit("INFO", event_name.replace("_", " "), incident=incident.label)
    return {"incident_id": incident_id, "posted": True}


def _reason(failure: Any) -> str:
    """A short reason from a Step Functions error object: {"Error": ..., "Cause": ...}."""
    if isinstance(failure, dict):
        return f"{failure.get('Error', 'error')}: {str(failure.get('Cause', ''))[:200]}"
    return str(failure)[:250]
