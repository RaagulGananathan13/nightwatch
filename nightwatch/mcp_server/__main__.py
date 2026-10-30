"""``python -m nightwatch.mcp_server``: NightWatch for your own AI assistant (spec F15).

Add it to Claude Code once (on macOS or Linux the Python is .venv/bin/python):

    claude mcp add nightwatch -- C:/path/to/nightwatch/.venv/Scripts/python.exe -m nightwatch.mcp_server

It starts with your AWS profile (AWS_PROFILE, default "nightwatch") and switches straight to
the nightwatch-mcp role, which has the investigator's read-only reach and nothing more. Every
tool call runs as that role and goes into NightWatch's audit log under your name.
``--check`` runs one whats_going_on and prints it, to test the setup without a client.
"""

from __future__ import annotations

import io
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

import nightwatch
from nightwatch.common.timeutil import utcnow
from nightwatch.mcp_server.protocol import Server
from nightwatch.mcp_server.tools import INSTRUCTIONS, call, definitions

ROLE = "nightwatch/nightwatch-mcp"  # path/name, in the caller's account


class RoleSession:
    """The read-only role's credentials, renewed five minutes before they run out."""

    def __init__(self, source: Any, region: str, clock: Callable[[], float] = time.time) -> None:
        identity = source.client("sts").get_caller_identity()
        self.caller = re.sub(r"[^\w+=,.@-]", "-", identity["Arn"].rsplit("/", 1)[-1])[:50]
        self.role_arn = os.environ.get("NIGHTWATCH_MCP_ROLE_ARN") or (
            f"arn:aws:iam::{identity['Account']}:role/{ROLE}"
        )
        self._source, self._region, self._clock = source, region, clock
        self._expires = 0.0

    def refresh(self) -> None:
        """Switch every boto3 client made from now on to the role, if it's due."""
        if self._clock() < self._expires - 300:
            return
        credentials = self._source.client("sts").assume_role(
            RoleArn=self.role_arn, RoleSessionName=f"mcp-{self.caller}", DurationSeconds=3600
        )["Credentials"]
        boto3.setup_default_session(
            aws_access_key_id=credentials["AccessKeyId"],
            aws_secret_access_key=credentials["SecretAccessKey"],
            aws_session_token=credentials["SessionToken"],
            region_name=self._region,
        )
        self._expires = credentials["Expiration"].timestamp()


def main() -> None:
    os.environ.setdefault("TABLE_NAME", "nightwatch")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-2"
    profile = os.environ.get("AWS_PROFILE") or "nightwatch"
    protocol = sys.stdout
    sys.stdout = sys.stderr  # logs and stray prints must never reach the MCP stream
    for stream in (sys.stdin, protocol):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", newline="\n" if stream is protocol else None)
    try:
        role = RoleSession(boto3.Session(profile_name=profile, region_name=region), region)
        role.refresh()
    except (BotoCoreError, ClientError) as exc:
        sys.stderr.write(f"NightWatch MCP: couldn't switch to the read-only role: {exc}\n")
        raise SystemExit(1) from None

    def run(name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        role.refresh()
        return call(name, arguments, role.caller, utcnow())

    info = {"name": "nightwatch", "title": "NightWatch", "version": nightwatch.__version__}
    server = Server(info, INSTRUCTIONS, definitions(), run)
    if "--check" in sys.argv:
        text, failed = run("whats_going_on", {})
        sys.stderr.write(f"{len(server.tools)} tools, running as {role.role_arn}\n\n{text}\n")
        raise SystemExit(1 if failed else 0)
    server.serve(sys.stdin, protocol)


if __name__ == "__main__":
    main()
