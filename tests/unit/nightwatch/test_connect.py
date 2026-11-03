"""Connected accounts (spec section 13): the template's promises, the role switch, read-only."""

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from nightwatch.agent import accounts
from nightwatch.agent.handler import propose_fix
from nightwatch.agent.models import Diagnosis, Hypothesis, Investigation
from nightwatch.agent.services import Account, service_info
from nightwatch.agent.tools import AwsClients, ToolContext
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.remediation.actions import ActionStore
from scripts.connect import new_id
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert

TEMPLATE = Path(__file__).resolve().parents[3] / "connect" / "nightwatch-connect.yaml"
ROLE = "arn:aws:iam::123456789012:role/NightWatchReadOnly-us-east-2"
ACCOUNT = Account(role_arn=ROLE, external_id_param="/nightwatch/tenants/demo/external_id")
READ_VERBS = re.compile(r"^(Get|Describe|List|LookupEvents|StartQuery|StopQuery)")


class TemplateLoader(yaml.SafeLoader):
    """CloudFormation's short tags (!Sub, !Ref, !GetAtt) as plain {tag: value} maps."""


def _short_tag(tag: str) -> Any:
    def construct(loader: yaml.SafeLoader, node: Any) -> dict[str, Any]:
        return {tag: loader.construct_scalar(node)}

    return construct


for tag in ("!Sub", "!Ref", "!GetAtt"):
    TemplateLoader.add_constructor(tag, _short_tag(tag))


def test_the_template_only_reads_and_only_with_the_external_id() -> None:
    template = yaml.load(TEMPLATE.read_text(encoding="utf-8"), Loader=TemplateLoader)  # noqa: S506
    role = template["Resources"]["ReadOnlyRole"]["Properties"]
    (trust,) = role["AssumeRolePolicyDocument"]["Statement"]
    assert trust["Principal"]["AWS"] == {"!Sub": "arn:aws:iam::${NightWatchAccountId}:root"}
    assert trust["Condition"] == {"StringEquals": {"sts:ExternalId": {"!Ref": "ExternalId"}}}
    assert role["RoleName"]["!Sub"].startswith("NightWatchReadOnly-")  # what NightWatch may assume

    statements = [s for p in role["Policies"] for s in p["PolicyDocument"]["Statement"]]
    actions = [a for s in statements for a in s["Action"]]
    assert actions
    assert all(READ_VERBS.match(action.split(":")[1]) for action in actions), actions
    assert not any(a.startswith(("s3:", "secretsmanager:", "kms:")) for a in actions)
    assert "dynamodb:GetItem" not in actions  # settings, never the data in tables
    (parameters,) = [s for s in statements if any(a.startswith("ssm:") for a in s["Action"])]
    assert parameters["Condition"] == {
        "StringEquals": {"ssm:resourceTag/nightwatch:readable": "true"}
    }


def test_a_connected_account_must_name_its_read_only_role() -> None:
    with pytest.raises(ValidationError):
        Account(role_arn="arn:aws:iam::123456789012:role/Admin", external_id_param="/x")
    assert ACCOUNT.region == "us-east-2"


class Sts:
    def __init__(self, expires: datetime) -> None:
        self.calls: list[dict[str, Any]] = []
        self.expires = expires

    def assume_role(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        keys = {"AccessKeyId": "id", "SecretAccessKey": "s", "SessionToken": "t"}
        return {"Credentials": {**keys, "Expiration": self.expires}}


class Ssm:
    def get_parameter(self, **kwargs: Any) -> dict[str, Any]:
        assert kwargs["Name"] == "/nightwatch/tenants/demo/external_id"
        return {"Parameter": {"Value": "e" * 32}}


def test_the_role_is_assumed_with_the_external_id_and_kept_until_it_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(accounts, "_sessions", {})
    expires = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
    sts, clock = Sts(expires), [expires.timestamp() - 3600]
    first = accounts.session_for(ACCOUNT, sts=sts, ssm=Ssm(), clock=lambda: clock[0])
    again = accounts.session_for(ACCOUNT, sts=sts, ssm=Ssm(), clock=lambda: clock[0])
    assert first is again
    assert sts.calls == [
        {
            "RoleArn": ROLE,
            "RoleSessionName": "nightwatch",
            "ExternalId": "e" * 32,
            "DurationSeconds": 3600,
        }
    ]
    clock[0] = expires.timestamp() - 200  # under five minutes left: renew
    accounts.session_for(ACCOUNT, sts=sts, ssm=Ssm(), clock=lambda: clock[0])
    assert len(sts.calls) == 2


def test_a_connected_service_reads_its_own_account(monkeypatch: pytest.MonkeyPatch) -> None:
    class Session:
        def client(self, name: str) -> str:
            return f"connected {name}"

    monkeypatch.setattr(accounts, "session_for", lambda account: Session())
    connected = service_info("shoplite").model_copy(update={"account": ACCOUNT})
    assert accounts.clients_for(connected).logs == "connected logs"  # type: ignore[comparison-overlap]
    assert isinstance(accounts.clients_for(service_info("shoplite")), AwsClients)


def test_tenants_can_bring_their_own_llm_key() -> None:
    settings = Settings(table_name=TABLE)
    own = ACCOUNT.model_copy(update={"llm_key_param": "/nightwatch/llm/tenants/demo/groq"})
    tenant = service_info("shoplite").model_copy(update={"account": own})
    assert accounts.llm_keys(settings, tenant)["groq"] == "/nightwatch/llm/tenants/demo/groq"
    assert accounts.llm_keys(settings, service_info("shoplite"))["groq"] == settings.groq_key_param


def test_no_fix_is_proposed_in_a_connected_account(store: IncidentStore) -> None:
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    service = service_info("shoplite").model_copy(update={"account": ACCOUNT})
    ctx = ToolContext(incident=incident, service=service, now=T0, store=store)
    hypothesis = Hypothesis(statement="x", confidence=0.9, evidence_ids=["E1"])
    result = Investigation(
        diagnosis=Diagnosis(summary="x", hypotheses=[hypothesis]), method="rules"
    )
    audit = AuditLog(TABLE, "default", "test")
    assert propose_fix(ctx, [], result, ActionStore(TABLE, "default"), audit) is None
    events = [e["event"] for e in audit.for_incident(incident.id)]
    assert "remediation_skipped" in events


def test_a_new_external_id_is_unguessable_and_never_replaces_one() -> None:
    stored: list[dict[str, Any]] = []

    class Params:
        def put_parameter(self, **kwargs: Any) -> None:
            stored.append(kwargs)

    value = new_id("demo", Params())
    assert len(value) >= 32
    assert stored[0]["Name"] == "/nightwatch/tenants/demo/external_id"
    assert (stored[0]["Value"], stored[0]["Overwrite"]) == (value, False)
