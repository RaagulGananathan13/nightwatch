"""The incident workflow's definition: every path ends, and only an approval leads to a fix."""

import json
import re
from typing import Any

from tests.unit.test_layout import REPO_ROOT

VALUES = {
    "settle_seconds": "30",
    "approval_timeout_seconds": "1800",
    "investigate_arn": '"arn:investigate"',
    "report_arn": '"arn:report"',
    "remediate_arn": '"arn:remediate"',
    "verify_arn": '"arn:verify"',
}


def definition() -> dict[str, Any]:
    text = (REPO_ROOT / "stepfunctions" / "incident.asl.json").read_text(encoding="utf-8")
    # Terraform's templatefile fills ${name}; ARNs sit inside JSON strings already.
    text = re.sub(r'"\$\{(\w+_arn)\}"', lambda m: VALUES[m.group(1)], text)
    text = re.sub(r"\$\{(\w+)\}", lambda m: VALUES[m.group(1)], text)
    parsed: dict[str, Any] = json.loads(text)
    return parsed


def targets(state: dict[str, Any]) -> list[str]:
    found = [state[key] for key in ("Next", "Default") if key in state]
    found += [choice["Next"] for choice in state.get("Choices", [])]
    found += [catch["Next"] for catch in state.get("Catch", [])]
    return found


def test_every_state_is_reachable_and_every_path_ends() -> None:
    states = definition()["States"]
    seen, todo = set(), [definition()["StartAt"]]
    while todo:
        name = todo.pop()
        if name not in seen:
            seen.add(name)
            todo += targets(states[name])
    assert seen == set(states)
    for name, state in states.items():
        assert targets(state) or state.get("End") or state["Type"] == "Succeed", name


def test_only_an_approval_or_an_auto_approve_runbook_leads_to_a_fix() -> None:
    states = definition()["States"]
    into_fix = {name for name, state in states.items() if "Remediate" in targets(state)}
    assert into_fix == {"IsThereAFix", "Approved"}
    auto = [c for c in states["IsThereAFix"]["Choices"] if c["Next"] == "Remediate"]
    assert auto == [
        {"Variable": "$.action.auto_approve", "BooleanEquals": True, "Next": "Remediate"}
    ]
    assert states["Approved"]["Choices"] == [
        {"Variable": "$.decision.decision", "StringEquals": "approve", "Next": "Remediate"}
    ]


def test_the_approval_wait_times_out_into_an_escalation() -> None:
    wait = definition()["States"]["RequestApproval"]
    assert wait["Resource"].endswith(":lambda:invoke.waitForTaskToken")
    assert wait["Parameters"]["Payload"]["task_token.$"] == "$$.Task.Token"
    assert wait["TimeoutSeconds"] == 1800
    assert wait["Catch"][0] == {
        "ErrorEquals": ["States.Timeout"],
        "ResultPath": "$.error",
        "Next": "Escalate",
    }
