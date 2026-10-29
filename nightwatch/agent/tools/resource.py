"""describe_resource: current configuration of one of the service's Lambda functions or tables.

Environment variable values are never read into evidence (they may hold secrets), only their
names.
"""

from __future__ import annotations

from botocore.exceptions import ClientError
from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolError, ToolResult


class Args(BaseModel):
    name: str = Field(max_length=255, description="function or table name")


def run(ctx: ToolContext, args: Args) -> ToolResult:
    if args.name in ctx.service.lambda_functions:
        return _function(ctx, args.name)
    if args.name in ctx.service.dynamodb_tables:
        return _table(ctx, args.name)
    raise ToolError(f"'{args.name}' is not a resource of {ctx.service.name}")


def _function(ctx: ToolContext, name: str) -> ToolResult:
    alias = ctx.service.lambda_alias
    try:
        config = ctx.aws.lambda_.get_function_configuration(FunctionName=name, Qualifier=alias)
    except ClientError:
        alias, config = "$LATEST", ctx.aws.lambda_.get_function_configuration(FunctionName=name)
    reserved = ctx.aws.lambda_.get_function_concurrency(FunctionName=name).get(
        "ReservedConcurrentExecutions"
    )
    env_names = sorted((config.get("Environment") or {}).get("Variables", {}))
    data = {
        "alias": alias,
        "version": config.get("Version"),
        "description": config.get("Description", ""),
        "runtime": config.get("Runtime"),
        "architectures": config.get("Architectures", []),
        "memory_mb": config.get("MemorySize"),
        "timeout_s": config.get("Timeout"),
        "reserved_concurrency": reserved,
        "environment_variable_names": env_names,
        "last_modified": config.get("LastModified"),
        "state": config.get("State"),
        "last_update_status": config.get("LastUpdateStatus"),
    }
    summary = (
        f"Lambda {name} ({alias} -> version {data['version']}): {data['runtime']} on "
        f"{'/'.join(data['architectures']) or '?'}, {data['memory_mb']} MB, timeout "
        f"{data['timeout_s']} s, reserved concurrency {reserved if reserved is not None else 'none'}; "
        f"description: {data['description'] or '-'}; last modified {data['last_modified']}; "
        f"environment variables {', '.join(env_names) or '-'} (values not shown); "
        f"state {data['state']}, last update {data['last_update_status']}."
    )
    return ToolResult(summary, data)


def _table(ctx: ToolContext, name: str) -> ToolResult:
    table = ctx.aws.dynamodb.describe_table(TableName=name)["Table"]
    throughput = table.get("ProvisionedThroughput", {})
    data = {
        "status": table.get("TableStatus"),
        "billing_mode": (table.get("BillingModeSummary") or {}).get("BillingMode", "PROVISIONED"),
        "read_units": throughput.get("ReadCapacityUnits"),
        "write_units": throughput.get("WriteCapacityUnits"),
        "item_count": table.get("ItemCount"),
        "size_bytes": table.get("TableSizeBytes"),
        "indexes": len(table.get("GlobalSecondaryIndexes", [])),
    }
    summary = (
        f"DynamoDB table {name}: {data['status']}, {data['billing_mode']} with "
        f"{data['read_units']} read / {data['write_units']} write units, "
        f"{data['item_count']} items, {data['indexes']} global indexes."
    )
    return ToolResult(summary, data)


TOOL = Tool(
    name="describe_resource",
    description="Current configuration of one of the service's Lambda functions or DynamoDB tables.",
    args=Args,
    run=run,
)
