"""Google Gemini over its REST API. The key goes in a header, never in the URL."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from nightwatch.agent.providers.base import LLMError, LLMResponse, Message, Opener, post_json

BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class Gemini:
    def __init__(self, model: str, api_key: str, opener: Opener | None = None) -> None:
        self.provider = "gemini"
        self.name = f"gemini:{model}"
        self.model = model
        self._key = api_key
        self._opener = opener

    def complete(self, messages: Sequence[Message], max_tokens: int = 1500) -> LLMResponse:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        contents = [
            {
                "role": "model" if m["role"] == "assistant" else "user",
                "parts": [{"text": m["content"]}],
            }
            for m in messages
            if m["role"] != "system"
        ]
        body: dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0,
                "maxOutputTokens": max_tokens,
                "responseMimeType": "application/json",
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        kwargs: dict[str, Any] = {}
        if self._opener is not None:
            kwargs["opener"] = self._opener
        reply, seconds = post_json(
            f"{BASE}/{self.model}:generateContent", body, {"x-goog-api-key": self._key}, **kwargs
        )
        try:
            parts = reply["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError):
            raise LLMError("reply had no candidates") from None
        usage = reply.get("usageMetadata") or {}
        return LLMResponse(
            text=text,
            provider=self.provider,
            model=self.model,
            input_tokens=int(usage.get("promptTokenCount", 0)),
            output_tokens=int(usage.get("candidatesTokenCount", 0)),
            latency_ms=int(seconds * 1000),
        )
