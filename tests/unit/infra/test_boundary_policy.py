"""Guards on the NightWatchBoundary policy, the ceiling for everything NightWatch can do in AWS.

A careless edit to this one file could hand the deployer (and every role it creates) admin
rights, so these checks run in CI on every change.
"""

import json
import re
from pathlib import Path
from typing import Any

import pytest

POLICY_FILE = (
    Path(__file__).resolve().parents[3] / "infra/bootstrap/nightwatch-boundary-policy.json"
)
BOUNDARY_ARN = "arn:aws:iam::111122223333:policy/nightwatch/NightWatchBoundary"
IAM_SIZE_LIMIT = 6144  # characters, not counting whitespace


def _policy() -> dict[str, Any]:
    text = POLICY_FILE.read_text(encoding="utf-8")
    text = text.replace("__ACCOUNT_ID__", "111122223333").replace("__REGION__", "us-east-2")
    policy: dict[str, Any] = json.loads(text)
    return policy


def _as_list(value: str | list[str]) -> list[str]:
    return [value] if isinstance(value, str) else value


def _allows() -> list[dict[str, Any]]:
    return [s for s in _policy()["Statement"] if s["Effect"] == "Allow"]


def test_fits_in_one_managed_policy() -> None:
    # A permissions boundary must be a single managed policy, so it can't be split later.
    raw = re.sub(r"\s", "", POLICY_FILE.read_text(encoding="utf-8"))
    assert len(raw) <= IAM_SIZE_LIMIT


def test_no_placeholders_left_after_substitution() -> None:
    assert "__" not in json.dumps(_policy())


@pytest.mark.parametrize("forbidden", ["*", "iam:*", "sts:*", "organizations:*", "account:*"])
def test_never_allows_admin_style_wildcards(forbidden: str) -> None:
    for statement in _allows():
        assert forbidden not in _as_list(statement["Action"]), statement["Sid"]


def test_iam_writes_are_limited_to_nightwatch_roles() -> None:
    for statement in _allows():
        iam_actions = [a for a in _as_list(statement["Action"]) if a.startswith("iam:")]
        if not iam_actions or statement["Sid"] in {"GitHubOidcProvider", "FindOidcProviders"}:
            continue
        assert _as_list(statement["Resource"]) == ["arn:aws:iam::111122223333:role/nightwatch/*"]


def test_new_roles_must_carry_this_boundary() -> None:
    (create,) = [s for s in _allows() if "iam:CreateRole" in _as_list(s["Action"])]
    assert create["Condition"]["StringEquals"]["iam:PermissionsBoundary"] == BOUNDARY_ARN


def test_cannot_create_users_or_keys() -> None:
    actions = {a for s in _allows() for a in _as_list(s["Action"])}
    assert not actions & {"iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile"}


def test_boundary_is_protected_by_explicit_denies() -> None:
    denies = [s for s in _policy()["Statement"] if s["Effect"] == "Deny"]
    denied = {a for s in denies for a in _as_list(s["Action"])}
    assert {"iam:CreatePolicyVersion", "iam:DeleteRolePermissionsBoundary"} <= denied


def test_account_wide_statements_are_pinned_to_the_region_or_read_only() -> None:
    for statement in _allows():
        if statement["Resource"] != "*":
            continue
        actions = _as_list(statement["Action"])
        condition = json.dumps(statement.get("Condition", {}))
        read_only = all(re.search(r":(List|Describe|Get)", action) for action in actions)
        # CloudFront is global: its API only answers in us-east-1.
        cloudfront = all(action.startswith("cloudfront:") for action in actions)
        pinned = "us-east-2" in condition or (cloudfront and "us-east-1" in condition)
        assert pinned or read_only, statement["Sid"]


def test_dashboard_changes_only_touch_this_accounts_cdn_and_login() -> None:
    manage = next(s for s in _allows() if s["Sid"] == "DashboardCdnManage")
    assert all(":111122223333:" in arn for arn in _as_list(manage["Resource"]))
    login = next(s for s in _allows() if s["Sid"] == "DashboardLogin")
    assert login["Resource"] == "arn:aws:cognito-idp:us-east-2:111122223333:userpool/*"
