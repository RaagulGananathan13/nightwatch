"""Reading a connected account (spec section 13): STS AssumeRole with the tenant's ExternalId.

The connect template (connect/nightwatch-connect.yaml) makes a read-only role in the account to
watch. Its trust policy accepts NightWatch's account only with the ExternalId NightWatch made
for that tenant, so someone who learns the role's ARN, even another NightWatch user, can't
borrow it (the "confused deputy" problem). Credentials are kept until five minutes before they
run out. A connected account is only read: no fix ever runs in it.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import boto3

from nightwatch.agent.services import Account, ServiceInfo
from nightwatch.agent.tools import AwsClients
from nightwatch.common.config import Settings

SESSION_SECONDS = 3600  # the most a chained role session may last
_sessions: dict[str, tuple[float, Any]] = {}


def session_for(
    account: Account,
    sts: Any = None,
    ssm: Any = None,
    clock: Callable[[], float] = time.time,
) -> Any:
    """A boto3 session in the connected account, through its NightWatch read-only role."""
    cached = _sessions.get(account.role_arn)
    if cached and clock() < cached[0] - 300:
        return cached[1]
    ssm = ssm or boto3.client("ssm")
    external_id = ssm.get_parameter(Name=account.external_id_param, WithDecryption=True)
    credentials = (sts or boto3.client("sts")).assume_role(
        RoleArn=account.role_arn,
        RoleSessionName="nightwatch",
        ExternalId=external_id["Parameter"]["Value"],
        DurationSeconds=SESSION_SECONDS,
    )["Credentials"]
    session = boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=account.region,
    )
    _sessions[account.role_arn] = (credentials["Expiration"].timestamp(), session)
    return session


def clients_for(service: ServiceInfo) -> AwsClients:
    """Clients for the account the service runs in: its connected account, or NightWatch's."""
    return AwsClients(session_for(service.account)) if service.account else AwsClients()


def llm_keys(settings: Settings, service: ServiceInfo) -> dict[str, str]:
    """NightWatch's LLM keys, or the tenant's own Groq key if it brought one."""
    own = service.account.llm_key_param if service.account else ""
    return {"groq": own or settings.groq_key_param, "gemini": settings.gemini_key_param}
