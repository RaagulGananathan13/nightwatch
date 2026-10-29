"""A small Discord webhook client: post a message, then keep editing it as the incident changes.

Plain urllib keeps the Lambda package small (ADR 0005). Two Discord details matter:
- Discord rejects Python's default user agent, so we send our own.
- Every message disables mentions, so text that came from an alarm can never ping @everyone.

The webhook URL is a secret, so it never appears in error messages or logs.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

USER_AGENT = "NightWatch (https://github.com/RaagulGananathan13/nightwatch, 0.3)"
_WEBHOOK = re.compile(r"^https://(?:discord|discordapp)\.com/api/webhooks/\d+/[\w-]+$")
_MAX_WAIT_SECONDS = 5.0


class DiscordError(RuntimeError):
    """Discord refused the request or could not be reached."""


class DiscordWebhook:
    def __init__(
        self,
        url: str,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
        attempts: int = 3,
        timeout: float = 5.0,
    ) -> None:
        if not _WEBHOOK.match(url):
            raise ValueError("that is not a Discord webhook URL")
        self._url = url
        self._opener = opener
        self._sleep = sleep
        self._attempts = attempts
        self._timeout = timeout

    def send(self, message: dict[str, Any]) -> str:
        """Post a message and return its id, which ``edit`` needs later."""
        reply = self._call("POST", f"{self._url}?wait=true", message)
        return str(reply["id"])

    def edit(self, message_id: str, message: dict[str, Any]) -> None:
        if not message_id.isdigit():
            raise ValueError("Discord message ids are numbers")
        self._call("PATCH", f"{self._url}/messages/{message_id}", message)

    def _call(self, method: str, url: str, message: dict[str, Any]) -> dict[str, Any]:
        body = json.dumps({**message, "allowed_mentions": {"parse": []}}).encode()
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
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


def webhook_from_ssm(param: str, secret: Callable[[str], str]) -> DiscordWebhook | None:
    """The webhook whose URL is stored in SSM at ``param``. None when notifications are off."""
    return DiscordWebhook(secret(param)) if param else None


def _retry_after(exc: urllib.error.HTTPError) -> float:
    """Seconds Discord asked us to wait after a 429, capped so a Lambda never stalls."""
    try:
        wait = float(json.loads(exc.read() or b"{}").get("retry_after", 1.0))
    except (ValueError, AttributeError):
        wait = float(exc.headers.get("Retry-After", 1.0)) if exc.headers else 1.0
    return max(0.0, min(wait, _MAX_WAIT_SECONDS))
