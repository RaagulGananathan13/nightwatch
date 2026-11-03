"""Tenant isolation: every key NightWatch writes starts with TENANT#<tenant> (spec section 13).

In AWS, each role's table access carries a dynamodb:LeadingKeys condition, so IAM refuses any
item outside the tenant's keys (infra/modules/nightwatch-core/tenancy.tf). This writes one of
every kind of item and checks its keys, so a new kind can't slip outside that fence unseen.
"""

from datetime import timedelta

from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.providers.chain import DailyBudget
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import ToolContext
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.memory.store import MemoryStore, remember
from nightwatch.remediation.actions import ActionStore
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_remediation import make_action


def test_every_key_nightwatch_writes_starts_with_the_tenant(store: IncidentStore) -> None:
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    store.attach(
        store.get_pointer("shoplite"),  # type: ignore[arg-type]
        incident,
        make_alert("shoplite-lambda-errors", at=T0 + timedelta(seconds=30)),
        T0 + timedelta(seconds=30),
    )
    store.set_investigation(incident.id, "done", {"diagnosis": {"summary": "x"}})
    store.acknowledge(incident.id, "1", "Raagul", T0)
    store.set_slack_message(incident.id, "1761818400.000100")
    store.save_report("forecast", {"forecasts": []}, T0, keep_days=90)
    draft = {"id": "01JB0000000000000000000DRF", "incident_id": incident.id, "service": "shoplite"}
    store.save_draft(draft, T0)
    store.add_status_update({"draft_id": draft["id"], "incident_id": incident.id}, T0)
    AuditLog(TABLE, "default", "test").record("checked", incident.id)
    action = ActionStore(TABLE, "default").create(make_action(incident_id=incident.id))
    ctx = ToolContext(incident=incident, service=service_info("shoplite"), now=T0, store=store)
    EvidenceLedger(store, incident.id).gather(ctx, "get_service_info", {"service": "shoplite"})
    MemoryStore(TABLE, "default").put(remember(incident, [action]))
    assert DailyBudget(TABLE, "default", 10).try_reserve("groq")

    items = store.client.scan(TableName=TABLE)["Items"]  # tests only: no role may Scan
    keys = [item["PK"]["S"] for item in items]
    keys += [item["GSI1PK"]["S"] for item in items if "GSI1PK" in item]
    kinds = {item["SK"]["S"].split("#")[0] for item in items}
    assert {"INC", "AUDIT", "REPORT", "DRAFT", "UPDATE", "ACT", "EV", "LLMUSE"} <= kinds
    outside = [
        key for key in keys if key != "TENANT#default" and not key.startswith("TENANT#default#")
    ]
    assert outside == []
