"""A deterministic provider for tests: replies come from a script, calls are recorded."""

from __future__ import annotations

from collections.abc import Sequence

from nightwatch.agent.providers.base import LLMResponse, Message


class FakeProvider:
    def __init__(self, replies: Sequence[str | Exception], name: str = "fake:scripted") -> None:
        self.name = name
        self.provider, _, self.model = name.partition(":")
        self._replies = list(replies)
        self.calls: list[list[Message]] = []

    def complete(self, messages: Sequence[Message], max_tokens: int = 1500) -> LLMResponse:
        self.calls.append(list(messages))
        if not self._replies:
            raise AssertionError("FakeProvider ran out of scripted replies")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        prompt_chars = sum(len(m["content"]) for m in messages)
        return LLMResponse(
            text=reply,
            provider=self.provider,
            model=self.model,
            input_tokens=prompt_chars // 4,
            output_tokens=len(reply) // 4,
            latency_ms=1,
        )
