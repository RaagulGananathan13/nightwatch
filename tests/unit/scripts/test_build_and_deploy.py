"""Lambda zips are reproducible, and deploys publish a traceable version behind the alias."""

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import boto3
import pytest
from moto import mock_aws

from scripts.build_lambda import build
from scripts.deploy_lambda import deploy
from scripts.discord_endpoint import set_endpoint

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_builds_are_byte_identical(tmp_path: Path) -> None:
    first = build(REPO_ROOT / "shoplite", tmp_path / "a.zip", "abc123")
    second = build(REPO_ROOT / "shoplite", tmp_path / "b.zip", "abc123")
    assert first == second
    assert (tmp_path / "a.zip").read_bytes() == (tmp_path / "b.zip").read_bytes()


def test_zip_holds_the_package_and_the_version_stamp(tmp_path: Path) -> None:
    build(REPO_ROOT / "shoplite", tmp_path / "app.zip", "abc123")
    with zipfile.ZipFile(tmp_path / "app.zip") as archive:
        names = archive.namelist()
        stamp = json.loads(archive.read("shoplite/_build.json"))
    assert "shoplite/app.py" in names
    assert not any("__pycache__" in name or name.endswith(".md") for name in names)
    assert stamp == {"version": "abc123"}


def _zip(source: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("handler.py", source)
    return buffer.getvalue()


@mock_aws
def test_deploy_publishes_a_described_version_and_moves_the_alias() -> None:
    role = boto3.client("iam").create_role(
        RoleName="test-lambda",
        AssumeRolePolicyDocument=json.dumps({"Version": "2012-10-17", "Statement": []}),
    )["Role"]["Arn"]
    client = boto3.client("lambda")
    client.create_function(
        FunctionName="shoplite-api",
        Runtime="python3.13",
        Role=role,
        Handler="handler.main",
        Code={"ZipFile": _zip("def main(e, c): return 1")},
        Publish=True,
    )
    client.create_alias(FunctionName="shoplite-api", Name="live", FunctionVersion="1")

    result = deploy(
        client,
        "shoplite-api",
        _zip("def main(e, c): return 2"),
        "live",
        "sha=abc123 run=7",
    )

    assert result["previous_version"] == "1"
    assert result["version"] != "1"
    alias = client.get_alias(FunctionName="shoplite-api", Name="live")
    assert alias["FunctionVersion"] == result["version"]
    version = client.get_function(FunctionName="shoplite-api", Qualifier=result["version"])
    assert version["Configuration"]["Description"] == "sha=abc123 run=7 prev=1"  # rollback target


def test_nightwatch_zip_carries_the_runbooks(tmp_path: Path) -> None:
    build(
        REPO_ROOT / "nightwatch",
        tmp_path / "nw.zip",
        extras=[(REPO_ROOT / "runbooks", "nightwatch/runbooks")],
    )
    with zipfile.ZipFile(tmp_path / "nw.zip") as archive:
        names = archive.namelist()
    assert "nightwatch/runbooks/rollback-lambda-alias.yaml" in names
    assert "nightwatch/runbooks/README.md" not in names


def test_discord_endpoint_is_a_patch_of_the_application() -> None:
    sent: list[Any] = []

    class Reply(io.BytesIO):
        def __enter__(self) -> "Reply":
            return self

        def __exit__(self, *args: object) -> None:
            return None

    def opener(request: Any, timeout: float) -> Reply:
        sent.append(request)
        return Reply(b'{"id": "1", "interactions_endpoint_url": "u"}')

    url = "https://abc123.lambda-url.us-east-2.on.aws/"
    assert set_endpoint("t", url, opener) == {"id": "1", "interactions_endpoint_url": "u"}
    assert sent[0].get_method() == "PATCH"
    assert sent[0].full_url == "https://discord.com/api/v10/applications/@me"
    assert json.loads(sent[0].data) == {"interactions_endpoint_url": url}
    with pytest.raises(ValueError, match="Lambda function URL"):
        set_endpoint("t", "https://evil.example.com/", opener)
