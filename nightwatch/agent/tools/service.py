"""get_service_info: the service catalogue entry (owner, resources, dependencies, deploys)."""

from __future__ import annotations

from pydantic import BaseModel, Field

from nightwatch.agent.services import load_services
from nightwatch.agent.tools import Tool, ToolContext, ToolError, ToolResult

# What the evidence keeps: facts about the service. Settings for other features (the SLO, fix
# limits, approvers) stay out, because their numbers aren't observations: the claim guard
# would otherwise accept "99 requests" on the strength of the 99.5% availability target.
FACTS = {
    "name",
    "description",
    "owner",
    "repository",
    "lambda_functions",
    "lambda_alias",
    "log_groups",
    "dynamodb_tables",
    "ssm_parameters",
    "dependencies",
    "deploys",
}


class Args(BaseModel):
    service: str = Field(max_length=100)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    known = load_services()
    if args.service == ctx.service.name:
        info = ctx.service
    elif args.service in known:
        info = known[args.service]
    else:
        raise ToolError(f"no service named '{args.service}' in the catalogue")
    lines = [
        f"Service {info.name}: {info.description or 'no description'}",
        f"Owner: {info.owner or 'unknown'}. Repository: {info.repository or 'unknown'}.",
        f"Lambda functions: {', '.join(info.lambda_functions) or '-'} (alias {info.lambda_alias}). "
        f"Tables: {', '.join(info.dynamodb_tables) or '-'}. "
        f"Parameters: {', '.join(info.ssm_parameters) or '-'}.",
        f"Depends on: {', '.join(info.dependencies) or '-'}.",
        f"Deploys: {info.deploys or 'unknown'}",
    ]
    return ToolResult("\n".join(lines), info.model_dump(include=FACTS))


TOOL = Tool(
    name="get_service_info",
    description="The service's owner, resources, dependencies and how it is deployed.",
    args=Args,
    run=run,
)
