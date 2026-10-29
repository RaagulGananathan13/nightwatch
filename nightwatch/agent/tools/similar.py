"""search_similar_incidents: earlier incidents on the same service.

For now this lists the most recent resolved incidents with their diagnoses. Phase 6 replaces
it with real similarity search over incident memory (spec F6).
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolResult
from nightwatch.chat.cards import duration


class Args(BaseModel):
    text: str = Field(default="", max_length=500)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    earlier = [
        incident
        for incident in ctx.store.list_incidents("resolved", limit=25)
        if incident.service == ctx.incident.service and incident.id != ctx.incident.id
    ][:3]
    if not earlier:
        return ToolResult(
            f"No earlier resolved incidents for {ctx.incident.service}.", {"incidents": []}
        )
    lines = [f"Most recent resolved incidents for {ctx.incident.service}:"]
    data = []
    for incident in earlier:
        took = duration(incident.resolved_at - incident.opened_at) if incident.resolved_at else "?"
        result = incident.investigation_result or {}
        cause = str((result.get("diagnosis") or {}).get("summary", "not diagnosed"))[:200]
        lines.append(
            f"{incident.label} ({incident.opened_at:%Y-%m-%d %H:%M} UTC, resolved after {took}, "
            f"alarms {', '.join(sorted(incident.alarms))}): {cause}"
        )
        data.append({"label": incident.label, "id": incident.id, "cause": cause})
    return ToolResult("\n".join(lines), {"incidents": data})


TOOL = Tool(
    name="search_similar_incidents",
    description="Recent resolved incidents on the same service and what caused them.",
    args=Args,
    run=run,
)
