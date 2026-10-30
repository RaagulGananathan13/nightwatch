"""Stakeholder updates (F13): drafting, keeping internals off the public page, publishing once."""

import base64
import hashlib
import json
from datetime import timedelta
from typing import Any

import pytest

from nightwatch.agent.services import service_info
from nightwatch.chat import interactions
from nightwatch.chat.discord import InteractionReply
from nightwatch.common.audit import AuditLog
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import to_iso
from nightwatch.status import handler as updates_lambda
from nightwatch.status import page
from nightwatch.status.drafts import internal_terms, write
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_approvals import APPROVER, PUBLIC
from tests.unit.nightwatch.test_commands import TOKEN, call, slash

RESULT = {
    "diagnosis": {
        "summary": "Errors began right after version 5 went live.",
        "hypotheses": [
            {
                "statement": "The deploy of shoplite-api version 5 raises KeyError.",
                "confidence": 0.9,
                "evidence_ids": ["E4"],
                "verified": True,
            }
        ],
        "impact": "Product listings and new orders fail.",
        "suggested_actions": [{"description": "Move the live alias back to version 4."}],
        "unknowns": ["Whether any orders were lost."],
    }
}


@pytest.fixture
def incident(store: IncidentStore) -> Incident:
    opened = store.assign_number(store.open_incident(make_alert(), None, T0))
    store.set_investigation(opened.id, "done", RESULT)
    found = store.get_incident(opened.id)
    assert found is not None
    return found


def test_rules_draft_both_updates_when_no_model_answers(incident: Incident) -> None:
    service = service_info("shoplite")
    drafts = write(incident, service, None)
    assert (drafts.method, drafts.state) == ("rules", "investigating")
    assert drafts.customer.startswith("We're looking into a problem affecting ShopLite.")
    assert internal_terms(drafts.customer, incident, service) == []
    assert "Impact: Product listings and new orders fail." in drafts.leadership
    assert (
        "Likely cause: The deploy of shoplite-api version 5 raises KeyError." in drafts.leadership
    )


def test_the_model_drafts_but_internals_never_reach_customers(incident: Incident) -> None:
    service = service_info("shoplite")
    clean = {
        "customer_update": "Some customers can't load products right now. We're on it.",
        "leadership_summary": "Listings have failed since a release at 10:00 UTC.",
    }
    drafts = write(incident, service, (json.dumps(clean), "a-model"))
    assert (drafts.customer, drafts.method, drafts.model) == (
        clean["customer_update"],
        "llm",
        "a-model",
    )

    leaky = {**clean, "customer_update": "The shoplite-api Lambda raised KeyError at 1a2b3c4d."}
    drafts = write(incident, service, (json.dumps(leaky), "a-model"))
    assert drafts.customer.startswith("We're looking into a problem affecting ShopLite.")
    assert drafts.leadership == clean["leadership_summary"]  # internal, so the model's stays
    assert "it named shoplite-api, Lambda, KeyError" in drafts.notes[0]
    assert write(incident, service, ("no JSON here", "a-model")).method == "rules"


def test_the_page_escapes_text_runs_no_scripts_and_shows_what_is_ongoing() -> None:
    def update(incident_id: str, state: str, text: str, minute: int) -> dict[str, Any]:
        published = to_iso(T0 + timedelta(minutes=minute))
        return {
            "incident_id": incident_id,
            "state": state,
            "text": text,
            "public_name": "ShopLite",
            "published_at": published,
        }

    newest_first = [
        update("A", "investigating", "<script>alert(1)</script>", 5),
        update("B", "resolved", "Fixed.", 4),
        update("B", "investigating", "Slow.", 1),
    ]
    html = page.render(newest_first, T0)
    assert "<script" not in html
    assert "&lt;script&gt;" in html
    digest = base64.b64encode(hashlib.sha256(page.STYLE.encode()).digest()).decode()
    assert f"style-src 'sha256-{digest}'" in html
    assert "We are working on 1 problem</p>" in html  # A; B's latest update says resolved
    assert "All systems operational" in page.render(newest_first[1:], T0)


@pytest.fixture
def env(
    store: IncidentStore, incident: Incident, monkeypatch: pytest.MonkeyPatch
) -> dict[str, Any]:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    monkeypatch.setenv("DISCORD_PUBLIC_KEY", PUBLIC)
    monkeypatch.setenv("UPDATES_FUNCTION", "nightwatch-updates")
    monkeypatch.setenv("STATUS_BUCKET", "nightwatch-test-dashboard")
    started: list[tuple[str, dict[str, Any]]] = []
    pages: dict[str, str] = {}

    class FakeLambda:
        def invoke(self, **kwargs: Any) -> dict[str, Any]:
            started.append((kwargs["FunctionName"], json.loads(kwargs["Payload"])))
            return {}

    class FakeS3:
        def put_object(self, **kwargs: Any) -> None:
            assert kwargs["CacheControl"] == "public, max-age=60"
            pages[kwargs["Key"]] = kwargs["Body"].decode()

    monkeypatch.setattr(interactions, "_lambda", FakeLambda)
    monkeypatch.setattr(interactions, "_s3", FakeS3)
    monkeypatch.setattr(interactions._secrets, "get", lambda name: "https://site.example/")
    return {"store": store, "incident": incident, "started": started, "pages": pages}


def drafted(env: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> tuple[str, dict[str, Any]]:
    """Run the drafting Lambda (rules, no LLM). Returns the draft id and the edited reply."""
    edits: list[dict[str, Any]] = []
    monkeypatch.setattr(InteractionReply, "edit", lambda self, message: edits.append(message))
    monkeypatch.setattr(updates_lambda, "_ask_model", lambda settings, text: None)
    job = {"application_id": "1", "token": TOKEN, "incident_id": env["incident"].id, "user": "R"}
    out = updates_lambda.handler(job, None)
    return out["draft_id"], edits[0]


def click(custom_id: str, user: str = APPROVER) -> dict[str, Any]:
    return call(
        {
            "type": 3,
            "data": {"custom_id": custom_id},
            "member": {"user": {"id": user, "global_name": "Raagul"}},
            "message": {"id": "1565300000000000000"},
        }
    )


def test_update_command_starts_drafting_for_approvers_only(env: dict[str, Any]) -> None:
    assert call(slash("update", incident="INC-1")) == {"type": 5}
    job = {"application_id": "1565236782549700638", "token": TOKEN, "user": "Raagul"}
    assert env["started"] == [("nightwatch-updates", {**job, "incident_id": env["incident"].id})]
    assert "approvers" in call(slash("update", user="5", incident="INC-1"))["data"]["content"]
    assert "isn't among" in call(slash("update", incident="INC-9"))["data"]["content"]
    assert len(env["started"]) == 1


def test_drafts_are_published_once_and_only_by_approvers(
    env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    draft_id, reply = drafted(env, monkeypatch)
    buttons = [b["custom_id"] for b in reply["components"][0]["components"]]
    assert buttons == [f"nw:publish:{draft_id}", f"nw:discard:{draft_id}"]

    assert "approvers" in click(f"nw:publish:{draft_id}", user="5")["data"]["content"]
    monkeypatch.delenv("STATUS_BUCKET")
    assert "no status page" in click(f"nw:publish:{draft_id}")["data"]["content"]
    monkeypatch.setenv("STATUS_BUCKET", "nightwatch-test-dashboard")
    assert not env["pages"]

    published = click(f"nw:publish:{draft_id}")
    assert published["type"] == 7
    assert published["data"]["components"] == []
    assert "✅ Published by Raagul: https://site.example/status/index.html" in (
        published["data"]["content"]
    )
    assert "We&#x27;re looking into a problem affecting ShopLite." in env["pages"][page.KEY]
    again = click(f"nw:publish:{draft_id}")["data"]["content"]
    assert again == "These drafts were already published."

    other, _ = drafted(env, monkeypatch)
    assert "Discarded by Raagul" in click(f"nw:discard:{other}")["data"]["content"]
    assert click(f"nw:publish:{other}")["data"]["content"] == "These drafts were already discarded."
    events = {e["event"] for e in AuditLog(TABLE, "default", "t").for_incident(env["incident"].id)}
    assert {"updates_drafted", "update_refused", "status_update_published"} <= events
    assert "updates_discarded" in events
