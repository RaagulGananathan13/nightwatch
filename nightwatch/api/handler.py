"""Lambda entry point for the dashboard's read-only API (spec F18).

    GET /api/incidents?status=open|resolved   newest first
    GET /api/incidents/<id>                   the incident with its alerts, evidence, timeline,
                                              fixes and postmortem
    GET /api/runbooks
    GET /api/alarms                           the latest alert-quality report (spec F11)
    GET /api/forecasts                        the latest forecasts (spec F12)
    GET /api/settings                         kill switch, services, LLM settings
    GET /api/me

Every route needs a signed-in user (auth.py). Nothing here writes: approvals stay in Discord,
where every click is signed and checked against the service's approvers.
"""

from __future__ import annotations

import json
import re
from functools import cache
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.models import Evidence
from nightwatch.agent.services import load_services
from nightwatch.api.auth import AuthError, User, Verifier
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.common.secrets import SecretCache
from nightwatch.remediation.actions import ActionStore
from nightwatch.remediation.runbooks import load_runbooks

_ULID = re.compile(r"^[0-9A-Z]{26}$")
_secrets = SecretCache()


@cache
def _verifier(region: str, pool_id: str, client_param: str) -> Verifier:
    return Verifier(region, pool_id, _secrets.get(client_param))


@cache
def _clients() -> tuple[Any, Any]:
    return boto3.client("s3"), boto3.client("ssm")


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    http = (event.get("requestContext") or {}).get("http") or {}
    if http.get("method", "GET") != "GET":
        return reply(405, {"error": "the dashboard API is read-only"})
    headers = {key.lower(): value for key, value in (event.get("headers") or {}).items()}
    try:
        verifier = _verifier(
            settings.aws_region, settings.dashboard_pool_id, settings.dashboard_client_param
        )
        user = verifier.verify(headers.get("x-nightwatch-token", ""))
    except AuthError as exc:
        return reply(401, {"error": str(exc)})
    return route(settings, user, str(event.get("rawPath") or "/"), event)


def route(settings: Settings, user: User, path: str, event: dict[str, Any]) -> dict[str, Any]:
    store = IncidentStore(settings.table_name, settings.tenant_id)
    parts = [part for part in path.split("/") if part]
    if parts[:1] != ["api"]:
        return reply(404, {"error": "not found"})
    if parts[1:] == ["me"]:
        return reply(200, {"username": user.username, "groups": list(user.groups)})
    if parts[1:] == ["incidents"]:
        status = ((event.get("queryStringParameters") or {}).get("status")) or "open"
        if status not in ("open", "resolved"):
            return reply(400, {"error": "status is open or resolved"})
        incidents = store.list_incidents(status, limit=100)
        return reply(200, {"incidents": [summary(i) for i in incidents]})
    if len(parts) == 3 and parts[1] == "incidents" and _ULID.match(parts[2]):
        return incident_detail(settings, store, parts[2])
    if parts[1:] == ["runbooks"]:
        books = load_runbooks()
        return reply(200, {"runbooks": [b.model_dump(by_alias=True) for b in books.values()]})
    if parts[1:] == ["settings"]:
        return reply(200, settings_view(settings))
    if parts[1:] == ["alarms"]:
        return reply(200, {"report": store.latest_report("alarms")})
    if parts[1:] == ["forecasts"]:
        return reply(200, {"report": store.latest_report("forecast")})
    return reply(404, {"error": "not found"})


def summary(incident: Incident) -> dict[str, Any]:
    result = incident.investigation_result or {}
    hypotheses = (result.get("diagnosis") or {}).get("hypotheses") or []
    top = hypotheses[0] if hypotheses else {}
    return {
        "id": incident.id,
        "label": incident.label,
        "service": incident.service,
        "title": incident.title,
        "status": incident.status,
        "severity": incident.severity,
        "opened_at": incident.opened_at.isoformat(),
        "resolved_at": incident.resolved_at.isoformat() if incident.resolved_at else None,
        "alert_count": incident.alert_count,
        "alarms": incident.alarms,
        "investigation": incident.investigation,
        "cause": top.get("statement"),
        "confidence": top.get("confidence"),
        "verified": top.get("verified"),
        "similar": [s.get("label") for s in result.get("similar") or []],
        "postmortem": bool(incident.postmortem_key),
        "acked_by": incident.acked_by_name,
    }


def incident_detail(settings: Settings, store: IncidentStore, incident_id: str) -> dict[str, Any]:
    incident = store.get_incident(incident_id)
    if incident is None:
        return reply(404, {"error": "no such incident"})
    alerts = [
        {k: alert.get(k) for k in ("alarm_name", "state", "severity", "occurred_at", "reason")}
        for alert in store.alerts(incident_id)
    ]
    evidence_items = store.client.query(
        TableName=store.table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :ev)",
        ExpressionAttributeValues={
            ":pk": {"S": f"TENANT#{store.tenant}#INC#{incident_id}"},
            ":ev": {"S": "EV#"},
        },
    ).get("Items", [])
    evidence = [
        Evidence.model_validate_json(item["json"]["S"]).model_dump(mode="json")
        for item in evidence_items
    ]
    timeline = AuditLog(settings.table_name, settings.tenant_id, "api").for_incident(incident_id)
    actions = ActionStore(settings.table_name, settings.tenant_id).for_incident(incident_id)
    return reply(
        200,
        {
            "incident": incident.model_dump(mode="json"),
            "alerts": alerts,
            "evidence": evidence,
            "timeline": timeline,
            "actions": [action.model_dump(mode="json") for action in actions],
            "postmortem": _postmortem(settings, incident),
        },
    )


def settings_view(settings: Settings) -> dict[str, Any]:
    _, ssm = _clients()
    try:
        switch = ssm.get_parameter(Name=settings.kill_switch_param)["Parameter"]["Value"]
    except (BotoCoreError, ClientError):
        switch = "unknown"
    return {
        "kill_switch": switch,
        "llm_providers": settings.llm_providers,
        "llm_daily_requests": settings.llm_daily_requests,
        "approval_timeout_minutes": settings.approval_timeout_minutes,
        "services": {name: info.model_dump() for name, info in load_services().items()},
    }


def _postmortem(settings: Settings, incident: Incident) -> str | None:
    if not incident.postmortem_key or not settings.postmortem_bucket:
        return None
    s3, _ = _clients()
    try:
        body = s3.get_object(Bucket=settings.postmortem_bucket, Key=incident.postmortem_key)
    except (BotoCoreError, ClientError):
        return None
    text: str = body["Body"].read().decode("utf-8")
    return text


def reply(status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Cache-Control": "no-store"},
        "body": json.dumps(body, default=str),
    }
