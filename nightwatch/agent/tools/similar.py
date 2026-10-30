"""search_similar_incidents: earlier incidents that looked like this one (spec F6).

Searches incident memory: every resolved incident is remembered with its cause and its fix.
The investigation searches with what it has already seen (the alarms, what changed, the error
types), so a repeat of an earlier failure finds that earlier incident first.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from nightwatch.agent.tools import Tool, ToolContext, ToolResult
from nightwatch.chat.cards import duration
from nightwatch.memory.store import MemoryStore


class Args(BaseModel):
    text: str = Field(default="", max_length=3000)


def run(ctx: ToolContext, args: Args) -> ToolResult:
    store = MemoryStore(ctx.store.table, ctx.store.tenant, ctx.store.client)
    text = args.text or ctx.incident.title
    matches = store.search(ctx.incident.service, text, ctx.now, exclude=ctx.incident.id)
    if not matches:
        return ToolResult(
            f"No earlier {ctx.incident.service} incident in memory looks like this one.",
            {"incidents": []},
        )
    lines = [
        f"Earlier {ctx.incident.service} incidents that look like this one, most similar first:"
    ]
    data = []
    for match in matches:
        memory = match.memory
        when = ago(ctx.now, memory.resolved_at or memory.opened_at)
        lines.append(
            f"{memory.label}, {when} (similarity {match.score:.2f}): {memory.cause} "
            f"Fix: {memory.fix}."
        )
        data.append(
            {
                "label": memory.label,
                "id": memory.incident_id,
                "score": match.score,
                "cause": memory.cause,
                "fix": memory.fix,
                "when": when,
            }
        )
    return ToolResult("\n".join(lines), {"incidents": data})


def ago(now: datetime, then: datetime) -> str:
    return f"{duration(now - then)} ago"


TOOL = Tool(
    name="search_similar_incidents",
    description=(
        "Earlier incidents on the same service that look like this one, with what caused them "
        "and what fixed them. 'text' describes what to look for."
    ),
    args=Args,
    run=run,
)
