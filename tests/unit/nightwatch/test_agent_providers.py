"""LLM providers over plain HTTP, the fallback chain and the daily budget.

Keys here are obvious placeholders; nothing in this file looks like a real credential.
"""

import io
import json
import urllib.error
from typing import Any

import pytest
from botocore.exceptions import ClientError

from nightwatch.agent.providers import build_chain
from nightwatch.agent.providers.base import LLMError, LLMUnavailableError, RateLimitedError
from nightwatch.agent.providers.chain import DailyBudget, ProviderChain
from nightwatch.agent.providers.fake import FakeProvider
from nightwatch.agent.providers.gemini import Gemini
from nightwatch.agent.providers.openai_compat import groq
from tests.unit.nightwatch.conftest import TABLE

FAKE_KEY = "placeholder-key-for-tests"
MESSAGES = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi json"}]


class Reply:
    def __init__(self, body: dict[str, Any]) -> None:
        self.body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self.body

    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Opener:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.request: Any = None

    def __call__(self, request: Any, timeout: float) -> Any:
        self.request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def http_error(code: int, headers: dict[str, str] | None = None) -> urllib.error.HTTPError:
    body = io.BytesIO(json.dumps({"error": {"message": "slow down"}}).encode())
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, body)  # type: ignore[arg-type]


def test_groq_request_shape_and_reply() -> None:
    usage = {"prompt_tokens": 152, "completion_tokens": 63}
    opener = Opener(Reply({"choices": [{"message": {"content": '{"ok": true}'}}], "usage": usage}))
    reply = groq("openai/gpt-oss-120b", FAKE_KEY, opener).complete(MESSAGES, 500)
    assert (reply.text, reply.tokens, reply.provider) == ('{"ok": true}', 215, "groq")
    body = json.loads(opener.request.data)
    assert body["response_format"] == {"type": "json_object"}
    assert body["reasoning_effort"] == "low"
    assert body["include_reasoning"] is False
    assert opener.request.get_header("Authorization") == f"Bearer {FAKE_KEY}"


def test_rate_limits_carry_retry_after_and_errors_hide_the_key() -> None:
    provider = groq("m", FAKE_KEY, Opener(http_error(429, {"retry-after": "3"})))
    with pytest.raises(RateLimitedError) as caught:
        provider.complete(MESSAGES)
    assert caught.value.retry_after == 3.0
    with pytest.raises(LLMError) as failed:
        groq("m", FAKE_KEY, Opener(http_error(500))).complete(MESSAGES)
    assert FAKE_KEY not in str(failed.value)


def test_gemini_request_shape_and_reply() -> None:
    reply_body = {
        "candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}],
        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 4},
    }
    opener = Opener(Reply(reply_body))
    history = [*MESSAGES, {"role": "assistant", "content": "x"}]
    reply = Gemini("gemini-flash", FAKE_KEY, opener).complete(history)
    assert reply.text == '{"a": 1}'
    body = json.loads(opener.request.data)
    assert body["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert [c["role"] for c in body["contents"]] == ["user", "model"]
    assert opener.request.get_header("X-goog-api-key") == FAKE_KEY
    assert FAKE_KEY not in opener.request.full_url  # the key never goes in the URL


def test_chain_retries_briefly_then_falls_back() -> None:
    waits: list[float] = []
    first = FakeProvider([RateLimitedError("busy", 2.0), RateLimitedError("busy", 30.0)], "groq:a")
    second = FakeProvider(['{"ok": 1}'], "groq:b")
    chain = ProviderChain([first, second], sleep=waits.append, min_interval=0)
    assert chain.complete(MESSAGES).model == "b"
    assert waits == [2.0]  # waited for the short retry-after, not the 30 s one


def test_chain_gives_up_with_every_reason() -> None:
    failing = FakeProvider([LLMError("x"), LLMError("y")], "groq:a")
    chain = ProviderChain([failing], sleep=lambda s: None)
    with pytest.raises(LLMUnavailableError, match="groq:a: y"):
        chain.complete(MESSAGES)


def test_daily_budget_caps_requests(aws: None) -> None:
    budget = DailyBudget(TABLE, "default", daily_requests=2)
    assert budget.try_reserve("groq:a")
    assert budget.try_reserve("groq:a")
    assert not budget.try_reserve("groq:a")
    assert budget.try_reserve("groq:b")  # limits are per model
    chain = ProviderChain(
        [FakeProvider([], "groq:a"), FakeProvider(['{"ok": 1}'], "groq:b")],
        budget=budget,
        sleep=lambda s: None,
    )
    assert chain.complete(MESSAGES).model == "b"


def test_build_chain_skips_providers_without_keys() -> None:
    def secret(param: str) -> str:
        if "gemini" in param:
            raise ClientError({"Error": {"Code": "ParameterNotFound"}}, "GetParameter")
        return FAKE_KEY

    params = {"groq": "/nightwatch/llm/groq_api_key", "gemini": "/nightwatch/llm/gemini_api_key"}
    chain = build_chain(
        "groq:openai/gpt-oss-120b, gemini:gemini-2.5-flash,groq:qwen/qwen3.8-27b", secret, params
    )
    assert chain is not None
    assert [p.name for p in chain.providers] == [
        "groq:openai/gpt-oss-120b",
        "groq:qwen/qwen3.8-27b",
    ]
    assert build_chain("gemini:x", secret, {"gemini": "/nightwatch/llm/gemini_api_key"}) is None
