"""Chat models behind an OpenAI-compatible API: Groq in the cloud, Ollama on a laptop."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nightwatch.agent.providers.base import LLMError, LLMResponse, Message, Opener, post_json

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
OLLAMA_URL = "http://localhost:11434/v1/chat/completions"


def reasoning_settings(model: str) -> dict[str, Any]:
    """Reasoning models think before answering. Keep that short and out of the reply."""
    if "gpt-oss" in model:
        return {"reasoning_effort": "low", "include_reasoning": False}
    if "qwen3" in model:
        return {"reasoning_format": "hidden"}
    return {}


class OpenAICompatible:
    def __init__(
        self,
        provider: str,
        url: str,
        model: str,
        api_key: str = "",
        extra: dict[str, Any] | None = None,
        opener: Opener | None = None,
        timeout: float = 45.0,
    ) -> None:
        self.provider = provider
        self.name = f"{provider}:{model}"
        self.url = url
        self.model = model
        self._key = api_key
        self._extra = extra or {}
        self._opener = opener
        self._timeout = timeout

    def complete(self, messages: Sequence[Message], max_tokens: int = 1500) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": list(messages),
            "temperature": 0,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            **self._extra,
        }
        headers = {"Authorization": f"Bearer {self._key}"} if self._key else {}
        kwargs: dict[str, Any] = {"timeout": self._timeout}
        if self._opener is not None:
            kwargs["opener"] = self._opener
        reply, seconds = post_json(self.url, body, headers, **kwargs)
        try:
            text = reply["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMError("reply had no message content") from None
        usage = reply.get("usage") or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=int(usage.get("prompt_tokens", 0)),
            output_tokens=int(usage.get("completion_tokens", 0)),
            latency_ms=int(seconds * 1000),
        )


def groq(model: str, api_key: str, opener: Opener | None = None) -> OpenAICompatible:
    return OpenAICompatible("groq", GROQ_URL, model, api_key, reasoning_settings(model), opener)


def ollama(model: str, url: str = OLLAMA_URL, opener: Opener | None = None) -> OpenAICompatible:
    """Local models: nothing leaves the machine (spec section 11)."""
    return OpenAICompatible("ollama", url, model, opener=opener, timeout=120.0)
