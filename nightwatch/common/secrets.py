"""Secrets from SSM Parameter Store (SecureString), cached per Lambda container.

Every uncached read costs one KMS request; the free tier allows 20,000 a month (ADR 0007).
Secret values are never logged.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

import boto3

if TYPE_CHECKING:
    from mypy_boto3_ssm import SSMClient


class SecretCache:
    def __init__(
        self,
        ttl_seconds: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        client: SSMClient | None = None,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._client = client
        self._values: dict[str, tuple[float, str]] = {}

    def get(self, name: str) -> str:
        now = self._clock()
        cached = self._values.get(name)
        if cached and now - cached[0] < self._ttl:
            return cached[1]
        if self._client is None:
            self._client = boto3.client("ssm")
        parameter = self._client.get_parameter(Name=name, WithDecryption=True)["Parameter"]
        value = parameter.get("Value", "")
        self._values[name] = (now, value)
        return value
