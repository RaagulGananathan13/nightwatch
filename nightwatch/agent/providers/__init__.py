"""LLM provider adapters: Groq (default), Gemini, Ollama, and a deterministic fake for tests.

``build_chain`` turns a setting like ``"groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b"`` into
a ProviderChain. Providers whose API key isn't stored in SSM are skipped, so adding a key later
needs no redeploy.
"""

from __future__ import annotations

from collections.abc import Callable

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.providers.base import LLMProvider
from nightwatch.agent.providers.chain import DailyBudget, ProviderChain
from nightwatch.agent.providers.gemini import Gemini
from nightwatch.agent.providers.openai_compat import groq, ollama
from nightwatch.common.log import emit


def build_chain(
    spec: str,
    secret: Callable[[str], str],
    key_params: dict[str, str],
    budget: DailyBudget | None = None,
) -> ProviderChain | None:
    """``secret(param)`` reads an SSM SecureString. Returns None when no provider is usable."""
    providers: list[LLMProvider] = []
    for entry in (part.strip() for part in spec.split(",")):
        kind, _, model = entry.partition(":")
        if not model:
            continue
        if kind == "ollama":
            providers.append(ollama(model))
            continue
        param = key_params.get(kind)
        if param is None:
            emit("WARN", "unknown LLM provider skipped", provider=kind)
            continue
        try:
            key = secret(param)
        except (BotoCoreError, ClientError):
            emit("INFO", "LLM provider skipped: no API key stored", provider=kind, parameter=param)
            continue
        providers.append(groq(model, key) if kind == "groq" else Gemini(model, key))
    return ProviderChain(providers, budget) if providers else None
