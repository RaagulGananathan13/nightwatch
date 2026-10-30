"""Fix-as-pull-request (F10): detection from evidence, one-line Terraform edits, the PR itself."""

import base64
import io
import json
from pathlib import Path
from typing import Any

import boto3
import pytest

from nightwatch.agent.models import Evidence
from nightwatch.agent.services import service_info
from nightwatch.common.incidents import IncidentStore
from nightwatch.remediation import fixpr
from nightwatch.remediation.config_fix import detect, proposal, read_value, set_value
from nightwatch.remediation.github import GitHub
from tests.unit.nightwatch.conftest import T0, TABLE, make_alert
from tests.unit.nightwatch.test_memory import resolved

REPO = Path(__file__).resolve().parents[3]
SHOPLITE = service_info("shoplite")


def ev(eid: str, label: str, data: dict[str, Any]) -> Evidence:
    return Evidence(id=eid, tool="t", label=label, summary="s", data=data, started_at=T0)


def evidence(
    changes: list[dict[str, Any]] | None = None,
    flags: str = "{}",
    error_type: str = "",
    runtime: str = "",
    reports: list[dict[str, str]] | None = None,
    timeout_s: int = 5,
) -> list[Evidence]:
    return [
        ev("E3", "changes", {"changes": changes or [], "parameters": {"/shoplite/flags": flags}}),
        ev("E4", "errors_by_version", {"rows": [{"error_type": error_type, "errors": "40"}]}),
        ev("E5", "runtime_errors", {"rows": [{"error": runtime, "count": "40"}]}),
        ev("E6", "lambda_reports", {"rows": reports or []}),
        ev("E7", "resource", {"timeout_s": timeout_s}),
    ]


OOM = [{"lambda_version": "12", "invocations": "40", "max_memory_mb": "256", "memory_mb": "256"}]
SLOW = [{"lambda_version": "12", "invocations": "40", "max_ms": "5001", "memory_mb": "256"}]


def test_throttling_without_a_capacity_change_needs_more_read_capacity() -> None:
    incident = resolved()
    fix = detect(evidence(error_type="ProvisionedThroughputExceededException"), incident, SHOPLITE)
    assert fix is not None
    assert fix.knob == "read_capacity"
    assert fix.evidence_ids == ["E3", "E4"]
    lowered = evidence(
        changes=[{"kind": "capacity"}], error_type="ProvisionedThroughputExceededException"
    )
    assert detect(lowered, incident, SHOPLITE) is None  # the raise-dynamodb-capacity runbook's job


def test_memory_and_timeout_only_when_nothing_else_explains_it() -> None:
    incident = resolved()
    oom = evidence(runtime="Runtime.OutOfMemory", reports=OOM)
    assert detect(oom, incident, SHOPLITE).knob == "memory"  # type: ignore[union-attr]
    assert (
        detect(
            evidence(runtime="Runtime.OutOfMemory", reports=OOM, flags='{"memory_hog_mb": 400}'),
            incident,
            SHOPLITE,
        )
        is None
    )
    assert (
        detect(
            evidence(runtime="Runtime.OutOfMemory", reports=OOM, changes=[{"kind": "deploy"}]),
            incident,
            SHOPLITE,
        )
        is None
    )
    slow = evidence(runtime="Task timed out after 5.00 seconds", reports=SLOW)
    assert detect(slow, incident, SHOPLITE).knob == "timeout"  # type: ignore[union-attr]
    assert detect(evidence(), incident, SHOPLITE) is None


def test_edits_change_only_the_one_setting_in_the_real_files() -> None:
    variables = (REPO / "infra/modules/shoplite/variables.tf").read_text(encoding="utf-8")
    capacity = SHOPLITE.terraform["read_capacity"]
    now = read_value(variables, capacity)
    changed = set_value(variables, capacity, now + 1)
    assert read_value(changed, capacity) == now + 1
    assert [line for line in changed.splitlines() if line not in variables.splitlines()] == [
        f"  default     = {now + 1}"
    ]

    main = (REPO / "infra/modules/shoplite/main.tf").read_text(encoding="utf-8")
    memory = SHOPLITE.terraform["memory"]
    bigger = set_value(main, memory, read_value(main, memory) * 2)
    assert bigger.count("memory_size") == main.count("memory_size")
    assert "memory_size      = 128" in bigger  # the canary's setting is left alone
    assert proposal(5, capacity) == 10
    assert proposal(8, capacity) == 10  # capped at the catalogue's ceiling


class FakeGitHub:
    """Answers like api.github.com for the calls a fix PR makes."""

    def __init__(self, open_prs: list[dict[str, str]] | None = None, branch_exists: bool = False):
        self.calls: list[tuple[str, str, Any]] = []
        self.open_prs = open_prs or []
        self.branch_exists = branch_exists

    def __call__(self, request: Any, timeout: float) -> Any:
        method, url = request.get_method(), request.full_url
        body = json.loads(request.data) if request.data else None
        self.calls.append((method, url.removeprefix("https://api.github.com/repos/o/r"), body))
        assert request.get_header("Authorization") == "Bearer t"
        if "/pulls?state=open" in url:
            reply: Any = self.open_prs
        elif url.endswith("/git/ref/heads/main"):
            reply = {"object": {"sha": "main-sha"}}
        elif url.endswith("/git/refs") and self.branch_exists:
            import urllib.error

            raise urllib.error.HTTPError(url, 422, "exists", {}, io.BytesIO(b"exists"))  # type: ignore[arg-type]
        elif "/contents/" in url and method == "GET":
            reply = {"content": base64.b64encode(b"x = 1").decode(), "sha": "blob-sha"}
        elif url.endswith("/pulls"):
            reply = {"html_url": "https://github.com/o/r/pull/7"}
        else:
            reply = {}
        return type("R", (io.BytesIO,), {"__enter__": lambda s: s, "__exit__": lambda s, *a: None})(
            json.dumps(reply).encode()
        )


def test_github_opens_one_pull_request_per_branch() -> None:
    fake = FakeGitHub()
    github = GitHub("t", "o/r", opener=fake)
    assert github.file("infra/x.tf") == ("x = 1", "blob-sha")
    url = github.open_pull_request(
        "nightwatch/inc-020-read-capacity", "infra/x.tf", "x = 2", "blob-sha", "m", "T", "B"
    )
    assert url == "https://github.com/o/r/pull/7"
    assert [(m, p.split("?")[0]) for m, p, _ in fake.calls[1:]] == [
        ("GET", "/pulls"),
        ("GET", "/git/ref/heads/main"),
        ("POST", "/git/refs"),
        ("PUT", "/contents/infra/x.tf"),
        ("POST", "/pulls"),
    ]
    assert fake.calls[-2][2]["branch"] == "nightwatch/inc-020-read-capacity"
    again = GitHub(
        "t", "o/r", opener=FakeGitHub(open_prs=[{"html_url": "https://github.com/o/r/pull/7"}])
    )
    assert again.open_pull_request("b", "infra/x.tf", "", "s", "m", "T", "B").endswith("/pull/7")
    assert GitHub("t", "o/r", opener=FakeGitHub(branch_exists=True)).open_pull_request(
        "b", "infra/x.tf", "", "s", "m", "T", "B"
    )
    with pytest.raises(ValueError, match="file path"):
        github.file("../secrets")


@pytest.fixture
def lambda_env(store: IncidentStore, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("TABLE_NAME", TABLE)
    boto3.client("ssm").put_parameter(Name="/nightwatch/kill_switch", Value="false", Type="String")
    monkeypatch.setattr(fixpr, "_aws", type("A", (), {"ssm": boto3.client("ssm")})())
    opened: list[dict[str, Any]] = []

    class StubGitHub:
        def __init__(self, token: str, repository: str) -> None:
            assert repository == "RaagulGananathan13/nightwatch"

        def file(self, path: str) -> tuple[str, str]:
            return (REPO / path).read_text(encoding="utf-8"), "blob"

        def open_pull_request(self, **kwargs: Any) -> str:
            opened.append(kwargs)
            return "https://github.com/RaagulGananathan13/nightwatch/pull/42"

    monkeypatch.setattr(fixpr, "GitHub", StubGitHub)
    monkeypatch.setattr(
        fixpr, "_secrets", type("S", (), {"get": lambda self, name: "token-for-tests"})()
    )
    monkeypatch.setattr(fixpr, "webhook_from_ssm", lambda param, secret: None)
    incident = store.assign_number(store.open_incident(make_alert(), None, T0))
    fix = {
        "knob": "read_capacity",
        "reason": "Reads were throttled. @everyone <b>",
        "evidence_ids": ["E3"],
    }
    store.set_investigation(
        incident.id,
        "done",
        {
            "diagnosis": {"summary": "Ping @someone"},
            "config_fix": fix,
            "evidence": [{"id": "E3", "brief": "no changes"}],
        },
    )
    return {"store": store, "incident": incident, "opened": opened}


def test_fix_pr_lambda_opens_a_reviewed_change(lambda_env: dict[str, Any]) -> None:
    incident = lambda_env["incident"]
    out = fixpr.handler({"incident_id": incident.id}, None)
    assert out["opened"] is True
    (request,) = lambda_env["opened"]
    assert request["branch"] == "nightwatch/inc-001-read-capacity"
    now = read_value(
        (REPO / "infra/modules/shoplite/variables.tf").read_text("utf-8"),
        SHOPLITE.terraform["read_capacity"],
    )
    after = proposal(now, SHOPLITE.terraform["read_capacity"])
    if after > now:
        assert (
            request["title"]
            == f"Raise shoplite read capacity from {now} to {after} read units (INC-001)"
        )
        assert f"  default     = {after}" in request["text"]
    body = request["body"]
    assert "&#64;everyone" in body
    assert "@" not in body
    assert "<b>" not in body
    assert "**E3**: no changes" in body
    assert lambda_env["store"].get_incident(incident.id).fix_pr_url.endswith("/pull/42")


def test_no_pull_request_while_the_kill_switch_is_on(lambda_env: dict[str, Any]) -> None:
    boto3.client("ssm").put_parameter(
        Name="/nightwatch/kill_switch", Value="true", Type="String", Overwrite=True
    )
    assert fixpr.handler({"incident_id": lambda_env["incident"].id}, None)["opened"] is False
    assert lambda_env["opened"] == []
