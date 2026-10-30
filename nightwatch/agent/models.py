"""What the agent produces: the evidence it gathered and the diagnosis it wrote (spec F3, F4).

LLM output is untrusted and often slightly off-schema, so the models here are forgiving where
that's safe (long text is cut, "85" is read as 0.85, "[E3]" as "E3") and strict where it
matters (only the two decision shapes are accepted).
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field


def _cut(limit: int) -> BeforeValidator:
    return BeforeValidator(lambda v: str(v)[:limit] if v is not None else "")


def _confidence(value: Any) -> float:
    number = float(value)
    if 1 < number <= 100:  # "85" means 85%
        number /= 100
    return max(0.0, min(1.0, number))


def _evidence_ids(value: Any) -> list[str]:
    items = value if isinstance(value, list) else [value]
    ids: list[str] = []
    for item in items:
        for match in re.findall(r"[Ee](\d{1,3})", str(item)):
            ident = f"E{int(match)}"
            if ident not in ids:
                ids.append(ident)
    return ids[:8]


def _short_list(limit: int, width: int) -> BeforeValidator:
    return BeforeValidator(
        lambda v: [str(x)[:width] for x in (v if isinstance(v, list) else [v])][:limit] if v else []
    )


class Evidence(BaseModel):
    """One tool result, numbered E1, E2, ... in the order it was gathered (spec F4)."""

    id: str
    tool: str
    label: str = ""  # what deterministic evidence is for, e.g. "errors_by_version"
    args: dict[str, Any] = Field(default_factory=dict)
    summary: str  # short text the LLM and the chat card see
    data: Any = None  # structured result, truncated
    ok: bool = True
    requested_by: Literal["plan", "llm"] = "plan"
    started_at: datetime
    duration_ms: int = 0

    def brief(self, width: int = 150) -> str:
        first = self.summary.strip().splitlines()[0] if self.summary.strip() else self.tool
        return first if len(first) <= width else first[: width - 1] + "…"


class Hypothesis(BaseModel):
    statement: Annotated[str, _cut(500)]
    confidence: Annotated[float, BeforeValidator(_confidence)] = 0.5
    evidence_ids: Annotated[list[str], BeforeValidator(_evidence_ids)] = Field(default_factory=list)
    verified: bool = True  # set by the evidence guard, never by the model
    notes: list[str] = Field(default_factory=list)  # why the guard downgraded it


class SuggestedAction(BaseModel):
    description: Annotated[str, _cut(300)]
    runbook_id: str | None = None  # runbooks arrive in Phase 4
    risk: Literal["low", "medium", "high"] = "medium"


class Diagnosis(BaseModel):
    summary: Annotated[str, _cut(800)]
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=5)
    impact: Annotated[str, _cut(400)] = ""
    suggested_actions: list[SuggestedAction] = Field(default_factory=list, max_length=5)
    unknowns: Annotated[list[str], _short_list(6, 300)] = Field(default_factory=list)


class ToolRequest(BaseModel):
    action: Literal["tool"]
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    why: Annotated[str, _cut(300)] = ""


class FinalAnswer(BaseModel):
    action: Literal["final"]
    diagnosis: Diagnosis


AgentDecision = Annotated[ToolRequest | FinalAnswer, Field(discriminator="action")]


class Investigation(BaseModel):
    """The stored result of one investigation: diagnosis plus how it was produced."""

    diagnosis: Diagnosis
    method: Literal["llm", "rules"]
    provider: str | None = None
    model: str | None = None
    llm_calls: int = 0
    tokens: int = 0
    duration_ms: int = 0
    prompt_version: str = ""
    fallback_reason: str | None = None
    evidence: list[dict[str, str]] = Field(default_factory=list)  # [{"id", "tool", "brief"}]
    # Earlier incidents incident memory found (spec F6), straight from the evidence, not the LLM.
    similar: list[dict[str, Any]] = Field(default_factory=list)
    # A Terraform setting the evidence shows is too small (spec F10); a fix PR follows.
    config_fix: dict[str, Any] | None = None
