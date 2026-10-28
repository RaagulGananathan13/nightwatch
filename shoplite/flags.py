"""Feature flags read from SSM Parameter Store. The chaos lab flips them to inject failures.

The parameter holds JSON such as ``{"error_rate": 0.5}``. Unknown keys are ignored and bad
values fall back to "off", so a typo in the flag can never take the store down by itself.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient

CACHE_SECONDS = 10.0


@dataclass(frozen=True)
class Flags:
    error_rate: float = 0.0  # share of requests that fail with an injected error, 0 to 1
    extra_latency_ms: int = 0  # delay added to every request, like a slow dependency
    memory_hog_mb: int = 0  # memory grabbed per request, to exhaust the function's memory
    hang: bool = False  # sleep past the function timeout

    @classmethod
    def from_json(cls, text: str) -> Flags:
        try:
            raw = json.loads(text)
        except ValueError:
            return cls()
        if not isinstance(raw, dict):
            return cls()
        return cls(
            error_rate=_clamp(_number(raw.get("error_rate")), 0.0, 1.0),
            extra_latency_ms=int(_clamp(_number(raw.get("extra_latency_ms")), 0, 30_000)),
            memory_hog_mb=int(_clamp(_number(raw.get("memory_hog_mb")), 0, 10_240)),
            hang=raw.get("hang") is True,
        )


class FlagSource:
    """Reads the flags parameter at most once every ``ttl`` seconds per Lambda container."""

    def __init__(
        self,
        parameter_name: str,
        ttl: float = CACHE_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = parameter_name
        self._ttl = ttl
        self._clock = clock
        self._client: SSMClient | None = None
        self._cached: Flags | None = None
        self._fetched_at = 0.0

    def current(self) -> Flags:
        now = self._clock()
        if self._cached is not None and now - self._fetched_at < self._ttl:
            return self._cached
        try:
            if self._client is None:
                self._client = boto3.client("ssm")
            value = self._client.get_parameter(Name=self._name)["Parameter"].get("Value", "{}")
            flags = Flags.from_json(value)
        except (BotoCoreError, ClientError):
            # SSM being unreachable must not break the store: keep the last flags we saw.
            flags = self._cached or Flags()
        self._cached, self._fetched_at = flags, now
        return flags


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0.0
    return float(value)


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
