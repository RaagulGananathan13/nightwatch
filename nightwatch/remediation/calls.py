"""The AWS calls a runbook may make: what each one changes, its current state, and running it.

The planner reads state here (with the read-only investigator role) and the remediator reads
it again just before it acts, so a fix only runs if the resource still looks the way it did
when a human approved it. Adding a call means adding code here and to the remediator's IAM
policy, so it gets reviewed like code, not just as a YAML file.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.services import ServiceInfo
from nightwatch.remediation.runbooks import ALLOWED_CALLS


class CallError(Exception):
    """The call isn't allowed, or it would touch something outside the service."""


def check_target(service: ServiceInfo, call: dict[str, Any]) -> None:
    """Refuse a call that isn't allow-listed or whose target isn't this service's resource."""
    name, method, args = call["service"], call["call"], call["args"]
    if (name, method) not in ALLOWED_CALLS:
        raise CallError(f"{name}.{method} is not an allowed remediation call")
    kind, resource = _target(name, args)
    owned = {
        "function": service.lambda_functions,
        "parameter": service.ssm_parameters,
        "table": service.dynamodb_tables,
    }[kind]
    if resource not in owned:
        raise CallError(f"{kind} {resource} is not one of {service.name}'s resources")


def read_state(aws: Any, call: dict[str, Any]) -> dict[str, str]:
    """The part of the target's current state that this call would change."""
    method, args = call["call"], call["args"]
    if method == "update_alias":
        alias = aws.lambda_.get_alias(FunctionName=args["FunctionName"], Name=args["Name"])
        return {"alias_version": str(alias["FunctionVersion"])}
    if method == "delete_function_concurrency":
        reply = aws.lambda_.get_function_concurrency(FunctionName=args["FunctionName"])
        reserved = reply.get("ReservedConcurrentExecutions")
        return {"reserved_concurrency": "none" if reserved is None else str(reserved)}
    if method == "put_parameter":
        return {"parameter_value": aws.ssm.get_parameter(Name=args["Name"])["Parameter"]["Value"]}
    if method == "update_table":
        table = aws.dynamodb.describe_table(TableName=args["TableName"])["Table"]
        throughput = table["ProvisionedThroughput"]
        return {
            "read_units": str(throughput["ReadCapacityUnits"]),
            "write_units": str(throughput["WriteCapacityUnits"]),
        }
    raise CallError(f"no state reader for {method}")


def already_done(call: dict[str, Any], state: dict[str, str]) -> bool:
    """True when the resource is already the way this call would leave it."""
    method, args = call["call"], call["args"]
    if method == "update_alias":
        return state["alias_version"] == str(args["FunctionVersion"])
    if method == "delete_function_concurrency":
        return state["reserved_concurrency"] == "none"
    if method == "put_parameter":
        return state["parameter_value"] == str(args["Value"])
    if method == "update_table":
        wanted = args["ProvisionedThroughput"]
        return state == {
            "read_units": str(wanted["ReadCapacityUnits"]),
            "write_units": str(wanted["WriteCapacityUnits"]),
        }
    return False


def execute(aws: Any, call: dict[str, Any]) -> None:
    name, method = call["service"], call["call"]
    if (name, method) not in ALLOWED_CALLS:
        raise CallError(f"{name}.{method} is not an allowed remediation call")
    client = {"lambda": aws.lambda_, "ssm": aws.ssm, "dynamodb": aws.dynamodb}[name]
    getattr(client, method)(**call["args"])


def kill_switch_on(aws: Any, parameter: str) -> bool:
    """Spec F14. Fails closed: if the switch can't be read, remediation counts as switched off."""
    try:
        value = aws.ssm.get_parameter(Name=parameter)["Parameter"]["Value"]
    except (BotoCoreError, ClientError):
        return True
    return str(value).strip().lower() != "false"


def describe(call: dict[str, Any]) -> str:
    """One line for the audit log and the chat card, e.g. "lambda.update_alias(...)"."""
    args = ", ".join(f"{key}={value}" for key, value in call["args"].items())
    return f"{call['service']}.{call['call']}({args})"


def _target(name: str, args: dict[str, Any]) -> tuple[str, str]:
    if name == "lambda":
        return "function", str(args["FunctionName"])
    if name == "ssm":
        return "parameter", str(args["Name"])
    return "table", str(args["TableName"])
