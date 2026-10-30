"""Register NightWatch's ``/nw`` slash command in one Discord server (spec F9).

    python -m scripts.discord_commands --guild <server id>

Server ("guild") commands appear at once; global ones can take an hour. Re-running replaces
the command with this definition. The bot token is read from SSM and never printed.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

import boto3

from nightwatch.agent.services import load_services
from nightwatch.chat.discord import API, USER_AGENT

SUBCOMMAND, TEXT = 1, 3  # Discord option types


def definition(services: list[str]) -> list[dict[str, Any]]:
    service_option: dict[str, Any] = {
        "type": TEXT,
        "name": "service",
        "description": "Which service (default: the only one)",
        "required": False,
        "choices": [{"name": name, "value": name} for name in services[:25]],
    }
    return [
        {
            "name": "nw",
            "description": "Talk to NightWatch",
            "type": 1,
            "options": [
                {"type": SUBCOMMAND, "name": "status", "description": "List open incidents"},
                {
                    "type": SUBCOMMAND,
                    "name": "ack",
                    "description": "Take an open incident",
                    "options": [
                        {
                            "type": TEXT,
                            "name": "incident",
                            "description": "For example INC-12",
                            "required": True,
                            "max_length": 12,
                        }
                    ],
                },
                {
                    "type": SUBCOMMAND,
                    "name": "update",
                    "description": "Draft a customer update and a leadership summary",
                    "options": [
                        {
                            "type": TEXT,
                            "name": "incident",
                            "description": "For example INC-12",
                            "required": True,
                            "max_length": 12,
                        }
                    ],
                },
                {
                    "type": SUBCOMMAND,
                    "name": "ask",
                    "description": "Ask a question; NightWatch investigates, read-only",
                    "options": [
                        {
                            "type": TEXT,
                            "name": "question",
                            "description": "For example: why is checkout slow?",
                            "required": True,
                            "max_length": 300,
                        },
                        service_option,
                    ],
                },
            ],
        }
    ]


def register(
    token: str,
    guild: str,
    commands: list[dict[str, Any]],
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> list[str]:
    """Replace the server's commands with ``commands``. Returns the registered names."""
    if not guild.isdigit():
        raise ValueError("a Discord server id is a number")
    headers = {"Authorization": f"Bot {token}", "User-Agent": USER_AGENT}

    def call(method: str, url: str, body: Any = None) -> Any:
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(  # noqa: S310 - fixed https host
            url, data=data, headers=headers, method=method
        )
        try:
            with opener(request, timeout=15) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"Discord refused (HTTP {exc.code}): {exc.read()[:500]!r}") from None

    application = call("GET", f"{API}/applications/@me")
    saved = call("PUT", f"{API}/applications/{application['id']}/guilds/{guild}/commands", commands)
    return [str(command["name"]) for command in saved]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--guild", required=True, help="the Discord server id")
    parser.add_argument("--token-param", default="/nightwatch/discord/bot_token")
    parser.add_argument("--profile", default="nightwatch")
    args = parser.parse_args()
    ssm = boto3.Session(profile_name=args.profile).client("ssm")
    token = ssm.get_parameter(Name=args.token_param, WithDecryption=True)["Parameter"]["Value"]
    names = register(token, args.guild, definition(sorted(load_services())))
    print(f"Registered: {', '.join('/' + name for name in names)}")  # noqa: T201


if __name__ == "__main__":
    main()
