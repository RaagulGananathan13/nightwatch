"""Slack, next to Discord (spec F5, F9, section 11): the same cards, buttons and commands.

NightWatch builds every card in Discord's shape (cards.py). ``to_slack`` turns one into Slack
Block Kit, so incident cards, approval cards and drafted updates read the same in both places,
and their buttons keep the same ids: a click in Slack runs the same code as a click in Discord.

Security:
- A request from Slack must carry a valid HMAC-SHA256 signature over its timestamp and body,
  made with the app's signing secret, and be at most 5 minutes old.
- Text that came from an alarm or a log can't ping anyone: everything is escaped, so
  "<!channel>" arrives as text. Only the approver pings NightWatch adds itself survive.

Plain urllib, like discord.py: the bot token and response URLs never appear in errors or logs.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from nightwatch.chat.discord import InteractionReply
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident

API = "https://slack.com/api"
REPLAY_WINDOW_SECONDS = 300
GREY = 0x8B8D98
_RESPONSE_URL = re.compile(r"https://hooks\.slack\.com/(?:actions|commands)/[\w/.-]+")
_MEMBER = re.compile(r"[UW][A-Z0-9]{2,20}")  # Slack member ids
_DISCORD_TIME = re.compile(r"<t:(\d{1,12})(?::[tTdDfFR])?>")
_DISCORD_PING = re.compile(r"<@!?\d+>\s*")
_BOLD = re.compile(r"\*\*(.+?)\*\*")
_STRIKE = re.compile(r"~~(.+?)~~")
_LINK = re.compile(r"\[([^\]]+)\]\((https://[^)\s]+)\)")
_HOLD = re.compile("\x00(\\d+)\x00")


class SlackError(RuntimeError):
    """Slack refused the request or could not be reached."""


def verify(secret: str, signature: str, timestamp: str, body: bytes, now: float) -> bool:
    """Did this request come from Slack, recently? (Slack's "v0" signing scheme.)"""
    if not (secret and signature.startswith("v0=") and timestamp.isdigit()):
        return False
    if abs(now - int(timestamp)) > REPLAY_WINDOW_SECONDS:
        return False
    signed = b"v0:" + timestamp.encode() + b":" + body
    expected = "v0=" + hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def to_slack(message: dict[str, Any], pings: Sequence[str] = ()) -> dict[str, Any]:
    """A Discord-shaped card as Slack Block Kit. ``pings`` are Slack member ids to mention."""
    content = mrkdwn(str(message.get("content") or ""))
    mentions = " ".join(f"<@{member}>" for member in pings if _MEMBER.fullmatch(member))
    content = f"{mentions} {content}".strip()
    embeds = list(message.get("embeds") or [])
    fallback = content or next((str(e["title"]) for e in embeds if e.get("title")), "NightWatch")
    blocks = [_section(content or mrkdwn(fallback))]
    attachments: list[dict[str, Any]] = [
        {
            "color": f"#{int(e.get('color', GREY)):06X}",
            "fallback": str(e.get("title") or "")[:150],
            "blocks": _embed(e),
        }
        for e in embeds
    ]
    buttons = [
        _button(b)
        for row in message.get("components") or []
        for b in row.get("components") or []
        if b.get("custom_id")
    ]
    if buttons:
        (attachments[-1]["blocks"] if attachments else blocks).append(
            {"type": "actions", "elements": buttons[:5]}
        )
    return {"text": fallback[:3000], "blocks": blocks, "attachments": attachments}


def mrkdwn(text: str) -> str:
    """Discord markdown as Slack mrkdwn, with everything that could ping or link escaped."""
    times: list[int] = []

    def hold(match: re.Match[str]) -> str:
        times.append(int(match.group(1)))
        return f"\x00{len(times) - 1}\x00"

    text = _DISCORD_PING.sub("", _DISCORD_TIME.sub(hold, text.replace("\x00", "")))
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = _LINK.sub(
        lambda m: f"<{m.group(2)}|{m.group(1)}>", _STRIKE.sub(r"~\1~", _BOLD.sub(r"*\1*", text))
    )
    return _HOLD.sub(lambda m: _date(times[int(m.group(1))]), text)


class SlackBot:
    """Posts to one channel as the NightWatch app (scope chat:write)."""

    def __init__(
        self,
        token: str,
        channel: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 5.0,
    ) -> None:
        if not re.fullmatch(r"[CG][A-Z0-9]{2,20}", channel):
            raise ValueError("that is not a Slack channel id")
        self._token, self.channel, self._opener, self._timeout = token, channel, opener, timeout

    def send(
        self, message: dict[str, Any], thread: str | None = None, pings: Sequence[str] = ()
    ) -> str:
        """Post a card and return its ts, which ``edit`` needs later."""
        body = {"channel": self.channel, **to_slack(message, pings)}
        if thread:
            body["thread_ts"] = thread
        return str(self._call("chat.postMessage", body)["ts"])

    def edit(self, ts: str, message: dict[str, Any], pings: Sequence[str] = ()) -> None:
        self._call("chat.update", {"channel": self.channel, "ts": ts, **to_slack(message, pings)})

    def _call(self, method: str, body: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(  # noqa: S310 - a fixed https:// API address
            f"{API}/{method}",
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Authorization": f"Bearer {self._token}",
            },
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                reply: dict[str, Any] = json.loads(response.read() or b"{}")
        except (urllib.error.URLError, TimeoutError, ValueError):
            raise SlackError("Slack could not be reached") from None
        if not reply.get("ok"):
            raise SlackError(f"Slack refused {method}: {str(reply.get('error', '?'))[:60]}")
        return reply


class SlackReply:
    """Answers a slash command or a click through its response URL; no token needed. It has
    the same ``edit`` as discord.InteractionReply, so the ask and updates Lambdas take either."""

    def __init__(
        self,
        response_url: str,
        opener: Callable[..., Any] = urllib.request.urlopen,
        timeout: float = 5.0,
    ) -> None:
        if not _RESPONSE_URL.fullmatch(response_url):
            raise ValueError("that is not a Slack response URL")
        self._url, self._opener, self._timeout = response_url, opener, timeout

    def edit(self, message: dict[str, Any]) -> None:
        """Post the answer for the whole channel (slash commands can't replace their reply)."""
        self.send({**to_slack(message), "response_type": "in_channel"})

    def send(self, body: dict[str, Any]) -> None:
        request = urllib.request.Request(  # noqa: S310 - checked against hooks.slack.com above
            self._url,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        try:
            with self._opener(request, timeout=self._timeout) as response:
                response.read()
        except urllib.error.HTTPError as exc:
            raise SlackError(f"Slack answered HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError):
            raise SlackError("Slack could not be reached") from None


def bot_from_ssm(param: str, channel: str, secret: Callable[[str], str]) -> SlackBot | None:
    """The bot whose token is stored in SSM at ``param``. None when Slack is off."""
    return SlackBot(secret(param), channel) if param and channel else None


def reply_for(job: dict[str, Any]) -> InteractionReply | SlackReply:
    """Where a slow command's answer goes: Slack's response URL, or Discord's interaction."""
    if job.get("slack_response_url"):
        return SlackReply(str(job["slack_response_url"]))
    return InteractionReply(str(job["application_id"]), str(job["token"]))


def show_incident(
    bot: SlackBot | None,
    store: IncidentStore,
    incident: Incident,
    message: dict[str, Any],
    create: bool = True,
) -> None:
    """Post the incident's card to Slack, or update the one there. With ``create`` False, only
    update: two alerts arriving together must not post two cards. Slack failing never stops
    the Discord side."""
    if bot is None:
        return
    try:
        if incident.slack_ts:
            bot.edit(incident.slack_ts, message)
        elif create:
            store.set_slack_message(incident.id, bot.send(message))
    except SlackError as exc:
        emit("WARNING", "Slack card failed", incident=incident.label, error=str(exc)[:200])


def _embed(embed: dict[str, Any]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    title = mrkdwn(str(embed.get("title") or ""))
    if title:
        url = str(embed.get("url") or "")
        blocks.append(
            _section(f"*<{url}|{title}>*" if url.startswith("https://") else f"*{title}*")
        )
    if embed.get("description"):
        blocks.append(_section(mrkdwn(str(embed["description"]))))
    fields = [
        {"type": "mrkdwn", "text": f"*{mrkdwn(str(f['name']))}*\n{mrkdwn(str(f['value']))}"[:2000]}
        for f in embed.get("fields") or []
    ]
    blocks += [{"type": "section", "fields": fields[i : i + 10]} for i in range(0, len(fields), 10)]
    footer = str((embed.get("footer") or {}).get("text") or "")
    if footer:
        blocks.append(
            {"type": "context", "elements": [{"type": "mrkdwn", "text": mrkdwn(footer)[:2000]}]}
        )
    return blocks


def _section(text: str) -> dict[str, Any]:
    return {"type": "section", "text": {"type": "mrkdwn", "text": (text or " ")[:3000]}}


def _button(button: dict[str, Any]) -> dict[str, Any]:
    element = {
        "type": "button",
        "text": {"type": "plain_text", "text": str(button.get("label", "?"))[:75]},
        "action_id": str(button["custom_id"])[:255],
        "value": str(button["custom_id"])[:2000],
    }
    style = {3: "primary", 4: "danger"}.get(int(button.get("style", 2)))
    return {**element, "style": style} if style else element


def _date(unix: int) -> str:
    """A Slack date, shown in each reader's own time zone."""
    shown = datetime.fromtimestamp(unix, UTC).strftime("%Y-%m-%d %H:%M UTC")
    return f"<!date^{unix}^{{date_short_pretty}} {{time}}|{shown}>"
