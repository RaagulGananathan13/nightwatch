"""Deploy new code as an immutable Lambda version and point an alias at it.

    python -m scripts.deploy_lambda --function shoplite-api --zip .build/shoplite-api.zip \
        --alias live --description "sha=abc123 run=42"

Steps: upload the code, wait for Lambda to finish updating, publish a version whose
description says where it came from, then move the alias. NightWatch's "what changed?"
evidence reads that description, so always include the git SHA. GitHub Actions runs this on
every merge to main; the chaos lab uses it to ship a deliberately bad build.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3

if TYPE_CHECKING:
    from mypy_boto3_lambda import LambdaClient


def deploy(
    client: LambdaClient, function: str, code: bytes, alias: str, description: str
) -> dict[str, Any]:
    # The version live right now is the one to roll back to. Record it in the new version's
    # description ("prev=N"); "the previous version number" may itself be a bad build.
    previous = client.get_alias(FunctionName=function, Name=alias)["FunctionVersion"]
    suffix = f" prev={previous}"
    update = client.update_function_code(FunctionName=function, ZipFile=code)
    client.get_waiter("function_updated_v2").wait(FunctionName=function)
    published = client.publish_version(
        FunctionName=function,
        Description=description[: 256 - len(suffix)] + suffix,
        CodeSha256=update["CodeSha256"],  # refuse to publish if someone else changed the code
    )
    client.update_alias(FunctionName=function, Name=alias, FunctionVersion=published["Version"])
    return {
        "function": function,
        "alias": alias,
        "version": published["Version"],
        "previous_version": previous,
        "description": published.get("Description", ""),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--function", required=True)
    parser.add_argument("--zip", type=Path, required=True)
    parser.add_argument("--alias", default="live")
    parser.add_argument("--description", required=True, help='e.g. "sha=<git sha> run=<id>"')
    args = parser.parse_args()
    result = deploy(
        boto3.client("lambda"), args.function, args.zip.read_bytes(), args.alias, args.description
    )
    print(json.dumps(result, indent=2))  # noqa: T201


if __name__ == "__main__":
    main()
