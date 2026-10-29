"""Approval in Discord: signed clicks, approver checks, the card, and the report step's modes."""

import json
import time
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws
from nacl.signing import SigningKey

from nightwatch.chat import interactions, report
from nightwatch.chat.cards import approval_message
from nightwatch.chat.discord import DiscordBot, DiscordWebhook
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.remediation.actions import Action, ActionStore
from tests.unit.nightwatch.conftest import T0, TABLE, create_table, make_alert
from tests.unit.nightwatch.test_chat import FakeResponse, Opener
from tests.unit.nightwatch.test_remediation import make_action

APPROVER = "1048648983599403058"  # from nightwatch/services.yaml
KEY = SigningKey.generate()
PUBLIC = KEY.verify_key.encode().hex()


def signed(body: dict[str, Any], at: float | None = None, key: SigningKey = KEY) -> dict[str, Any]:
    raw = json.dumps(body)
    stamp = str(int(at if at is not None else time.time()))
    signature = key.sign(stamp.encode() + raw.encode()).signature.hex()
    return {
        "headers": {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": stamp},
        "body": raw,
        "isBase64Encoded": False,
    }


def test_signatures_must_be_discords_and_fresh() -> None:
    body, now = b'{"type":1}', time.time()
    stamp = str(int(now))
    good = KEY.sign(stamp.encode() + body).signature.hex()
    assert interactions.verify_signature(PUBLIC, good, stamp, body, now)
    assert not interactions.verify_signature(PUBLIC, good, stamp, body + b" ", now)  # tampered
    assert not interactions.verify_signature(PUBLIC, good, stamp, body, now + 301)  # replayed
    other = SigningKey.generate().sign(stamp.encode() + body).signature.hex()
    assert not interactions.verify_signature(PUBLIC, other, stamp, body, now)  # someone else
    assert not interactions.verify_signature(PUBLIC, "zz", stamp, body, now)
    assert not interactions.verify_signature("", good, stamp, body, now)


class FakeStates:
    def __init__(self, error: str | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error = error

    def send_task_success(self, **kwargs: Any) -> dict[str, Any]:
        if self.error:
            raise ClientError({"Error": {"Code": self.error, "Message": "x"}}, "SendTaskSuccess")
        self.calls.append(kwargs)
        return {}


@pytest.fixture
def lambda_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    with mock_aws():
        create_table()
        boto3.client("ssm").put_parameter(
            Name="/nightwatch/kill_switch", Value="false", Type="String"
        )
        monkeypatch.setenv("TABLE_NAME", TABLE)
        monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC)
        monkeypatch.setenv("DISCORD_BOT_TOKEN_PARAM", "/nightwatch/discord/bot_token")
        monkeypatch.setenv("DISCORD_CHANNEL_ID", "123")
        states = FakeStates()
        monkeypatch.setattr(interactions, "_states", lambda: states)
        monkeypatch.setattr(report, "_states", lambda: states)
        bot = FakeBot()
        monkeypatch.setattr(report, "bot_from_ssm", lambda param, channel, secret: bot)
        monkeypatch.setattr(report, "webhook_from_ssm", lambda param, secret: None)
        monkeypatch.setattr(report, "_aws", type("A", (), {"ssm": boto3.client("ssm")})())
        store = IncidentStore(TABLE, "default")
        incident = store.assign_number(store.open_incident(make_alert(), None, T0))
        store.set_discord_message(incident.id, "777")
        yield {
            "states": states,
            "bot": bot,
            "incident": store.get_incident(incident.id),
            "actions": ActionStore(TABLE, "default"),
            "audit": AuditLog(TABLE, "default", "t"),
        }


class FakeBot:
    def __init__(self) -> None:
        self.sent: list[tuple[dict[str, Any], str | None]] = []
        self.edits: list[tuple[str, dict[str, Any]]] = []

    def send(self, message: dict[str, Any], reply_to: str | None = None) -> str:
        self.sent.append((message, reply_to))
        return "900"

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        self.edits.append((message_id, message))


def waiting(env: dict[str, Any]) -> Action:
    incident: Incident = env["incident"]
    action = make_action("awaiting_approval", incident_id=incident.id, task_token="tok")
    return env["actions"].create(action)  # type: ignore[no-any-return]


def click_event(action: Action, verb: str = "approve", user: str = APPROVER) -> dict[str, Any]:
    return signed(
        {
            "type": 3,
            "data": {"custom_id": f"nw:{verb}:{action.incident_id}:{action.id}"},
            "member": {"user": {"id": user, "username": "raagul", "global_name": "Raagul"}},
            "message": {"id": "900"},
        }
    )


def test_discord_ping_and_bad_signatures(lambda_env: dict[str, Any]) -> None:
    pong = interactions.handler(signed({"type": 1}), None)
    assert pong["statusCode"] == 200
    assert json.loads(pong["body"]) == {"type": 1}
    forged = signed({"type": 1}, key=SigningKey.generate())
    assert interactions.handler(forged, None)["statusCode"] == 401
    stale = signed({"type": 1}, at=time.time() - 600)
    assert interactions.handler(stale, None)["statusCode"] == 401


def test_an_approver_click_resumes_the_workflow_once(lambda_env: dict[str, Any]) -> None:
    action = waiting(lambda_env)
    reply = json.loads(interactions.handler(click_event(action), None)["body"])
    assert reply["type"] == 7  # update the card in place
    assert reply["data"]["components"] == []  # no more buttons
    assert "Approved by Raagul" in reply["data"]["content"]
    assert lambda_env["states"].calls == [
        {"taskToken": "tok", "output": json.dumps({"decision": "approve", "by": APPROVER})}
    ]
    stored = lambda_env["actions"].get(action.incident_id, action.id)
    assert stored.status == "approved"
    assert stored.decided_by == APPROVER

    again = json.loads(interactions.handler(click_event(action, "reject"), None)["body"])
    assert again["data"] == {"content": "This fix is already approved.", "flags": 64}
    assert len(lambda_env["states"].calls) == 1
    events = [e["event"] for e in lambda_env["audit"].for_incident(action.incident_id)]
    assert events == ["approval_decided"]


def test_only_approvers_can_click_and_bad_buttons_do_nothing(lambda_env: dict[str, Any]) -> None:
    action = waiting(lambda_env)
    stranger = json.loads(interactions.handler(click_event(action, user="5"), None)["body"])
    assert stranger["data"]["flags"] == 64
    assert "approvers" in stranger["data"]["content"]
    forged_id = click_event(action)
    body = json.loads(forged_id["body"])
    body["data"]["custom_id"] = "nw:approve:../../x:y"
    junk = json.loads(interactions.handler(signed(body), None)["body"])
    assert junk["data"]["flags"] == 64
    assert lambda_env["states"].calls == []
    assert lambda_env["actions"].get(action.incident_id, action.id).status == "awaiting_approval"


def test_reject_and_a_late_click(
    lambda_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    action = waiting(lambda_env)
    interactions.handler(click_event(action, "reject"), None)
    assert lambda_env["actions"].get(action.incident_id, action.id).status == "rejected"

    late = lambda_env["actions"].create(
        make_action("awaiting_approval", id="01JB0000000000000000000LAT",
                    incident_id=action.incident_id, task_token="old")
    )  # fmt: skip
    monkeypatch.setattr(interactions, "_states", lambda: FakeStates("TaskTimedOut"))
    reply = json.loads(interactions.handler(click_event(late), None)["body"])
    assert "Nobody approved in time" in reply["data"]["content"]
    assert lambda_env["actions"].get(late.incident_id, late.id).status == "expired"


def test_request_approval_posts_buttons_and_parks_the_token(lambda_env: dict[str, Any]) -> None:
    action = lambda_env["actions"].create(
        make_action("proposed", incident_id=lambda_env["incident"].id)
    )
    out = report.handler(
        {"mode": "request_approval", "incident_id": action.incident_id,
         "action_id": action.id, "task_token": "tok-2"}, None,
    )  # fmt: skip
    assert out["status"] == "awaiting_approval"
    ((message, reply_to),) = lambda_env["bot"].sent
    assert reply_to == "777"  # threaded under the incident's message
    assert message["content"].startswith(f"<@{APPROVER}>")
    assert message["allowed_mentions"] == {"users": [APPROVER]}
    buttons = message["components"][0]["components"]
    assert [b["custom_id"] for b in buttons] == [
        f"nw:approve:{action.incident_id}:{action.id}",
        f"nw:reject:{action.incident_id}:{action.id}",
    ]
    stored = lambda_env["actions"].get(action.incident_id, action.id)
    assert stored.task_token == "tok-2"
    assert stored.message_id == "900"
    assert stored.expires_at


def test_kill_switch_answers_the_request_itself(lambda_env: dict[str, Any]) -> None:
    boto3.client("ssm").put_parameter(
        Name="/nightwatch/kill_switch", Value="true", Type="String", Overwrite=True
    )
    action = lambda_env["actions"].create(
        make_action("proposed", incident_id=lambda_env["incident"].id)
    )
    out = report.handler(
        {"mode": "request_approval", "incident_id": action.incident_id,
         "action_id": action.id, "task_token": "tok-3"}, None,
    )  # fmt: skip
    assert out["status"] == "blocked"
    assert lambda_env["states"].calls == [
        {"taskToken": "tok-3", "output": json.dumps({"decision": "blocked"})}
    ]
    ((message, _),) = lambda_env["bot"].sent
    assert message["components"] == []
    assert "kill switch" in message["content"]


def test_timeout_escalates_and_errors_end_as_failed(lambda_env: dict[str, Any]) -> None:
    action = waiting(lambda_env)
    lambda_env["actions"].transition(action, {"awaiting_approval"}, message_id="900")
    state = {"incident_id": action.incident_id, "action": {"action_id": action.id}}
    assert report.handler({"mode": "expired", "state": state}, None)["status"] == "expired"
    assert lambda_env["bot"].edits[0][0] == "900"
    escalation, _ = lambda_env["bot"].sent[0]
    assert escalation["content"].startswith(f"<@{APPROVER}> ⏰")

    other = lambda_env["actions"].create(
        make_action("approved", id="01JB0000000000000000000OTH", incident_id=action.incident_id)
    )
    state = {"incident_id": other.incident_id, "action": {"action_id": other.id},
             "error": {"Error": "Lambda.Unknown", "Cause": "boom"}}  # fmt: skip
    assert report.handler({"mode": "outcome", "state": state}, None)["status"] == "failed"


def test_bot_posts_as_the_application_and_webhooks_drop_buttons() -> None:
    opener = Opener(FakeResponse({"id": "42"}))
    bot = DiscordBot("token-for-tests", "123", opener=opener)
    message = {
        "content": "hi",
        "components": [{"type": 1}],
        "allowed_mentions": {"users": ["9", "@everyone"]},
    }
    assert bot.send(message, reply_to="777") == "42"
    request = opener.requests[0]
    assert request.full_url == "https://discord.com/api/v10/channels/123/messages"
    assert request.get_header("Authorization") == "Bot token-for-tests"
    body = json.loads(request.data)
    assert body["allowed_mentions"] == {"parse": [], "users": ["9"]}
    assert body["message_reference"] == {"message_id": "777", "fail_if_not_exists": False}
    assert body["components"] == [{"type": 1}]

    hook_opener = Opener(FakeResponse({"id": "43"}))
    DiscordWebhook("https://discord.com/api/webhooks/1/abc", opener=hook_opener).send(message)
    assert "components" not in json.loads(hook_opener.requests[0].data)


def test_card_shows_what_changes_and_how_it_is_checked(lambda_env: dict[str, Any]) -> None:
    action = make_action("verified", verification={"detail": "Errors after the fix: 0, 0."})
    card = approval_message(action, lambda_env["incident"], [APPROVER])
    fields = {f["name"]: f["value"] for f in card["embeds"][0]["fields"]}
    assert fields["Change"] == "```shoplite-api alias live: version 2 -> 1```"
    assert fields["Undo"] == "Automatic if things get worse"
    assert "stays at or below 0 for 2 min" in fields["Verified by"]
    assert card["components"] == []
    assert "allowed_mentions" not in card
    assert "Fixed and verified" in card["content"]
