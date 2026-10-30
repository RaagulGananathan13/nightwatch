"""The /nw command definition and its registration in one Discord server."""

import io
import json
from typing import Any

import pytest

from scripts.discord_commands import definition, register


class Reply(io.BytesIO):
    def __enter__(self) -> "Reply":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def test_definition_has_status_ack_and_ask_with_service_choices() -> None:
    (command,) = definition(["shoplite"])
    assert command["name"] == "nw"
    assert [o["name"] for o in command["options"]] == ["status", "ack", "ask"]
    ask = command["options"][2]["options"]
    assert ask[0]["max_length"] == 300
    assert ask[1]["choices"] == [{"name": "shoplite", "value": "shoplite"}]


def test_register_replaces_the_servers_commands() -> None:
    sent: list[Any] = []

    def opener(request: Any, timeout: float) -> Reply:
        sent.append(request)
        if request.get_method() == "GET":
            return Reply(b'{"id": "42"}')
        return Reply(json.dumps([{"name": "nw"}]).encode())

    assert register("t", "777", definition(["shoplite"]), opener) == ["nw"]
    assert sent[0].full_url == "https://discord.com/api/v10/applications/@me"
    assert sent[1].full_url == "https://discord.com/api/v10/applications/42/guilds/777/commands"
    assert sent[1].get_method() == "PUT"
    assert sent[1].get_header("Authorization") == "Bot t"
    with pytest.raises(ValueError, match="server id"):
        register("t", "../x", [], opener)
