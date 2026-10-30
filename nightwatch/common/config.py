"""Runtime settings, read from the environment variables Terraform sets on each Lambda."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta

DEFAULT_LLM_PROVIDERS = "groq:openai/gpt-oss-120b,groq:qwen/qwen3.8-27b"


@dataclass(frozen=True)
class Settings:
    table_name: str
    tenant_id: str = "default"
    # SSM parameter holding the Discord webhook URL. Empty turns notifications off.
    discord_webhook_param: str = ""
    # An alarm that fires again this soon after its incident resolved reopens that incident.
    flap_window: timedelta = timedelta(minutes=10)
    # An open incident with no new alert for this long stops collecting new alerts.
    stale_after: timedelta = timedelta(hours=6)
    # The incident workflow (Step Functions). Empty means no automatic investigation.
    state_machine_arn: str = ""
    # LLMs to try in order, "<provider>:<model>" (ADR 0003). Keys live in SSM.
    llm_providers: str = DEFAULT_LLM_PROVIDERS
    groq_key_param: str = "/nightwatch/llm/groq_api_key"
    gemini_key_param: str = "/nightwatch/llm/gemini_api_key"
    # Requests per provider per day, kept well under the free tier.
    llm_daily_requests: int = 300
    investigation_seconds: float = 90.0
    # Phase 4: approval buttons need a Discord bot (webhooks can't receive clicks).
    discord_bot_token_param: str = ""  # SSM SecureString; empty means no approval requests
    discord_channel_id: str = ""
    discord_public_key: str = ""  # the application's Ed25519 key, used to check every click
    approval_timeout_minutes: int = 30
    # Spec F14: "true" (or anything but "false", or unreadable) turns every fix off.
    kill_switch_param: str = "/nightwatch/kill_switch"
    # Phase 6: postmortems (S3) and the Lambdas other steps start.
    postmortem_bucket: str = ""
    postmortem_function: str = ""  # empty: no postmortems
    ask_function: str = ""  # empty: /nw ask is off

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            table_name=env["TABLE_NAME"],
            tenant_id=env.get("TENANT_ID", "default"),
            discord_webhook_param=env.get("DISCORD_WEBHOOK_PARAM", ""),
            flap_window=timedelta(minutes=float(env.get("FLAP_WINDOW_MINUTES", "10"))),
            stale_after=timedelta(hours=float(env.get("STALE_AFTER_HOURS", "6"))),
            state_machine_arn=env.get("STATE_MACHINE_ARN", ""),
            llm_providers=env.get("LLM_PROVIDERS", DEFAULT_LLM_PROVIDERS),
            groq_key_param=env.get("GROQ_KEY_PARAM", "/nightwatch/llm/groq_api_key"),
            gemini_key_param=env.get("GEMINI_KEY_PARAM", "/nightwatch/llm/gemini_api_key"),
            llm_daily_requests=int(env.get("LLM_DAILY_REQUESTS", "300")),
            investigation_seconds=float(env.get("INVESTIGATION_SECONDS", "90")),
            discord_bot_token_param=env.get("DISCORD_BOT_TOKEN_PARAM", ""),
            discord_channel_id=env.get("DISCORD_CHANNEL_ID", ""),
            discord_public_key=env.get("DISCORD_PUBLIC_KEY", ""),
            approval_timeout_minutes=int(env.get("APPROVAL_TIMEOUT_MINUTES", "30")),
            kill_switch_param=env.get("KILL_SWITCH_PARAM", "/nightwatch/kill_switch"),
            postmortem_bucket=env.get("POSTMORTEM_BUCKET", ""),
            postmortem_function=env.get("POSTMORTEM_FUNCTION", ""),
            ask_function=env.get("ASK_FUNCTION", ""),
        )
