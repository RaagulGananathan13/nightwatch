"""What every chaos scenario needs: ShopLite's flags, signed traffic, and NightWatch's incidents."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import boto3

from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import utcnow
from shoplite.canary import signed_request

FLAGS_PARAM = "/shoplite/flags"
FLAP_WINDOW = timedelta(minutes=10)  # matches nightwatch-ingest's FLAP_WINDOW_MINUTES
FUNCTION = "shoplite-api"


@dataclass
class Lab:
    session: boto3.Session
    tenant: str = "default"
    table: str = "nightwatch"
    _url: str = field(default="", init=False)

    @classmethod
    def connect(cls, profile: str | None = "nightwatch", region: str = "us-east-2") -> Lab:
        return cls(boto3.Session(profile_name=profile, region_name=region))

    @property
    def region(self) -> str:
        return self.session.region_name or "us-east-2"

    # ShopLite ------------------------------------------------------------------

    def get_flags(self) -> dict[str, Any]:
        value = self.session.client("ssm").get_parameter(Name=FLAGS_PARAM)["Parameter"]
        flags: dict[str, Any] = json.loads(value.get("Value") or "{}")
        return flags

    def set_flags(self, flags: dict[str, Any]) -> None:
        self.session.client("ssm").put_parameter(
            Name=FLAGS_PARAM, Value=json.dumps(flags), Type="String", Overwrite=True
        )

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> int:
        """One signed request to ShopLite. Returns the HTTP status (0 if unreachable)."""
        if not self._url:
            config = self.session.client("lambda").get_function_url_config(
                FunctionName=FUNCTION, Qualifier="live"
            )
            self._url = config["FunctionUrl"].rstrip("/")
        credentials = self.session.get_credentials()
        if credentials is None:
            raise RuntimeError("no AWS credentials; is the nightwatch profile set up?")
        status, _ = signed_request(method, self._url + path, credentials, self.region, body)
        return status

    # NightWatch ------------------------------------------------------------------

    def incidents_since(self, service: str, since: datetime) -> list[Incident]:
        store = IncidentStore(self.table, self.tenant, self.session.client("dynamodb"))
        found = store.list_incidents("open") + store.list_incidents("resolved")
        return sorted(
            (i for i in found if i.service == service and i.opened_at >= since),
            key=lambda incident: incident.opened_at,
        )

    def seconds_until_new_incident(self, service: str) -> float:
        """How long until a new alarm would open a fresh incident instead of reopening one."""
        store = IncidentStore(self.table, self.tenant, self.session.client("dynamodb"))
        pointer = store.get_pointer(service)
        if pointer is None or pointer.status != "resolved" or pointer.resolved_at is None:
            return 0.0
        remaining = pointer.resolved_at + FLAP_WINDOW - utcnow()
        return max(0.0, remaining.total_seconds() + 30)  # a little margin for clock skew

    def audit(self, incident_id: str) -> list[dict[str, Any]]:
        log = AuditLog(self.table, self.tenant, "chaos", self.session.client("dynamodb"))
        return log.for_incident(incident_id)
