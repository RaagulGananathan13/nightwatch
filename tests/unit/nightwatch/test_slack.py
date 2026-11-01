"""Slack, next to Discord: signatures, cards as Block Kit, clicks and /nw commands."""

import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any

import pytest

from nightwatch.agent.services import ServiceInfo, service_info
from nightwatch.chat import commands, interactions
from nightwatch.chat.slack import (
    SlackBot,
    SlackError,
    SlackReply,
    reply_for,
    show_incident,
    to_slack,
    verify,
)
from nightwatch.common.incidents import IncidentStore
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_approvals import lambda_env, waiting  # noqa: F401 - a fixture
from tests.unit.nightwatch.test_chat import FakeResponse, Opener

SIGNING = "n" * 32  # the app's signing secret in these tests
MEMBER = "U0APPROVE1"  # a Slack approver
REPLY_URL = "https://hooks.slack.com/actions/T1/2/abc"


def sign(body: bytes, at: float | None = None, key: str = SIGNING) -> dict[str, str]:
    stamp = str(int(time.time() if at is None else at))
    digest = hmac.new(key.encode(), b"v0:" + stamp.encode() + b":" + body, hashlib.sha256)
    return {"X-Slack-Signature": f"v0={digest.hexdigest()}", "X-Slack-Request-Timestamp": stamp}


def slack_event(form: dict[str, str], key: str = SIGNING) -> dict[str, Any]:
    body = urllib.parse.urlencode(form).encode()
    return {"headers": sign(body, key=key), "body": body.decode(), "isBase64Encoded": False}


def with_member(name: str) -> ServiceInfo:
    return service_info(name).model_copy(update={"slack_approvers": [MEMBER]})


def test_slack_signatures_must_match_and_be_fresh() -> None:
    body, now = b"command=%2Fnw&text=status", time.time()
    good = sign(body)
    args = (good["X-Slack-Signature"], good["X-Slack-Request-Timestamp"])
    assert verify(SIGNING, *args, body, now)
    assert not verify("o" * 32, *args, body, now)  # another app's secret
    assert not verify(SIGNING, *args, body + b"&text=rm", now)  # the body was changed
    assert not verify("", *args, body, now)  # Slack is off
    old = sign(body, at=now - 600)
    assert not verify(
        SIGNING, old["X-Slack-Signature"], old["X-Slack-Request-Timestamp"], body, now
    )


def test_cards_become_block_kit_and_alarm_text_cannot_ping() -> None:
    card = {
        "content": "<@1048648983599403058> **INC-007** <!channel> & co, opened <t:1761818400:R>",
        "embeds": [
            {
                "title": "Proposed fix",
                "color": 0xE5484D,
                "fields": [{"name": "Change", "value": "x < y"}],
                "footer": {"text": "Runbook r"},
            }
        ],
        "components": [
            {
                "type": 1,
                "components": [
                    {"type": 2, "style": 3, "label": "Approve", "custom_id": "nw:approve:A:B"},
                    {"type": 2, "style": 4, "label": "Reject", "custom_id": "nw:reject:A:B"},
                ],
            }
        ],
    }
    out = to_slack(card, pings=[MEMBER, "1048648983599403058"])
    text = out["blocks"][0]["text"]["text"]
    assert text.startswith(
        f"<@{MEMBER}> *INC-007* &lt;!channel&gt; &amp; co, opened <!date^1761818400^"
    )
    assert "1048648983599403058" not in json.dumps(out)  # Discord ids never reach Slack
    (attachment,) = out["attachments"]
    assert attachment["color"] == "#E5484D"
    assert [b["type"] for b in attachment["blocks"]] == ["section", "section", "context", "actions"]
    assert attachment["blocks"][1]["fields"][0]["text"] == "*Change*\nx &lt; y"
    buttons = attachment["blocks"][-1]["elements"]
    assert [(b["action_id"], b["style"]) for b in buttons] == [
        ("nw:approve:A:B", "primary"),
        ("nw:reject:A:B", "danger"),
    ]


def test_a_slack_click_approves_like_a_discord_one(
    lambda_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch  # noqa: F811 - the fixture
) -> None:
    monkeypatch.setenv("SLACK_SIGNING_SECRET_PARAM", "/nightwatch/slack/signing_secret")
    monkeypatch.setattr(interactions._secrets, "get", lambda name: SIGNING)
    monkeypatch.setattr(interactions, "service_info", with_member)
    answers: list[dict[str, Any]] = []
    monkeypatch.setattr(SlackReply, "send", lambda self, body: answers.append(body))
    action = waiting(lambda_env)

    def press(user: str, key: str = SIGNING) -> int:
        payload = {
            "type": "block_actions",
            "user": {"id": user, "name": "raagul"},
            "actions": [{"action_id": f"nw:approve:{action.incident_id}:{action.id}"}],
            "response_url": REPLY_URL,
        }
        event = slack_event({"payload": json.dumps(payload)}, key=key)
        return int(interactions.handler(event, None)["statusCode"])

    assert press(MEMBER, key="o" * 32) == 401  # not signed by our app
    assert press("U0STRANGER") == 200
    assert answers[-1]["response_type"] == "ephemeral"
    assert "approvers" in answers[-1]["text"]
    assert lambda_env["states"].calls == []

    assert press(MEMBER) == 200
    assert answers[-1]["replace_original"] is True
    assert '"actions"' not in json.dumps(answers[-1])  # the decided card has no buttons
    decided = lambda_env["actions"].get(action.incident_id, action.id)
    assert (decided.status, decided.message_id) == ("approved", None)  # Discord's id untouched
    assert json.loads(lambda_env["states"].calls[0]["output"])["decision"] == "approve"


def test_slash_commands_from_slack(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("SLACK_SIGNING_SECRET_PARAM", "/nightwatch/slack/signing_secret")
    monkeypatch.setenv("ASK_FUNCTION", "nightwatch-ask")
    monkeypatch.setattr(interactions._secrets, "get", lambda name: SIGNING)
    monkeypatch.setattr(commands, "service_info", with_member)
    jobs: list[dict[str, Any]] = []

    class FakeLambda:
        def invoke(self, **kwargs: Any) -> dict[str, Any]:
            jobs.append(json.loads(kwargs["Payload"]))
            return {}

    monkeypatch.setattr(interactions, "_lambda", FakeLambda)
    store.assign_number(store.open_incident(make_alert(), None, T0))
    url = "https://hooks.slack.com/commands/T1/2/xyz"

    def run(text: str, user: str = MEMBER) -> dict[str, Any]:
        form = {"command": "/nw", "text": text, "user_id": user, "user_name": "raagul"}
        reply = interactions.handler(slack_event({**form, "response_url": url}), None)
        assert reply["statusCode"] == 200
        answer: dict[str, Any] = json.loads(reply["body"])
        return answer

    status = run("status")
    assert status["response_type"] == "in_channel"
    assert "*1 open incident(s):*" in status["blocks"][0]["text"]["text"]
    assert run("")["response_type"] == "ephemeral"  # a hint
    assert run("ask why is checkout slow?")["text"].startswith("NightWatch is thinking")
    question = {"question": "why is checkout slow?", "service": "shoplite", "user": "raagul"}
    assert jobs == [{**question, "slack_response_url": url}]
    refused = run("ack INC-1", user="U0STRANGER")
    assert (refused["response_type"], "approvers" in refused["text"]) == ("ephemeral", True)


def test_incident_cards_are_posted_once_then_edited(store: IncidentStore) -> None:
    class Bot:
        def __init__(self) -> None:
            self.sent: list[dict[str, Any]] = []
            self.edits: list[str] = []

        def send(self, message: dict[str, Any], thread: Any = None, pings: Any = ()) -> str:
            self.sent.append(message)
            return "1761818400.000100"

        def edit(self, ts: str, message: dict[str, Any], pings: Any = ()) -> None:
            self.edits.append(ts)

    bot = Bot()
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    show_incident(bot, store, incident, {"content": "x"}, create=False)  # type: ignore[arg-type]
    assert bot.sent == []  # only an opened incident posts a card
    show_incident(bot, store, incident, {"content": "x"})  # type: ignore[arg-type]
    posted = store.get_incident(incident.id)
    assert posted is not None
    assert posted.slack_ts == "1761818400.000100"
    show_incident(bot, store, posted, {"content": "y"})  # type: ignore[arg-type]
    assert bot.edits == ["1761818400.000100"]


def test_slack_refusals_and_where_answers_go() -> None:
    refused = Opener(FakeResponse({"ok": False, "error": "not_in_channel"}))
    with pytest.raises(SlackError, match="not_in_channel"):
        SlackBot("t" * 10, "C0INCIDENT", opener=refused).send({"content": "hi"})
    with pytest.raises(ValueError, match="channel id"):
        SlackBot("t" * 10, "general")
    assert isinstance(reply_for({"slack_response_url": REPLY_URL}), SlackReply)
    with pytest.raises(ValueError, match="response URL"):
        SlackReply("https://example.com/hooks")
    posted = Opener(FakeResponse({"ok": True}))
    SlackReply(REPLY_URL, opener=posted).edit({"content": "**Done**"})
    assert json.loads(posted.requests[0].data)["text"] == "*Done*"
