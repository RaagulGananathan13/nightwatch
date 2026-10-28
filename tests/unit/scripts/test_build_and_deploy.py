"""Lambda zips are reproducible, and deploys publish a traceable version behind the alias."""

import io
import json
import zipfile
from pathlib import Path

import boto3
from moto import mock_aws

from scripts.build_lambda import build
from scripts.deploy_lambda import deploy

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
    assert version["Configuration"]["Description"] == "sha=abc123 run=7"
