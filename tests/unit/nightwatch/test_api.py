"""The dashboard API: only Cognito access tokens for the dashboard get in, and it only reads."""

import json
import time
from datetime import timedelta
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from nightwatch.api import handler as api
from nightwatch.api.auth import AuthError, User, Verifier
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert

ISSUER = "https://cognito-idp.us-east-2.amazonaws.com/us-east-2_pool"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class Keys:
    """Stands in for the pool's published keys (PyJWKClient)."""

    def get_signing_key_from_jwt(self, token: str) -> Any:
        return type("SigningKey", (), {"key": KEY.public_key()})()


def token(key: Any = KEY, **claims: Any) -> str:
    now = int(time.time())
    body = {
        "iss": ISSUER,
        "sub": "user-1",
        "username": "raagul",
        "token_use": "access",
        "client_id": "dashboard-client",
        "iat": now,
        "exp": now + 3600,
        **claims,
    }
    return jwt.encode(body, key, algorithm="RS256")


def verifier() -> Verifier:
    return Verifier("us-east-2", "us-east-2_pool", "dashboard-client", keys=Keys())


def test_a_dashboard_access_token_is_accepted() -> None:
    assert verifier().verify(token()) == User(username="raagul", groups=())


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "not-a-jwt",
        token(key=OTHER_KEY),  # signed by someone else
        token(exp=int(time.time()) - 3600),  # expired
        token(token_use="id"),  # an ID token
        token(client_id="another-app"),
        token(iss="https://cognito-idp.us-east-2.amazonaws.com/other_pool"),
        jwt.encode({"iss": ISSUER, "token_use": "access"}, key="", algorithm="none"),
    ],
)
def test_anything_else_is_refused(bad: str) -> None:
    with pytest.raises(AuthError):
        verifier().verify(bad)


@pytest.fixture
def env(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> IncidentStore:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("POSTMORTEM_BUCKET", "pm")
    monkeypatch.setattr(api, "_verifier", lambda *args: verifier())

    class S3:
        def get_object(self, **kwargs: Any) -> dict[str, Any]:
            assert kwargs == {"Bucket": "pm", "Key": "postmortems/default/INC-001.md"}
            return {"Body": type("B", (), {"read": lambda self: b"# INC-001"})()}

    class Ssm:
        def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
            return {"Parameter": {"Value": "false"}}

    monkeypatch.setattr(api, "_clients", lambda: (S3(), Ssm()))
    return store


def call(path: str, auth: str | None = None, method: str = "GET", **query: str) -> Any:
    event = {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "headers": {"X-NightWatch-Token": auth if auth is not None else token()},
        "queryStringParameters": query or None,
    }
    reply = api.handler(event, None)
    return reply["statusCode"], json.loads(reply["body"])


def test_routes_need_a_token_and_only_read(env: IncidentStore) -> None:
    assert call("/api/incidents", auth="")[0] == 401
    assert call("/api/incidents", auth=token(client_id="x"))[0] == 401
    assert call("/api/incidents", method="POST")[0] == 405
    assert call("/api/nothing")[0] == 404
    assert call("/api/incidents/../../etc")[0] == 404
    assert call("/api/me")[1] == {"username": "raagul", "groups": []}


def test_incidents_list_and_detail(env: IncidentStore) -> None:
    incident = env.assign_number(env.open_incident(make_alert(), None, T0))
    env.set_postmortem(incident.id, "postmortems/default/INC-001.md")
    AuditLog(TABLE, "default", "test").record("incident_opened", incident.id, alarm="a")

    status, body = call("/api/incidents", status="open")
    assert status == 200
    (row,) = body["incidents"]
    assert (row["label"], row["postmortem"], row["service"]) == ("INC-001", True, "shoplite")
    assert call("/api/incidents", status="bogus")[0] == 400

    status, detail = call(f"/api/incidents/{incident.id}")
    assert status == 200
    assert detail["incident"]["id"] == incident.id
    assert detail["alerts"][0]["alarm_name"] == "shoplite-5xx-rate"
    assert [e["event"] for e in detail["timeline"]] == ["incident_opened"]
    assert detail["postmortem"] == "# INC-001"
    assert detail["evidence"] == []
    assert call("/api/incidents/01JB0000000000000000000000")[0] == 404


def test_runbooks_and_settings(env: IncidentStore) -> None:
    status, body = call("/api/runbooks")
    assert status == 200
    assert {book["id"] for book in body["runbooks"]} >= {"rollback-lambda-alias"}
    assert body["runbooks"][0]["params"]  # params use their YAML name, "from"
    status, settings = call("/api/settings")
    assert settings["kill_switch"] == "false"
    assert "shoplite" in settings["services"]


def test_the_latest_scheduled_reports(env: IncidentStore) -> None:
    assert call("/api/forecasts") == (200, {"report": None})  # before the first morning
    env.save_report("forecast", {"at": "first"}, T0, keep_days=90)
    env.save_report("forecast", {"at": "second"}, T0 + timedelta(days=1), keep_days=90)
    env.save_report("alarms", {"noisy": 1}, T0, keep_days=180)
    assert call("/api/forecasts")[1] == {"report": {"at": "second"}}
    assert call("/api/alarms")[1] == {"report": {"noisy": 1}}
