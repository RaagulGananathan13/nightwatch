"""Runtime settings, read from the environment variables Terraform sets on each Lambda."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta


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

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        return cls(
            table_name=env["TABLE_NAME"],
            tenant_id=env.get("TENANT_ID", "default"),
            discord_webhook_param=env.get("DISCORD_WEBHOOK_PARAM", ""),
            flap_window=timedelta(minutes=float(env.get("FLAP_WINDOW_MINUTES", "10"))),
            stale_after=timedelta(hours=float(env.get("STALE_AFTER_HOURS", "6"))),
        )
