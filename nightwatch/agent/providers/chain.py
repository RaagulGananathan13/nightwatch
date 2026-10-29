"""Try providers in order, within free-tier budgets (ADR 0003).

- Each provider gets one quick retry: after a short ``retry-after`` on 429, or after 1 s on
  other errors.
- Calls to the same provider are spaced at least ``min_interval`` seconds apart.
- An optional daily budget, stored in DynamoDB, caps requests per provider per day, so a
  noisy day can't exhaust or overrun a free tier. Free limits are per model, so falling back
  from one Groq model to another also doubles the capacity.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.providers.base import (
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    Message,
    RateLimitedError,
)
from nightwatch.common.timeutil import utcnow

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

MAX_RETRY_WAIT_SECONDS = 8.0


class DailyBudget:
    """Requests and tokens per provider per UTC day, counted atomically in DynamoDB."""

    def __init__(
        self,
        table: str,
        tenant: str,
        daily_requests: int,
        client: DynamoDBClient | None = None,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self.table = table
        self.tenant = tenant
        self.limit = daily_requests
        self._client = client
        self._clock = clock

    @property
    def client(self) -> DynamoDBClient:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    def try_reserve(self, provider: str) -> bool:
        """Count one request. False once today's limit for this provider is reached."""
        try:
            self.client.update_item(
                TableName=self.table,
                Key=self._key(provider),
                UpdateExpression="ADD requests :one SET expires_at = :expires",
                ConditionExpression="attribute_not_exists(requests) OR requests < :limit",
                ExpressionAttributeValues={
                    ":one": {"N": "1"},
                    ":limit": {"N": str(self.limit)},
                    ":expires": {"N": str(int((self._clock() + timedelta(days=7)).timestamp()))},
                },
            )
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def add_tokens(self, provider: str, tokens: int) -> None:
        with contextlib.suppress(BotoCoreError, ClientError):  # bookkeeping only
            self.client.update_item(
                TableName=self.table,
                Key=self._key(provider),
                UpdateExpression="ADD tokens :n",
                ExpressionAttributeValues={":n": {"N": str(tokens)}},
            )

    def _key(self, provider: str) -> dict[str, dict[str, str]]:
        day = self._clock().strftime("%Y-%m-%d")
        return {"PK": {"S": f"TENANT#{self.tenant}"}, "SK": {"S": f"LLMUSE#{day}#{provider}"}}


class ProviderChain:
    def __init__(
        self,
        providers: Sequence[LLMProvider],
        budget: DailyBudget | None = None,
        min_interval: float = 2.5,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not providers:
            raise ValueError("a provider chain needs at least one provider")
        self.providers = list(providers)
        self.budget = budget
        self.min_interval = min_interval
        self._sleep = sleep
        self._clock = clock
        self._last_call: dict[str, float] = {}

    def complete(self, messages: Sequence[Message], max_tokens: int = 1500) -> LLMResponse:
        problems: list[str] = []
        for provider in self.providers:
            if self.budget is not None and not self.budget.try_reserve(provider.name):
                problems.append(f"{provider.name}: daily budget used up")
                continue
            for attempt in (1, 2):
                self._space_out(provider.name)
                try:
                    response = provider.complete(messages, max_tokens)
                except RateLimitedError as exc:
                    wait = exc.retry_after
                    if attempt == 1 and wait is not None and wait <= MAX_RETRY_WAIT_SECONDS:
                        self._sleep(wait)
                        continue
                    problems.append(f"{provider.name}: {exc}")
                    break
                except LLMError as exc:
                    if attempt == 1:
                        self._sleep(1.0)
                        continue
                    problems.append(f"{provider.name}: {exc}")
                    break
                if self.budget is not None:
                    self.budget.add_tokens(provider.name, response.tokens)
                return response
        raise LLMUnavailableError("; ".join(problems) or "no provider answered")

    def _space_out(self, name: str) -> None:
        last = self._last_call.get(name)
        if last is not None:
            wait = self.min_interval - (self._clock() - last)
            if wait > 0:
                self._sleep(wait)
        self._last_call[name] = self._clock()
