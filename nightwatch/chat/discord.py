"""Small Discord clients: post a message, then keep editing it as the incident changes.

- ``DiscordWebhook`` posts incident status and diagnoses (Phase 2).
- ``DiscordBot`` posts approval requests (Phase 4). Only messages an application sends can
  carry buttons whose clicks reach NightWatch, so these go through the bot's API instead.

Plain urllib keeps the Lambda package small (ADR 0005). Two Discord details matter:
- Discord rejects Python's default user agent, so we send our own.
- Mentions are off: text that came from an alarm can never ping @everyone or a role. A
  message may ping specific users (the approvers) by listing their ids, nothing else.

The webhook URL and the bot token are secrets, so they never appear in errors or logs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = "DiscordBot (https://github.com/RaagulGananathan13/nightwatch, 0.5)"
API = "https://discord.com/api/v10"
_WEBHOOK = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")
_MAX_WAIT_SECONDS = 5.0


class DiscordError(RuntimeError):
    """Discord refused the request or could not be reached."""


class _Client:
    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 3,
        timeout: float = 5.0,
    ) -> None:
        self._opener = opener
        self._sleep = sleep
        self._attempts = attempts
        self._timeout = timeout
        self._auth: dict[str, str] = {}

    def _call(self, method: str, url: str, message: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({**message, "allowed_mentions": _mentions(message)}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT, **self._auth}
        for attempt in range(1, self._attempts + 1):
            request = urllib.request.Request(  # noqa: S310 - URL checked in __init__
                url, data=body, headers=headers, method=method
            )
            try:
                with self._opener(request, timeout=self._timeout) as response:
                    raw = response.read()
                    reply: dict[str, Any] = json.loads(raw) if raw else {}
                    return reply
            except urllib.error.HTTPError as exc:
                retry = exc.code == 429 or exc.code >= 500
                if retry and attempt < self._attempts:
                    self._sleep(_retry_after(exc) if exc.code == 429 else 0.5 * attempt)
                    continue
                raise DiscordError(f"Discord answered HTTP {exc.code}") from None
            except (urllib.error.URLError, TimeoutError):
                if attempt < self._attempts:
                    self._sleep(0.5 * attempt)
                    continue
                raise DiscordError("Discord could not be reached") from None
        raise DiscordError("Discord request failed")  # pragma: no cover - loop always returns


class DiscordWebhook(_Client):
    def __init__(self, url: str, **options: Any) -> None:
        super().__init__(**options)
        if not _WEBHOOK.match(url):
            raise ValueError("that is not a Discord webhook URL")
        self._url = url

    def send(self, message: dict[str, Any]) -> str:
        """Post a message and return its id, which ``edit`` needs later."""
        reply = self._call("POST", f"{self._url}?wait=true", _without_buttons(message))
        return str(reply["id"])

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        _check_id(message_id)
        self._call("PATCH", f"{self._url}/messages/{message_id}", _without_buttons(message))


class DiscordBot(_Client):
    """Posts to one channel as the NightWatch application, so its buttons reach us."""

    def __init__(self, token: str, channel_id: str, **options: Any) -> None:
        super().__init__(**options)
        _check_id(channel_id)
        self._channel = channel_id
        self._auth = {"Authorization": f"Bot {token}"}

    def send(self, message: dict[str, Any], reply_to: str | None = None) -> str:
        if reply_to:  # thread it under the incident's message; still posts if that's gone
            _check_id(reply_to)
            reference = {"message_id": reply_to, "fail_if_not_exists": False}
            message = {**message, "message_reference": reference}
        reply = self._call("POST", f"{API}/channels/{self._channel}/messages", message)
        return str(reply["id"])

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        _check_id(message_id)
        self._call("PATCH", f"{API}/channels/{self._channel}/messages/{message_id}", message)


def webhook_from_ssm(param: str, secret: Callable[[str], str]) -> DiscordWebhook | None:
    """The webhook whose URL is stored in SSM at ``param``. None when notifications are off."""
    return DiscordWebhook(secret(param)) if param else None


class InteractionReply(_Client):
    """Edits the reply to a slash command. The URL carries the interaction's token, a secret
    that Discord accepts for 15 minutes, so no bot token is needed."""

    def __init__(self, application_id: str, token: str, **options: Any) -> None:
        super().__init__(**options)
        _check_id(application_id)
        if not re.fullmatch(r"[\w.-]{20,500}", token):
            raise ValueError("that is not an interaction token")
        self._url = f"{API}/webhooks/{application_id}/{token}/messages/@original"

    def edit(self, message: dict[str, Any]) -> None:
        self._call("PATCH", self._url, _without_buttons(message))


def bot_from_ssm(param: str, channel_id: str, secret: Callable[[str], str]) -> DiscordBot | None:
    """The bot whose token is stored in SSM at ``param``. None when approvals are off."""
    return DiscordBot(secret(param), channel_id) if param and channel_id else None


def _mentions(message: dict[str, Any]) -> dict[str, Any]:
    """Never parse mentions out of text; allow only user ids the message lists explicitly."""
    asked = (message.get("allowed_mentions") or {}).get("users") or []
    users = [str(user) for user in asked if str(user).isdigit()][:10]
    return {"parse": [], "users": users} if users else {"parse": []}


def _without_buttons(message: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in message.items() if key != "components"}


def _check_id(snowflake: str) -> None:
    if not snowflake.isdigit():
        raise ValueError("Discord ids are numbers")


def _retry_after(exc: urllib.error.HTTPError) -> float:
    """Seconds Discord asked us to wait after a 429, capped so a Lambda never stalls."""
    try:
        wait = float(json.loads(exc.read() or b"{}").get("retry_after", 1.0))
    except (ValueError, AttributeError):
        wait = float(exc.headers.get("Retry-After", 1.0)) if exc.headers else 1.0
    return max(0.0, min(wait, _MAX_WAIT_SECONDS))
