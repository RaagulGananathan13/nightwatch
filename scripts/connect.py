"""Connect an AWS account to NightWatch (spec section 13).

    python -m scripts.connect new-id --tenant demo
        Make the tenant's ExternalId and keep it in SSM. It is printed once, for the
        CloudFormation stack (connect/nightwatch-connect.yaml) in the account to connect.

    python -m scripts.connect check --tenant demo --role <the stack's RoleArn output>
        Assume the role the way NightWatch does and read through it, then show that the role
        refuses anyone without the ExternalId.

Both run as your AWS profile (AWS_PROFILE, default "nightwatch").
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from collections.abc import Callable
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.accounts import session_for
from nightwatch.agent.services import Account, service_info
from nightwatch.agent.tools import AwsClients, ToolContext, run_tool
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.models import Incident
from nightwatch.common.timeutil import utcnow


def external_id_param(tenant: str) -> str:
    return f"/nightwatch/tenants/{tenant}/external_id"


def new_id(tenant: str, ssm: Any) -> str:
    """A fresh, unguessable ExternalId for the tenant, kept in SSM. Never replaces one."""
    value = secrets.token_urlsafe(24)
    ssm.put_parameter(
        Name=external_id_param(tenant),
        Value=value,
        Type="String",  # an ExternalId isn't a secret, but it must be unique to the tenant
        Description=f"ExternalId for NightWatch's connected account '{tenant}'",
        Overwrite=False,
    )
    return value


def check(tenant: str, role: str, region: str, say: Callable[[str], None] = print) -> int:
    account = Account(role_arn=role, external_id_param=external_id_param(tenant), region=region)
    session = session_for(account)
    say(f"Assumed with the ExternalId: {session.client('sts').get_caller_identity()['Arn']}")

    service = service_info("shoplite").model_copy(update={"account": account})
    now = utcnow()
    incident = Incident(
        id="01JB0000000000000000CONNCT",
        tenant_id="default",
        service=service.name,
        title="connect check",
        first_alarm="",
        opened_at=now,
        updated_at=now,
        last_alert_at=now,
        alert_count=0,
    )
    ctx = ToolContext(
        incident=incident,
        service=service,
        now=now,
        store=IncidentStore("nightwatch", "default"),
        aws=AwsClients(session),
    )
    for tool, args in (
        ("describe_resource", {"name": service.function}),
        ("usage_history", {"days": 7}),
    ):
        result = run_tool(tool, args, ctx)
        say(f"{tool} through the connected role: {result.summary.splitlines()[0]}")

    try:
        boto3.client("sts").assume_role(RoleArn=role, RoleSessionName="no-external-id")
    except ClientError as exc:
        say(f"Without the ExternalId: refused ({exc.response['Error']['Code']}), as it should be.")
        return 0
    say("FAIL: the role let us in without the ExternalId.")
    return 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m scripts.connect", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    make = sub.add_parser("new-id", help="make and store a tenant's ExternalId")
    make.add_argument("--tenant", required=True)
    test = sub.add_parser("check", help="assume a connected role and read through it")
    test.add_argument("--tenant", required=True)
    test.add_argument("--role", required=True)
    test.add_argument("--region", default="us-east-2")
    args = parser.parse_args()
    os.environ.setdefault("AWS_PROFILE", "nightwatch")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-2")
    try:
        if args.command == "new-id":
            value = new_id(args.tenant, boto3.client("ssm"))
            sys.stdout.write(
                f"ExternalId for '{args.tenant}' (also in SSM at {external_id_param(args.tenant)}):\n"
                f"{value}\n"
            )
            return
        sys.exit(check(args.tenant, args.role, args.region))
    except (BotoCoreError, ClientError, ValueError) as exc:
        sys.exit(f"connect {args.command} failed: {exc}")


if __name__ == "__main__":
    main()
