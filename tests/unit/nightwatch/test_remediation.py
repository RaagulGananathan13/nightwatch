"""Planning a fix from evidence, the action's status machine, running it, and verifying it."""

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import boto3
import pytest
from moto import mock_aws

from nightwatch.agent.handler import propose_fix
from nightwatch.agent.models import Diagnosis, Evidence, Hypothesis, Investigation, SuggestedAction
from nightwatch.agent.services import service_info
from nightwatch.agent.tools import AwsClients, run_tool
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.remediation import remediate as remediate_step
from nightwatch.remediation.actions import Action, ActionStore, TransitionError
from nightwatch.remediation.calls import CallError, check_target
from nightwatch.remediation.planner import plan
from nightwatch.remediation.verify import verify
from tests.unit.nightwatch.conftest import TABLE, create_table
from tests.unit.nightwatch.fakes import T0, FakeAws, FakeLambda, FakeSsm
from tests.unit.nightwatch.test_agent_tools import context

SHOPLITE = service_info("shoplite")


def changes_evidence(aws: FakeAws) -> Evidence:
    result = run_tool("recent_changes", {}, context(aws))
    return Evidence(
        id="E1", tool="recent_changes", label="changes", summary=result.summary,
        data=result.data, started_at=T0,
    )  # fmt: skip


def diagnosis(cites: str = "E1", confidence: float = 0.9, hint: str | None = None) -> Diagnosis:
    return Diagnosis(
        summary="Version 5 broke listings.",
        hypotheses=[Hypothesis(statement="x", confidence=confidence, evidence_ids=[cites])],
        suggested_actions=[SuggestedAction(description="fix it", runbook_id=hint)] if hint else [],
    )


# ----------------------------------------------------------------------------- planner


def test_a_bad_deploy_gets_a_rollback_to_the_version_that_was_live() -> None:
    aws = FakeAws(lambda_=FakeLambda(prev="4"))
    proposal = plan([changes_evidence(aws)], diagnosis(), SHOPLITE, T0, aws)
    assert proposal is not None
    assert proposal.runbook.id == "rollback-lambda-alias"
    assert proposal.preview == "shoplite-api alias live: version 5 -> 4"
    assert proposal.call == {
        "service": "lambda",
        "call": "update_alias",
        "args": {"FunctionName": "shoplite-api", "Name": "live", "FunctionVersion": "4"},
    }
    assert proposal.rollback is not None
    assert proposal.rollback["args"]["FunctionVersion"] == "5"
    assert proposal.before == {"alias_version": "5"}
    assert proposal.verify["dimensions"] == {"FunctionName": "shoplite-api"}
    assert proposal.reason.startswith("E1: shoplite-api version 5 went live 2m 00s before")


def test_no_fix_without_evidence_the_llm_can_not_invent_one() -> None:
    no_prev = FakeAws()  # the deploy doesn't say what was live before it: never guess
    assert plan([changes_evidence(no_prev)], diagnosis(), SHOPLITE, T0, no_prev) is None
    aws = FakeAws(lambda_=FakeLambda(prev="4"))
    evidence = [changes_evidence(aws)]
    # The diagnosis must back the change (a confident hypothesis citing it) or name the runbook.
    assert plan(evidence, diagnosis(confidence=0.3), SHOPLITE, T0, aws) is None
    assert plan(evidence, diagnosis(cites="E9"), SHOPLITE, T0, aws) is None
    hinted = plan(evidence, diagnosis(cites="E9", hint="rollback-lambda-alias"), SHOPLITE, T0, aws)
    assert hinted is not None
    # A hint for a runbook whose signal isn't in the evidence proposes nothing.
    assert (
        plan(evidence, diagnosis(cites="E9", hint="reset-feature-flags"), SHOPLITE, T0, aws) is None
    )


def test_nothing_to_do_when_the_resource_is_already_fixed() -> None:
    aws = FakeAws(lambda_=FakeLambda(prev="5"))  # "roll back" to the version already live
    assert plan([changes_evidence(aws)], diagnosis(), SHOPLITE, T0, aws) is None


def test_a_flag_change_gets_a_reset_that_remembers_the_old_value() -> None:
    history = [
        {
            "Version": 7,
            "Type": "String",
            "Value": '{"error_rate": 0.8}',
            "LastModifiedDate": T0 - timedelta(minutes=1),
            "LastModifiedUser": "arn:aws:iam::111122223333:user/nightwatch/nightwatch-deployer",
        }
    ]
    aws = FakeAws(ssm=FakeSsm(history))
    proposal = plan([changes_evidence(aws)], diagnosis(), SHOPLITE, T0, aws)
    assert proposal is not None
    assert proposal.runbook.id == "reset-feature-flags"
    assert proposal.preview == '/shoplite/flags: {"error_rate": 0.8} -> {}'
    assert proposal.call["args"] == {
        "Name": "/shoplite/flags", "Value": "{}", "Type": "String", "Overwrite": True,
    }  # fmt: skip
    assert proposal.rollback is not None
    assert proposal.rollback["args"]["Value"] == '{"error_rate": 0.8}'


def test_calls_may_only_touch_the_services_own_resources() -> None:
    call = {"service": "lambda", "call": "update_alias", "args": {"FunctionName": "other-app"}}
    with pytest.raises(CallError, match="not one of shoplite's resources"):
        check_target(SHOPLITE, call)
    with pytest.raises(CallError, match="not an allowed"):
        check_target(SHOPLITE, {**call, "call": "delete_function"})


# ------------------------------------------------------------------- moto: store + steps


@pytest.fixture
def world() -> Iterator[dict[str, Any]]:
    """ShopLite's function with versions 1 and 2 (2 live), plus NightWatch's table."""
    with mock_aws():
        create_table()
        role = boto3.client("iam").create_role(
            RoleName="test", AssumeRolePolicyDocument=json.dumps({"Statement": []})
        )["Role"]["Arn"]
        lam = boto3.client("lambda")
        lam.create_function(
            FunctionName="shoplite-api", Runtime="python3.13", Role=role,
            Handler="h.main", Code={"ZipFile": _zip("v1")}, Publish=True,
        )  # fmt: skip
        lam.update_function_code(FunctionName="shoplite-api", ZipFile=_zip("v2"))
        lam.publish_version(FunctionName="shoplite-api")
        lam.create_alias(FunctionName="shoplite-api", Name="live", FunctionVersion="2")
        boto3.client("ssm").put_parameter(
            Name="/nightwatch/kill_switch", Value="false", Type="String"
        )
        yield {
            "lambda": lam,
            "actions": ActionStore(TABLE, "default"),
            "audit": AuditLog(TABLE, "default", actor="test"),
            "settings": Settings(table_name=TABLE),
            "aws": AwsClients(),
        }


def make_action(status: str = "approved", target: str = "1", **extra: Any) -> Action:
    args = {"FunctionName": "shoplite-api", "Name": "live"}
    return Action.model_validate(
        {
            "id": "01JB0000000000000000000ACT",
            "incident_id": "01JB0000000000000000000INC",
            "service": "shoplite",
            "runbook_id": "rollback-lambda-alias",
            "title": "Roll back the deploy",
            "risk": "low",
            "reason": "E1: a deploy",
            "evidence_id": "E1",
            "preview": f"shoplite-api alias live: version 2 -> {target}",
            "blast_radius": "1 alias",
            "params": {
                "function_name": "shoplite-api", "target_version": target,
                "current_version": "2", "alias": "live",
            },
            "call": {"service": "lambda", "call": "update_alias",
                     "args": {**args, "FunctionVersion": target}},
            "rollback": {"service": "lambda", "call": "update_alias",
                         "args": {**args, "FunctionVersion": "2"}},
            "verify": {"namespace": "AWS/Lambda", "metric": "Errors", "stat": "Sum",
                       "dimensions": {"FunctionName": "shoplite-api"}, "max_value": 0, "minutes": 2},
            "before": {"alias_version": "2"},
            "status": status,
            "created_at": T0,
            "decided_by": "42",
            "decided_by_name": "Raagul",
            **extra,
        }
    )  # fmt: skip


def live(world: dict[str, Any]) -> str:
    return str(
        world["lambda"].get_alias(FunctionName="shoplite-api", Name="live")["FunctionVersion"]
    )


def test_store_keeps_the_token_out_of_the_json_and_refuses_double_decisions(
    world: dict[str, Any],
) -> None:
    store: ActionStore = world["actions"]
    action = store.create(make_action("awaiting_approval", task_token="token-1"))
    item = boto3.client("dynamodb").get_item(
        TableName=TABLE,
        Key={"PK": {"S": "TENANT#default#INC#01JB0000000000000000000INC"},
             "SK": {"S": "ACT#01JB0000000000000000000ACT"}},
    )["Item"]  # fmt: skip
    assert "token-1" not in item["json"]["S"]
    assert item["task_token"]["S"] == "token-1"
    assert store.get(action.incident_id, action.id) == action

    stale = action
    approved = store.transition(action, {"awaiting_approval"}, status="approved")
    assert approved.version == 1
    with pytest.raises(TransitionError, match="already approved"):
        store.transition(stale, {"awaiting_approval"}, status="rejected")  # the second click
    # An unrelated change made from a stale copy is re-applied to the fresh item, not lost.
    with_message = store.transition(stale, {"approved", "awaiting_approval"}, message_id="9")
    assert (with_message.status, with_message.message_id) == ("approved", "9")


def test_an_approved_fix_runs_once_and_is_recorded(world: dict[str, Any]) -> None:
    store: ActionStore = world["actions"]
    action = store.create(make_action())
    reply = remediate_step.fix(action, store, world["audit"], world["settings"], world["aws"])
    assert reply["status"] == "executed"
    assert 30 <= reply["verify_after_seconds"] <= 900
    assert live(world) == "1"
    done = store.get(action.incident_id, action.id)
    assert done is not None
    assert done.after == {"alias_version": "1"}
    assert done.executed_at
    again = remediate_step.fix(done, store, world["audit"], world["settings"], world["aws"])
    assert again["status"] == "executed"  # a retry doesn't run it twice
    events = [e["event"] for e in world["audit"].for_incident(action.incident_id)]
    assert events == ["remediation_executed"]


@pytest.mark.parametrize(
    ("action", "switch", "status", "why"),
    [
        (make_action("awaiting_approval"), "false", "awaiting_approval", ""),
        (make_action(), "true", "blocked", "kill switch"),
        (make_action(before={"alias_version": "3"}), "false", "failed", "changed since"),
        (make_action(target="1", preview="x", call={"service": "lambda", "call": "update_alias",
         "args": {"FunctionName": "other-app", "Name": "live", "FunctionVersion": "1"}}),
         "false", "failed", "doesn't match runbook"),
    ],
)  # fmt: skip
def test_nothing_changes_unless_approved_switched_on_unchanged_and_untampered(
    world: dict[str, Any], action: Action, switch: str, status: str, why: str
) -> None:
    boto3.client("ssm").put_parameter(
        Name="/nightwatch/kill_switch", Value=switch, Type="String", Overwrite=True
    )
    store: ActionStore = world["actions"]
    store.create(action)
    reply = remediate_step.fix(action, store, world["audit"], world["settings"], world["aws"])
    assert reply == {"status": status, "verify_after_seconds": 0}
    assert live(world) == "2"
    stored = store.get(action.incident_id, action.id)
    assert stored is not None
    assert why in stored.result


class FakeCloudWatch:
    def __init__(self, before: list[float], after: list[float]) -> None:
        self.before, self.after = before, after

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]:
        start = kwargs["StartTime"]
        values = self.after if start >= T0 else self.before
        return {
            "Datapoints": [
                {"Timestamp": start + timedelta(minutes=i), "Sum": v} for i, v in enumerate(values)
            ]
        }


@pytest.mark.parametrize(
    ("after", "verdict"),
    [([0.0, 0.0], "verified"), ([4.0, 3.0], "not_fixed"), ([40.0, 30.0], "worse"), ([], "no_data")],
)
def test_verification_compares_the_minutes_after_the_fix_with_before(
    world: dict[str, Any], after: list[float], verdict: str
) -> None:
    store: ActionStore = world["actions"]
    action = store.create(make_action("executed", executed_at=T0 - timedelta(seconds=20)))
    result = verify(
        action,
        store,
        world["audit"],
        FakeCloudWatch([15.0, 12.0], after),
        T0 + timedelta(minutes=4),
    )
    assert result == {"verdict": verdict}
    stored = store.get(action.incident_id, action.id)
    assert stored is not None
    assert stored.status == verdict
    assert stored.verification


def test_a_fix_that_made_things_worse_is_undone(world: dict[str, Any]) -> None:
    store: ActionStore = world["actions"]
    action = store.create(make_action())
    remediate_step.fix(action, store, world["audit"], world["settings"], world["aws"])
    executed = store.get(action.incident_id, action.id)
    assert executed is not None
    worse = store.transition(executed, {"executed"}, status="worse")
    reply = remediate_step.revert(worse, store, world["audit"], world["settings"], world["aws"])
    assert reply["status"] == "reverted"
    assert live(world) == "2"


def test_investigation_stores_the_proposal_and_hands_its_id_to_the_workflow(aws: None) -> None:
    fake = FakeAws(lambda_=FakeLambda(prev="4"))
    ctx = context(fake)
    store = ActionStore(TABLE, "default")
    result = Investigation(diagnosis=diagnosis(), method="rules")
    out = propose_fix(ctx, [changes_evidence(fake)], result, store, AuditLog(TABLE, "default", "t"))
    assert out is not None
    assert out["auto_approve"] is False
    (saved,) = store.for_incident(ctx.incident.id)
    assert saved.id == out["action_id"]
    assert saved.status == "proposed"
    assert propose_fix(ctx, [], result, store, AuditLog(TABLE, "default", "t")) is None


def _zip(text: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("h.py", f"def main(e, c): return '{text}'")
    return buffer.getvalue()
