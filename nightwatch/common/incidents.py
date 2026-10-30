"""Incidents in DynamoDB: one table for everything (spec section 9, keys per ADR 0009).

| Item            | PK                     | SK                        |
|-----------------|------------------------|---------------------------|
| Incident        | TENANT#<t>             | INC#<ulid>                |
| Service pointer | TENANT#<t>             | OPEN#<service>            |
| Counter         | TENANT#<t>             | META                      |
| Alert           | TENANT#<t>#INC#<ulid>  | ALERT#<time>#<event id>   |

GSI1 lists incidents by status: GSI1PK = TENANT#<t>#STATUS#<status>, GSI1SK = <opened_at>#<ulid>.

The service pointer is what makes correlation safe (ADR 0010). It names the service's current
incident and carries a version number. Every change to that incident is one transaction that
also bumps the pointer's version, guarded by the version we read. If two Lambdas handle two
alarms at the same moment, one transaction wins and the other fails and retries, so the second
alarm joins the first incident instead of opening another.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Literal, cast

import boto3
from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import ClientError

from nightwatch.common.ids import new_ulid
from nightwatch.common.models import SEVERITY_RANK, Alert, Incident
from nightwatch.common.timeutil import from_iso, to_iso

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import TransactWriteItemTypeDef

ALERT_RETENTION = timedelta(days=90)
DRAFT_RETENTION = timedelta(days=30)  # drafted stakeholder updates (spec F13)
UPDATE_RETENTION = timedelta(days=365)  # published ones, shown on the status page

_serializer = TypeSerializer()
_deserializer = TypeDeserializer()


class ConflictError(Exception):
    """Someone changed this service's incident first. Read it again and retry."""


class DuplicateAlertError(Exception):
    """This alert is already stored; the sender delivered it twice."""


@dataclass(frozen=True)
class Pointer:
    """Which incident a service's new alerts belong to, and how fresh that is."""

    service: str
    incident_id: str
    status: Literal["open", "resolved"]
    version: int
    last_alert_at: datetime
    resolved_at: datetime | None = None


class IncidentStore:
    def __init__(
        self, table_name: str, tenant_id: str, client: DynamoDBClient | None = None
    ) -> None:
        self.table = table_name
        self.tenant = tenant_id
        self._client = client

    @property
    def client(self) -> DynamoDBClient:
        if self._client is None:
            self._client = boto3.client("dynamodb")
        return self._client

    # ------------------------------------------------------------------------ reads

    def get_pointer(self, service: str) -> Pointer | None:
        item = self._get(f"OPEN#{service}")
        if item is None:
            return None
        return Pointer(
            service=service,
            incident_id=item["incident_id"],
            status=item["status"],
            version=int(item["version"]),
            last_alert_at=from_iso(item["last_alert_at"]),
            resolved_at=from_iso(item["resolved_at"]) if item.get("resolved_at") else None,
        )

    def get_incident(self, incident_id: str) -> Incident | None:
        item = self._get(f"INC#{incident_id}")
        return _incident(item) if item else None

    def list_incidents(self, status: str, limit: int = 25) -> list[Incident]:
        """Newest first."""
        response = self.client.query(
            TableName=self.table,
            IndexName="GSI1",
            KeyConditionExpression="GSI1PK = :pk",
            ExpressionAttributeValues={":pk": {"S": self._status_key(status)}},
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_incident(_plain(item)) for item in response.get("Items", [])]

    def find(
        self, number: int, statuses: tuple[str, ...] = ("open", "resolved")
    ) -> Incident | None:
        """INC-<number> among the 50 newest incidents of each status."""
        for status in statuses:
            found = next(
                (i for i in self.list_incidents(status, limit=50) if i.number == number), None
            )
            if found:
                return found
        return None

    def alerts(self, incident_id: str) -> list[dict[str, Any]]:
        """Every alert on an incident, oldest first."""
        response = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :alert)",
            ExpressionAttributeValues={
                ":pk": {"S": self._alerts_pk(incident_id)},
                ":alert": {"S": "ALERT#"},
            },
        )
        return [_plain(item) for item in response.get("Items", [])]

    # ----------------------------------------------------------------------- writes

    def open_incident(self, alert: Alert, replaces: Pointer | None, now: datetime) -> Incident:
        """Open a new incident for ``alert``'s service and point the service at it."""
        incident = Incident(
            id=new_ulid(now),
            tenant_id=self.tenant,
            service=alert.service,
            title=_title(alert),
            first_alarm=alert.alarm_name,
            severity=alert.severity,
            opened_at=alert.occurred_at,
            updated_at=now,
            last_alert_at=alert.occurred_at,
            alarms={alert.alarm_name: "ALARM"},
        )
        pointer = {
            "PK": self._pk,
            "SK": f"OPEN#{alert.service}",
            "incident_id": incident.id,
            "status": "open",
            "version": replaces.version + 1 if replaces else 1,
            "last_alert_at": to_iso(alert.occurred_at),
        }
        guard: dict[str, Any] = (
            {"ConditionExpression": "attribute_not_exists(PK)"}
            if replaces is None
            else {
                "ConditionExpression": "#ver = :expected",
                "ExpressionAttributeNames": {"#ver": "version"},
                "ExpressionAttributeValues": {":expected": {"N": str(replaces.version)}},
            }
        )
        self._transact(
            [
                {"Put": {"TableName": self.table, "Item": _item(pointer), **guard}},
                {
                    "Put": {
                        "TableName": self.table,
                        "Item": self._incident_item(incident),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                },
                self._put_alert(incident.id, alert, now),
            ]
        )
        return incident

    def attach(self, pointer: Pointer, incident: Incident, alert: Alert, now: datetime) -> Incident:
        """Record ``alert`` on the incident.

        A firing alert on a recently resolved incident reopens it. The OK that leaves every
        alarm recovered resolves it. Both happen in the same transaction as the alert itself.
        """
        firing = alert.state == "ALARM"
        alarms = {**incident.alarms, alert.alarm_name: alert.state}
        reopen = firing and pointer.status == "resolved"
        resolve = not firing and all(state == "OK" for state in alarms.values())
        at, stamp = _s(to_iso(alert.occurred_at)), _s(to_iso(now))

        # The pointer: bump the version (this is the lock), track the latest firing alert.
        p_set, p_names = ["#ver = :next"], {"#ver": "version"}
        p_values = {":expected": _n(pointer.version), ":next": _n(pointer.version + 1)}
        if firing:
            p_set.append("last_alert_at = :at")
            p_values[":at"] = at
        if reopen or resolve:
            p_set.append("#st = :status")
            p_names["#st"] = "status"
            p_values[":status"] = _s("open" if reopen else "resolved")
        if resolve:
            p_set.append("resolved_at = :now")
            p_values[":now"] = stamp
        p_expr = "SET " + ", ".join(p_set) + (" REMOVE resolved_at" if reopen else "")

        # The incident: this alarm's latest state, counts, severity and status.
        i_set = ["alarms.#alarm = :state", "updated_at = :now"]
        i_names = {"#alarm": alert.alarm_name}
        i_values = {":state": _s(alert.state), ":now": stamp}
        i_add: list[str] = []
        if firing:
            i_set.append("last_alert_at = :at")
            i_values[":at"] = at
            i_add.append("alert_count :one")
            i_values[":one"] = _n(1)
            if SEVERITY_RANK[alert.severity] > SEVERITY_RANK[incident.severity]:
                i_set.append("#sev = :sev")
                i_names["#sev"] = "severity"
                i_values[":sev"] = _s(alert.severity)
        if reopen or resolve:
            new_status = "open" if reopen else "resolved"
            i_set += ["#st = :status", "GSI1PK = :gsi"]
            i_names["#st"] = "status"
            i_values[":status"] = _s(new_status)
            i_values[":gsi"] = _s(self._status_key(new_status))
        if reopen:
            i_add.append("reopen_count :one")
        if resolve:
            i_set.append("resolved_at = :now")
        i_expr = "SET " + ", ".join(i_set)
        if i_add:
            i_expr += " ADD " + ", ".join(i_add)
        if reopen:
            i_expr += " REMOVE resolved_at"

        self._transact(
            [
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": self._key(f"OPEN#{pointer.service}"),
                        "UpdateExpression": p_expr,
                        "ConditionExpression": "#ver = :expected",
                        "ExpressionAttributeNames": p_names,
                        "ExpressionAttributeValues": p_values,
                    }
                },
                {
                    "Update": {
                        "TableName": self.table,
                        "Key": self._key(f"INC#{incident.id}"),
                        "UpdateExpression": i_expr,
                        "ConditionExpression": "attribute_exists(PK)",
                        "ExpressionAttributeNames": i_names,
                        "ExpressionAttributeValues": i_values,
                    }
                },
                self._put_alert(incident.id, alert, now),
            ]
        )
        updated = self.get_incident(incident.id)
        if updated is None:  # pragma: no cover - the transaction just checked it exists
            raise ConflictError(f"incident {incident.id} disappeared")
        return updated

    def assign_number(self, incident: Incident) -> Incident:
        """Give a new incident its INC-### number. Safe to call again; it only numbers once."""
        if incident.number is not None:
            return incident
        counter = self.client.update_item(
            TableName=self.table,
            Key=self._key("META"),
            UpdateExpression="ADD incident_seq :one",
            ExpressionAttributeValues={":one": _n(1)},
            ReturnValues="UPDATED_NEW",
        )
        number = int(counter["Attributes"]["incident_seq"]["N"])
        try:
            self.client.update_item(
                TableName=self.table,
                Key=self._key(f"INC#{incident.id}"),
                UpdateExpression="SET #num = :n",
                ConditionExpression="attribute_exists(PK) AND attribute_not_exists(#num)",
                ExpressionAttributeNames={"#num": "number"},
                ExpressionAttributeValues={":n": _n(number)},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            return self.get_incident(incident.id) or incident  # numbered by a retry already
        return incident.model_copy(update={"number": number})

    def set_discord_message(self, incident_id: str, message_id: str) -> bool:
        """Remember the incident's chat message. Returns False if one was already saved."""
        try:
            self.client.update_item(
                TableName=self.table,
                Key=self._key(f"INC#{incident_id}"),
                UpdateExpression="SET discord_message_id = :m",
                ConditionExpression="attribute_exists(PK) AND attribute_not_exists(discord_message_id)",
                ExpressionAttributeValues={":m": _s(message_id)},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def set_investigation(
        self, incident_id: str, status: str, result: dict[str, Any] | None = None
    ) -> None:
        """Record an investigation's progress and, when done, its result (as JSON text)."""
        expression, values = "SET investigation = :s", {":s": _s(status)}
        if result is not None:
            expression += ", investigation_result = :r"
            values[":r"] = _s(json.dumps(result, default=str))
        self.client.update_item(
            TableName=self.table,
            Key=self._key(f"INC#{incident_id}"),
            UpdateExpression=expression,
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues=values,
        )

    def set_postmortem(self, incident_id: str, key: str) -> None:
        self.client.update_item(
            TableName=self.table,
            Key=self._key(f"INC#{incident_id}"),
            UpdateExpression="SET postmortem_key = :k",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":k": _s(key)},
        )

    def set_fix_pr(self, incident_id: str, url: str) -> None:
        self.client.update_item(
            TableName=self.table,
            Key=self._key(f"INC#{incident_id}"),
            UpdateExpression="SET fix_pr_url = :u",
            ConditionExpression="attribute_exists(PK)",
            ExpressionAttributeValues={":u": _s(url)},
        )

    def acknowledge(self, incident_id: str, user_id: str, user_name: str, now: datetime) -> bool:
        """Record who took an open incident. False if it isn't open or someone already has."""
        try:
            self.client.update_item(
                TableName=self.table,
                Key=self._key(f"INC#{incident_id}"),
                UpdateExpression="SET acked_by = :u, acked_by_name = :n, acked_at = :t",
                ConditionExpression=(
                    "attribute_exists(PK) AND #st = :open AND attribute_not_exists(acked_by)"
                ),
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":u": _s(user_id),
                    ":n": _s(user_name),
                    ":t": _s(to_iso(now)),
                    ":open": _s("open"),
                },
            )
        except ClientError as exc:
            if _code(exc) == "ConditionalCheckFailedException":
                return False
            raise
        return True

    # ---------------------------------------------------------------------- reports

    def save_report(self, kind: str, report: dict[str, Any], now: datetime, keep_days: int) -> None:
        """Store a scheduled report ("alarms" from the coach, "forecast") for the dashboard."""
        self.client.put_item(
            TableName=self.table,
            Item={
                **self._key(f"REPORT#{kind}#{to_iso(now)}"),
                "type": _s(f"{kind}_report"),
                "json": _s(json.dumps(report)),
                "expires_at": _n(int((now + timedelta(days=keep_days)).timestamp())),
            },
        )

    def latest_report(self, kind: str) -> dict[str, Any] | None:
        """The newest report of a kind, or None before the first one is written."""
        reply = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :report)",
            ExpressionAttributeValues={":pk": _s(self._pk), ":report": _s(f"REPORT#{kind}#")},
            ScanIndexForward=False,
            Limit=1,
        )
        items = reply.get("Items", [])
        report: dict[str, Any] | None = json.loads(items[0]["json"]["S"]) if items else None
        return report

    # ---------------------------------------------------------------------- status updates

    def save_draft(self, draft: dict[str, Any], now: datetime) -> None:
        """Keep drafted stakeholder updates until an approver publishes or discards them."""
        expires = int((now + DRAFT_RETENTION).timestamp())
        self.client.put_item(
            TableName=self.table,
            Item={
                **self._key(f"DRAFT#{draft['id']}"),
                **_item({**draft, "status": "draft", "expires_at": expires}),
            },
        )

    def get_draft(self, draft_id: str) -> dict[str, Any] | None:
        return self._get(f"DRAFT#{draft_id}")

    def decide_draft(self, draft_id: str, status: str, user_name: str, now: datetime) -> bool:
        """Publish or discard drafts, once. False if someone already decided them."""
        try:
            self.client.update_item(
                TableName=self.table,
                Key=self._key(f"DRAFT#{draft_id}"),
                UpdateExpression="SET #st = :status, decided_by_name = :n, decided_at = :t",
                ConditionExpression="#st = :draft",
                ExpressionAttributeNames={"#st": "status"},
                ExpressionAttributeValues={
                    ":status": _s(status),
                    ":n": _s(user_name),
                    ":t": _s(to_iso(now)),
                    ":draft": _s("draft"),
                },
            )
        except ClientError as exc:
            if _code(exc) == "ConditionalCheckFailedException":
                return False
            raise
        return True

    def add_status_update(self, update: dict[str, Any], now: datetime) -> None:
        """A published customer update, for the public status page."""
        expires = int((now + UPDATE_RETENTION).timestamp())
        self.client.put_item(
            TableName=self.table,
            Item={
                **self._key(f"UPDATE#{to_iso(now)}#{update['draft_id']}"),
                **_item({**update, "published_at": to_iso(now), "expires_at": expires}),
            },
        )

    def status_updates(self, limit: int) -> list[dict[str, Any]]:
        """Published customer updates, newest first."""
        reply = self.client.query(
            TableName=self.table,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :update)",
            ExpressionAttributeValues={":pk": _s(self._pk), ":update": _s("UPDATE#")},
            ScanIndexForward=False,
            Limit=limit,
        )
        return [_plain(item) for item in reply.get("Items", [])]

    # ---------------------------------------------------------------------- helpers

    @property
    def _pk(self) -> str:
        return f"TENANT#{self.tenant}"

    def _key(self, sort_key: str) -> dict[str, Any]:
        return {"PK": _s(self._pk), "SK": _s(sort_key)}

    def _status_key(self, status: str) -> str:
        return f"{self._pk}#STATUS#{status}"

    def _alerts_pk(self, incident_id: str) -> str:
        return f"{self._pk}#INC#{incident_id}"

    def _get(self, sort_key: str) -> dict[str, Any] | None:
        item = self.client.get_item(
            TableName=self.table, Key=self._key(sort_key), ConsistentRead=True
        ).get("Item")
        return _plain(item) if item else None

    def _incident_item(self, incident: Incident) -> dict[str, Any]:
        fields = {
            name: to_iso(value) if isinstance(value, datetime) else value
            for name, value in incident.model_dump(exclude_none=True).items()
        }
        if "investigation_result" in fields:
            fields["investigation_result"] = json.dumps(fields["investigation_result"])
        return _item(
            {
                "PK": self._pk,
                "SK": f"INC#{incident.id}",
                "type": "incident",
                "GSI1PK": self._status_key(incident.status),
                "GSI1SK": f"{to_iso(incident.opened_at)}#{incident.id}",
                **fields,
            }
        )

    def _put_alert(self, incident_id: str, alert: Alert, now: datetime) -> dict[str, Any]:
        item = {
            "PK": self._alerts_pk(incident_id),
            "SK": f"ALERT#{to_iso(alert.occurred_at)}#{alert.id}",
            "type": "alert",
            "alert_id": alert.id,
            "source": alert.source,
            "alarm_name": alert.alarm_name,
            "alarm_arn": alert.alarm_arn,
            "state": alert.state,
            "previous_state": alert.previous_state,
            "severity": alert.severity,
            "description": alert.description,
            "reason": alert.reason,
            "fingerprint": alert.fingerprint,
            "occurred_at": to_iso(alert.occurred_at),
            "received_at": to_iso(now),
            "metrics": [metric.model_dump(exclude_none=True) for metric in alert.metrics],
            "raw": alert.raw,
            "expires_at": int((now + ALERT_RETENTION).timestamp()),  # DynamoDB TTL
        }
        return {
            "Put": {
                "TableName": self.table,
                "Item": _item(item),
                "ConditionExpression": "attribute_not_exists(PK)",
            }
        }

    def _transact(self, items: list[dict[str, Any]]) -> None:
        """Run a transaction whose last item is always the alert put."""
        try:
            self.client.transact_write_items(
                TransactItems=cast("Sequence[TransactWriteItemTypeDef]", items)
            )
        except ClientError as exc:
            if _code(exc) != "TransactionCanceledException":
                raise
            reasons = [
                reason.get("Code", "None") for reason in exc.response.get("CancellationReasons", [])
            ]
            if reasons and reasons[-1] == "ConditionalCheckFailed":
                raise DuplicateAlertError("alert already recorded") from exc
            if {"ConditionalCheckFailed", "TransactionConflict"} & set(reasons):
                raise ConflictError("incident changed while we were writing") from exc
            raise


def _title(alert: Alert) -> str:
    text = f"{alert.alarm_name}: {alert.description}" if alert.description else alert.alarm_name
    return text[:200]


def _s(value: str) -> dict[str, str]:
    return {"S": value}


def _n(value: int) -> dict[str, str]:
    return {"N": str(value)}


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _item(values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _serializer.serialize(value) for key, value in values.items() if value is not None}


def _plain(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _python(_deserializer.deserialize(value)) for key, value in item.items()}


def _python(value: Any) -> Any:
    """DynamoDB numbers come back as Decimal; turn them (and nested ones) into int or float."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {key: _python(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_python(inner) for inner in value]
    return value


def _incident(item: Mapping[str, Any]) -> Incident:
    return Incident.model_validate({k: v for k, v in item.items() if k in Incident.model_fields})
