"""The MCP server (F15): the protocol over stdio, NightWatch's answers, and the read-only role."""

import io
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest

from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext, registry
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.mcp_server import __main__ as entry
from nightwatch.mcp_server.protocol import Server
from nightwatch.mcp_server.tools import call, definitions
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert


def exchange(server: Server, *messages: Any) -> list[dict[str, Any]]:
    lines = [m if isinstance(m, str) else json.dumps(m) for m in messages]
    stdout = io.StringIO()
    server.serve(io.StringIO("\n".join(lines) + "\n"), stdout)
    return [json.loads(line) for line in stdout.getvalue().splitlines()]


def test_the_protocol_over_stdio() -> None:
    def tool(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        if arguments.get("boom"):
            raise RuntimeError("bug")
        return json.dumps(arguments), False

    echo = {"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}
    server = Server({"name": "nightwatch", "version": "0"}, "Start here.", [echo], tool)
    hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}
    replies = exchange(
        server,
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "echo"}},
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"boom": True}},
        },
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "rm"}},
        {"jsonrpc": "2.0", "id": 6, "method": "resources/list"},
        "not JSON",
    )
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5, 6, None]  # notifications get nothing
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert replies[0]["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert replies[1]["result"] == {"tools": [echo]}
    assert replies[2]["result"] == {"content": [{"type": "text", "text": "{}"}], "isError": False}
    assert replies[3]["result"]["isError"]  # a failing tool is an answer, not a dead session
    assert [r["error"]["code"] for r in replies[4:]] == [-32602, -32601, -32700]
    future = {"jsonrpc": "2.0", "id": 7, "method": "initialize", "params": {"protocolVersion": "9"}}
    assert server.handle(future)["result"]["protocolVersion"] == "2025-11-25"


def test_every_tool_is_read_only_and_keeps_its_own_arguments() -> None:
    tools = {tool["name"]: tool for tool in definitions()}
    assert {"whats_going_on", "list_incidents", "get_incident", "get_evidence"} <= set(tools)
    assert set(registry()) <= set(tools)
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools.values())
    service = tools["get_service_info"]["inputSchema"]
    assert service["required"] == ["service"]  # its own argument, not the optional context one
    assert "incident" in tools["query_logs"]["inputSchema"]["properties"]


RESULT = {
    "method": "llm",
    "diagnosis": {
        "summary": "Errors began after version 5. Ask ops@example.com.",
        "hypotheses": [
            {
                "statement": "Version 5 raises KeyError.",
                "confidence": 0.9,
                "evidence_ids": ["E1"],
                "verified": True,
            },
            {"statement": "DynamoDB throttled.", "confidence": 0.3, "evidence_ids": []},
        ],
        "impact": "Listings fail.",
    },
}


@pytest.fixture
def env(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> IncidentStore:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    store.set_investigation(incident.id, "done", RESULT)
    ctx = ToolContext(incident=incident, service=service_info("shoplite"), now=T0, store=store)
    EvidenceLedger(store, incident.id).gather(ctx, "get_service_info", {"service": "shoplite"})
    forecast = {"name": "Lambda requests", "status": "warn", "sentence": "Near the limit."}
    store.save_report("forecast", {"at": "2026-10-30T07:00:00Z", "forecasts": [forecast]}, T0, 90)
    return store


def test_whats_going_on_answers_from_nightwatchs_records(env: IncidentStore) -> None:
    text, failed = call("whats_going_on", {}, "tester", T0 + timedelta(minutes=5))
    assert not failed
    assert "1 open incident(s):" in text
    assert "INC-001 shoplite" in text
    assert "verified cause: Version 5 raises KeyError. [E1]" in text
    assert "- warn: Lambda requests: Near the limit." in text


def test_incident_evidence_and_tools_are_redacted_and_audited(env: IncidentStore) -> None:
    now = T0 + timedelta(minutes=5)
    detail, failed = call("get_incident", {"incident": "inc-1"}, "tester", now)
    assert not failed
    assert "1. [verified, confidence 0.90] Version 5 raises KeyError. (evidence: E1)" in detail
    assert "2. [NOT verified, confidence 0.30] DynamoDB throttled. (evidence: none)" in detail
    assert "ops@example.com" not in detail
    assert "[email]" in detail
    assert "E1 get_service_info: Service shoplite" in detail

    item, _ = call("get_evidence", {"incident": "INC-001", "evidence_id": "e1"}, "tester", now)
    assert item.startswith('INC-001 E1: get_service_info({"service": "shoplite"})')
    assert "Data:" in item

    tool, failed = call("get_service_info", {"service": "shoplite"}, "tester", now)
    assert not failed
    assert tool.startswith("Service shoplite:")

    missing, failed = call("get_incident", {"incident": "INC-99"}, "tester", now)
    assert failed
    assert missing.startswith("Refused: no recent incident called INC-99")

    incident = env.find(1)
    assert incident is not None
    audited = [
        (e["event"], e["actor"]) for e in AuditLog(TABLE, "default", "t").for_incident(incident.id)
    ]
    assert audited.count(("mcp_tool_called", "mcp:tester")) == 2  # get_incident, get_evidence


def test_the_server_switches_to_the_read_only_role_and_renews_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    switched: list[dict[str, Any]] = []
    monkeypatch.setattr(boto3, "setup_default_session", lambda **kw: switched.append(kw))
    expires = datetime(2026, 10, 30, 13, 0, tzinfo=UTC)
    assumed: list[dict[str, Any]] = []

    class Sts:
        def get_caller_identity(self) -> dict[str, str]:
            return {
                "Arn": "arn:aws:iam::123456789012:user/nightwatch/nightwatch-deployer",
                "Account": "123456789012",
            }

        def assume_role(self, **kwargs: Any) -> dict[str, Any]:
            assumed.append(kwargs)
            keys = {"AccessKeyId": "id", "SecretAccessKey": "s", "SessionToken": "t"}
            return {"Credentials": {**keys, "Expiration": expires}}

    class Source:
        def client(self, name: str) -> Sts:
            assert name == "sts"
            return Sts()

    clock = [expires.timestamp() - 3600]
    role = entry.RoleSession(Source(), "us-east-2", clock=lambda: clock[0])
    assert role.role_arn == "arn:aws:iam::123456789012:role/nightwatch/nightwatch-mcp"
    role.refresh()
    role.refresh()  # still valid: no second call
    assert [a["RoleSessionName"] for a in assumed] == ["mcp-nightwatch-deployer"]
    assert switched[0]["region_name"] == "us-east-2"
    clock[0] = expires.timestamp() - 200  # under five minutes left
    role.refresh()
    assert len(assumed) == 2
