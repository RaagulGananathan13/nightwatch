"""The LLM provider interface (spec 10.2) and the plain-HTTP plumbing every provider shares.

Providers are called over urllib, with no vendor SDKs (ADR 0005). API keys travel only in
request headers and never appear in errors, logs or audit entries.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

Message = dict[str, str]  # {"role": "system" | "user" | "assistant", "content": "..."}
Opener = Callable[..., Any]

USER_AGENT = "NightWatch (https://github.com/RaagulGananathan13/nightwatch)"


@dataclass(frozen=True)
class LLMResponse:
    text: str
    provider: str  # e.g. "groq"
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int

    @property
    def tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMError(RuntimeError):
    """This provider failed; another one may still work."""


class RateLimitedError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailableError(RuntimeError):
    """Every configured provider failed or is out of budget."""


class LLMProvider(Protocol):
    name: str  # "<provider>:<model>", e.g. "groq:openai/gpt-oss-120b"

    def complete(self, messages: Sequence[Message], max_tokens: int = 1500) -> LLMResponse:
        """Return the model's reply, which is asked to be a single JSON object."""
        ...


def post_json(
    url: str,
    body: dict[str, Any],
    headers: dict[str, str],
    opener: Opener = urllib.request.urlopen,
    timeout: float = 45.0,
) -> tuple[dict[str, Any], float]:
    """POST JSON and return (reply, seconds). Raises LLMError types, never leaking headers."""
    request = urllib.request.Request(  # noqa: S310 - fixed https endpoints or local Ollama
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT, **headers},
        method="POST",
    )
    started = time.monotonic()
    try:
        with opener(request, timeout=timeout) as response:
            reply: dict[str, Any] = json.loads(response.read())
            return reply, time.monotonic() - started
    except urllib.error.HTTPError as exc:
        detail = _error_detail(exc)
        if exc.code == 429:
            raise RateLimitedError(f"rate limited: {detail}", _retry_after(exc)) from None
        raise LLMError(f"HTTP {exc.code}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise LLMError(f"request failed: {type(exc).__name__}") from None


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read() or b"{}")
    except ValueError:
        return "no details"
    error = body.get("error", body)
    message = error.get("message") if isinstance(error, dict) else error
    return str(message)[:200]


def _retry_after(exc: urllib.error.HTTPError) -> float | None:
    value = exc.headers.get("retry-after") if exc.headers else None
    try:
        return float(value) if value is not None else None
    except ValueError:
        return None
