"""Discord webhook client and the incident card."""

import io
import json
import urllib.error
from datetime import timedelta
from typing import Any

import pytest

from nightwatch.chat.cards import duration, incident_message
from nightwatch.chat.discord import USER_AGENT, DiscordError, DiscordWebhook
from nightwatch.common.models import Incident
from tests.unit.nightwatch.conftest import T0

URL = "https://discord.com/api/webhooks/123456/secret-token"


class FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = json.dumps(body).encode()

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def http_error(code: int, body: dict[str, Any] | None = None) -> urllib.error.HTTPError:
    payload = io.BytesIO(json.dumps(body or {}).encode())
    return urllib.error.HTTPError(URL, code, "error", {}, payload)  # type: ignore[arg-type]


class Opener:
    def __init__(self, *results: Any) -> None:
        self.results = list(results)
        self.requests: list[Any] = []

    def __call__(self, request: Any, timeout: float) -> Any:
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_send_posts_with_wait_and_returns_the_message_id() -> None:
    opener = Opener(FakeResponse({"id": "987"}))
    assert DiscordWebhook(URL, opener=opener).send({"content": "hi @everyone"}) == "987"
    request = opener.requests[0]
    assert request.full_url == URL + "?wait=true"
    assert request.get_method() == "POST"
    assert request.get_header("User-agent") == USER_AGENT
    body = json.loads(request.data)
    assert body["allowed_mentions"] == {"parse": []}  # alarm text can never ping anyone


def test_edit_patches_the_message() -> None:
    opener = Opener(FakeResponse({}))
    DiscordWebhook(URL, opener=opener).edit("987", {"content": "update"})
    assert opener.requests[0].full_url == URL + "/messages/987"
    assert opener.requests[0].get_method() == "PATCH"


def test_rate_limits_are_waited_out() -> None:
    waits: list[float] = []
    opener = Opener(http_error(429, {"retry_after": 0.7}), FakeResponse({"id": "1"}))
    assert DiscordWebhook(URL, opener=opener, sleep=waits.append).send({}) == "1"
    assert waits == [0.7]


def test_errors_never_leak_the_webhook_url() -> None:
    hook = DiscordWebhook(URL, opener=Opener(http_error(404)), sleep=lambda s: None)
    with pytest.raises(DiscordError) as caught:
        hook.send({})
    assert "secret-token" not in str(caught.value)
    assert caught.value.__cause__ is None


def test_only_discord_webhook_urls_are_accepted() -> None:
    with pytest.raises(ValueError, match="webhook"):
        DiscordWebhook("https://evil.example.com/api/webhooks/1/x")
    with pytest.raises(ValueError, match="numbers"):
        DiscordWebhook(URL, opener=Opener()).edit("../x", {})


def incident(**changes: Any) -> Incident:
    base = {
        "id": "01JBEXAMPLE0000000000000000",
        "tenant_id": "default",
        "number": 7,
        "service": "shoplite",
        "title": "shoplite-5xx-rate: More than 5% of requests failed",
        "first_alarm": "shoplite-5xx-rate",
        "opened_at": T0,
        "updated_at": T0,
        "last_alert_at": T0,
        "alarms": {"shoplite-5xx-rate": "ALARM"},
    }
    return Incident.model_validate({**base, **changes})


def test_card_for_a_new_incident() -> None:
    message = incident_message(incident(), T0)
    assert message["content"].startswith("🔴 **INC-007 opened** for **shoplite**")
    embed = message["embeds"][0]
    assert embed["title"] == "INC-007 · shoplite"
    assert any(field["name"] == "Alarms" for field in embed["fields"])


def test_card_shows_grouping_then_resolution() -> None:
    grouped = incident(
        alert_count=3, alarms={"shoplite-5xx-rate": "ALARM", "shoplite-lambda-errors": "ALARM"}
    )
    assert "3 alerts grouped into 1 incident" in incident_message(grouped, T0)["content"]
    done = incident(
        status="resolved",
        resolved_at=T0 + timedelta(minutes=6, seconds=20),
        alarms={"shoplite-5xx-rate": "OK"},
    )
    assert "resolved** for **shoplite** after 6m 20s" in incident_message(done, T0)["content"]


def test_card_text_from_alarms_cannot_break_formatting() -> None:
    message = incident_message(incident(first_alarm="x`**y", title="a\n" * 500), T0)
    assert "`x'y`" in message["content"]
    assert len(message["embeds"][0]["description"]) <= 400


@pytest.mark.parametrize(
    ("seconds", "text"), [(45, "45s"), (380, "6m 20s"), (3900, "1h 5m"), (-5, "0s")]
)
def test_duration(seconds: int, text: str) -> None:
    assert duration(timedelta(seconds=seconds)) == text
