"""Lambda entry point for Discord button clicks (spec F5, and section 11 on chat security).

Discord POSTs every interaction to this function's URL. The URL has to be public (Discord
can't sign AWS requests), so each request must prove it came from Discord:
1. An Ed25519 signature over timestamp + body, checked with the application's public key.
   Anything else gets 401, which is also what Discord's own endpoint check expects.
2. A timestamp within 5 minutes, so a captured request can't be replayed later.
Then the click itself is checked: the button must name a real action that is still waiting,
and the person clicking must be one of the service's approvers. Only then does the workflow
resume, through the task token stored on the action.

Slash commands (``/nw status``, ``/nw ack``, ``/nw ask``, ``/nw update``) arrive here too,
after the same signature check, and are handled in ``commands.py``. Drafted status updates
(spec F13) carry two more buttons, Publish to status page and Discard, under the same approver
check; drafts can be decided only once.

Slack (the spec's Slack adapter) uses the same URL. Its requests carry an HMAC-SHA256
signature instead, checked with the app's signing secret, and are reshaped into Discord's form
so the same code handles both: a Slack click answers through its response URL.

Discord and Slack want an answer within 3 seconds, so this only makes a few DynamoDB calls and
one Step Functions, Lambda or S3 call (and, for Slack, one post to its response URL).
"""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.parse
from collections.abc import Callable
from datetime import datetime
from functools import cache
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from nacl.exceptions import BadSignatureError
from nacl.signing import VerifyKey

from nightwatch.agent.services import service_info
from nightwatch.chat import slack
from nightwatch.chat.cards import approval_message, drafts_message
from nightwatch.chat.commands import COMMAND, command
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.actions import ActionStore, TransitionError
from nightwatch.status import page

if TYPE_CHECKING:
    from mypy_boto3_lambda import LambdaClient
    from mypy_boto3_stepfunctions import SFNClient

PING, SLASH_COMMAND, COMPONENT = 1, 2, 3  # interaction types
PONG, MESSAGE, DEFERRED, UPDATE_MESSAGE = 1, 4, 5, 7  # response types
EPHEMERAL = 64  # only the person who clicked sees the reply
REPLAY_WINDOW_SECONDS = 300
_CUSTOM_ID = re.compile(r"^nw:(approve|reject):([0-9A-Z]{26}):([0-9A-Z]{26})$")
_UPDATE_ID = re.compile(r"^nw:(publish|discard):([0-9A-Z]{26})$")
_secrets = SecretCache()


@cache
def _states() -> SFNClient:
    return boto3.client("stepfunctions")


@cache
def _lambda() -> LambdaClient:
    return boto3.client("lambda")


@cache
def _s3() -> Any:  # the S3 type stubs aren't installed, to keep CI's install small
    return boto3.client("s3")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    body = _body(event)
    if "x-slack-signature" in headers:
        return slack_request(settings, headers, body)
    if not verify_signature(
        settings.discord_public_key,
        headers.get("x-signature-ed25519", ""),
        headers.get("x-signature-timestamp", ""),
        body,
        time.time(),
    ):
        return _http(401, {"error": "invalid request signature"})

    interaction = json.loads(body)
    if interaction.get("type") == PING:
        return _http(200, {"type": PONG})
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-interactions")
    store = IncidentStore(settings.table_name, settings.tenant_id)
    if interaction.get("type") == SLASH_COMMAND:
        return _http(200, command(interaction, store, audit, utcnow(), _starter(settings)))
    if interaction.get("type") != COMPONENT:
        return _http(200, _private("NightWatch only handles its own buttons and commands."))
    if _UPDATE_ID.match(str((interaction.get("data") or {}).get("custom_id", ""))):
        writer = _status_page(settings, store)
        return _http(200, decide_update(interaction, store, audit, utcnow(), writer))
    actions = ActionStore(settings.table_name, settings.tenant_id)
    return _http(200, click(interaction, actions, store, audit, utcnow()))


def slack_request(settings: Settings, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    """A Slack click or /nw command: checked, then handled by the same code as Discord's."""
    param = settings.slack_signing_secret_param
    secret = _secrets.get(param) if param else ""
    signature = headers.get("x-slack-signature", "")
    stamp = headers.get("x-slack-request-timestamp", "")
    if not slack.verify(secret, signature, stamp, body, time.time()):
        return _http(401, {"error": "invalid request signature"})
    form = {
        k: v[0] for k, v in urllib.parse.parse_qs(body.decode(), keep_blank_values=True).items()
    }
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-interactions")
    store = IncidentStore(settings.table_name, settings.tenant_id)
    if "payload" in form:  # a button click
        payload = json.loads(form["payload"])
        if payload.get("type") == "block_actions":
            _answer_click(payload, settings, store, audit)
        return {"statusCode": 200, "headers": {"Content-Type": "text/plain"}, "body": ""}

    sub, _, rest = form.get("text", "").strip().partition(" ")
    if not sub:
        hint = "Try `/nw status`, `/nw ack INC-12`, `/nw ask why is checkout slow?` or `/nw update INC-12`."
        return _http(200, {"response_type": "ephemeral", "text": hint})
    interaction = {
        "data": {
            "name": COMMAND,
            "options": [
                {"name": sub.lower(), "options": _slack_options(sub.lower(), rest.strip())}
            ],
        },
        "member": {
            "user": {"id": form.get("user_id", ""), "global_name": form.get("user_name", "")}
        },
        "slack_response_url": form.get("response_url", ""),
    }
    return _http(
        200, _slack_answer(command(interaction, store, audit, utcnow(), _starter(settings)))
    )


def _slack_options(sub: str, rest: str) -> list[dict[str, str]]:
    """Slack sends "/nw ack INC-12" as plain text; Discord sends named options."""
    if sub in ("ack", "update"):
        return [{"name": "incident", "value": rest}]
    if sub == "ask":
        return [{"name": "question", "value": rest}]
    return []


def _slack_answer(answer: dict[str, Any]) -> dict[str, Any]:
    """Discord's response to a command, as Slack's."""
    if answer.get("type") == DEFERRED:
        return {
            "response_type": "ephemeral",
            "text": "NightWatch is thinking... the answer will follow.",
        }
    data = answer.get("data") or {}
    private = bool(int(data.get("flags", 0)) & EPHEMERAL)
    return {**slack.to_slack(data), "response_type": "ephemeral" if private else "in_channel"}


def _answer_click(
    payload: dict[str, Any], settings: Settings, store: IncidentStore, audit: AuditLog
) -> None:
    """Run a Slack click as a Discord one, then answer through Slack's response URL."""
    action = (payload.get("actions") or [{}])[0]
    user = payload.get("user") or {}
    name = str(user.get("name") or user.get("username") or "")
    custom_id = str(action.get("action_id", ""))
    interaction = {  # no message id: the Discord card's id must not be overwritten
        "data": {"custom_id": custom_id},
        "member": {"user": {"id": str(user.get("id", "")), "global_name": name}},
    }
    if _UPDATE_ID.match(custom_id):
        answer = decide_update(interaction, store, audit, utcnow(), _status_page(settings, store))
    else:
        actions = ActionStore(settings.table_name, settings.tenant_id)
        answer = click(interaction, actions, store, audit, utcnow())
    data = answer.get("data") or {}
    try:
        reply = slack.SlackReply(str(payload.get("response_url", "")))
        if answer.get("type") == UPDATE_MESSAGE:
            reply.send({**slack.to_slack(data), "replace_original": True})
        else:
            text = slack.mrkdwn(str(data.get("content", "")))
            reply.send({"response_type": "ephemeral", "replace_original": False, "text": text})
    except (slack.SlackError, ValueError) as exc:
        emit("WARNING", "could not answer the Slack click", error=str(exc)[:200])


def _starter(settings: Settings) -> Callable[[str, dict[str, Any]], bool]:
    """Starts a slow command's Lambda ("ask" or "updates") without waiting. False when that
    command is off or its Lambda can't start."""
    functions = {"ask": settings.ask_function, "updates": settings.updates_function}

    def start(kind: str, job: dict[str, Any]) -> bool:
        if not functions.get(kind):
            return False
        try:
            _lambda().invoke(
                FunctionName=functions[kind],
                InvocationType="Event",
                Payload=json.dumps(job).encode(),
            )
        except (BotoCoreError, ClientError):
            return False
        return True

    return start


def _status_page(settings: Settings, store: IncidentStore) -> Callable[[datetime], str] | None:
    """Rewrites the public status page and returns its address; None when there isn't one."""
    if not settings.status_bucket:
        return None

    def publish(now: datetime) -> str:
        site = _secrets.get(settings.dashboard_url_param)
        return page.publish(store, _s3(), settings.status_bucket, site, now)

    return publish


def verify_signature(
    public_key: str, signature: str, timestamp: str, body: bytes, now: float
) -> bool:
    if not (public_key and signature and timestamp.isdigit()):
        return False
    if abs(now - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        return False
    try:
        VerifyKey(bytes.fromhex(public_key)).verify(
            timestamp.encode() + body, bytes.fromhex(signature)
        )
    except (BadSignatureError, ValueError, TypeError):
        return False
    return True


def click(
    interaction: dict[str, Any],
    actions: ActionStore,
    store: IncidentStore,
    audit: AuditLog,
    now: datetime,
) -> dict[str, Any]:
    """Approve or reject one action. Returns Discord's response to the click."""
    custom_id = str((interaction.get("data") or {}).get("custom_id", ""))
    match = _CUSTOM_ID.match(custom_id)
    if not match:
        return _private("That button isn't one of NightWatch's.")
    verb, incident_id, action_id = match.groups()
    user_id, user_name = _who(interaction)

    action = actions.get(incident_id, action_id)
    if action is None:
        return _private("NightWatch doesn't know that fix.")
    service = service_info(action.service)
    approvers = service.approvers  # pinged on the Discord card
    if not service.can_approve(user_id):
        audit.record(
            "approval_refused",
            incident_id,
            action_id=action_id,
            user_id=user_id,
            user=user_name,
            why="not an approver",
        )
        return _private(f"Only {action.service}'s approvers can do that. Nothing was changed.")

    decision = "approve" if verb == "approve" else "reject"
    message_id = str((interaction.get("message") or {}).get("id", "")) or None
    try:
        action = actions.transition(
            action,
            {"awaiting_approval"},
            status="approved" if decision == "approve" else "rejected",
            decided_by=user_id,
            decided_by_name=user_name,
            decided_at=now,
            message_id=action.message_id or message_id,
        )
    except TransitionError as exc:
        return _private(f"This fix is already {exc.status.replace('_', ' ')}.")

    try:
        _states().send_task_success(
            taskToken=action.task_token or "",
            output=json.dumps({"decision": decision, "by": user_id}),
        )
    except (BotoCoreError, ClientError) as exc:  # the request timed out a moment ago
        action = actions.transition(action, {action.status}, status="expired")
        audit.record(
            "approval_too_late", incident_id, action_id=action_id, error=type(exc).__name__
        )
    else:
        audit.record(
            "approval_decided",
            incident_id,
            action_id=action_id,
            decision=decision,
            user_id=user_id,
            user=user_name,
            runbook=action.runbook_id,
            preview=action.preview,
        )
        emit("INFO", "fix decided", action=action_id, decision=decision, user=user_name)

    incident = store.get_incident(incident_id)
    if incident is None:
        return _private(f"Recorded: {decision}.")
    return {"type": UPDATE_MESSAGE, "data": approval_message(action, incident, approvers)}


def decide_update(
    interaction: dict[str, Any],
    store: IncidentStore,
    audit: AuditLog,
    now: datetime,
    publish: Callable[[datetime], str] | None,
) -> dict[str, Any]:
    """Publish or discard drafted updates (spec F13). Returns Discord's response to the click."""
    match = _UPDATE_ID.match(str((interaction.get("data") or {}).get("custom_id", "")))
    if not match:
        return _private("That button isn't one of NightWatch's.")
    verb, draft_id = match.groups()
    user_id, user_name = _who(interaction)
    draft = store.get_draft(draft_id)
    if draft is None:
        return _private("NightWatch doesn't know those drafts; drafts are kept for 30 days.")
    incident_id, service = str(draft["incident_id"]), str(draft["service"])
    if not service_info(service).can_approve(user_id):
        audit.record(
            "update_refused",
            incident_id,
            draft_id=draft_id,
            user_id=user_id,
            user=user_name,
            why="not an approver",
        )
        return _private(f"Only {service}'s approvers can do that. Nothing was published.")

    if verb == "discard":
        if not store.decide_draft(draft_id, "discarded", user_name, now):
            return _already(store, draft_id)
        audit.record(
            "updates_discarded", incident_id, draft_id=draft_id, user_id=user_id, user=user_name
        )
        return {
            "type": UPDATE_MESSAGE,
            "data": drafts_message(draft, f"🗑️ Discarded by {user_name}."),
        }

    if publish is None:
        return _private("There's no status page to publish to. The drafts are kept.")
    if not store.decide_draft(draft_id, "published", user_name, now):
        return _already(store, draft_id)
    store.add_status_update(
        {
            "draft_id": draft_id,
            "incident_id": incident_id,
            "label": draft["label"],
            "public_name": draft["public_name"],
            "state": draft["state"],
            "text": draft["customer"],
            "published_by": user_name,
        },
        now,
    )
    try:
        address = publish(now)
    except (BotoCoreError, ClientError) as exc:
        audit.record("status_page_failed", incident_id, draft_id=draft_id, error=type(exc).__name__)
        note = (
            f"⚠️ Saved by {user_name}, but the page couldn't be written ({type(exc).__name__}). "
            "The next publish includes it."
        )
        return {"type": UPDATE_MESSAGE, "data": drafts_message(draft, note)}
    audit.record(
        "status_update_published",
        incident_id,
        draft_id=draft_id,
        user_id=user_id,
        user=user_name,
        text=draft["customer"],
    )
    emit("INFO", "status update published", incident=draft["label"], user=user_name)
    published = f"✅ Published by {user_name}: {address}"
    return {"type": UPDATE_MESSAGE, "data": drafts_message(draft, published)}


def _already(store: IncidentStore, draft_id: str) -> dict[str, Any]:
    status = (store.get_draft(draft_id) or {}).get("status", "decided")
    return _private(f"These drafts were already {status}.")


def _who(interaction: dict[str, Any]) -> tuple[str, str]:
    """The clicker's Discord id and display name."""
    user = (interaction.get("member") or {}).get("user") or interaction.get("user") or {}
    name = str(user.get("global_name") or user.get("username") or "someone")[:80]
    return str(user.get("id", "")), name


def _body(event: dict[str, Any]) -> bytes:
    raw = event.get("body") or ""
    return base64.b64decode(raw) if event.get("isBase64Encoded") else raw.encode()


def _private(text: str) -> dict[str, Any]:
    return {"type": MESSAGE, "data": {"content": text, "flags": EPHEMERAL}}


def _http(status: int, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
