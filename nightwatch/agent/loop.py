"""The investigation loop (spec 10.1, ADR 0003): evidence first, LLM second, guard last.

1. Gather cheap, high-signal evidence deterministically: service, alarms, what changed,
   earlier incidents, then aggregated logs, metrics and the function's configuration.
2. Ask the LLM once with all of it. It may request a couple more read-only tools.
3. Check the answer with the evidence guard. If no LLM is available, write a rule-based
   diagnosis from the same evidence, so an incident is never left without one.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import TypeAdapter, ValidationError

from nightwatch.agent import guard, prompts
from nightwatch.agent.evidence import EvidenceLedger
from nightwatch.agent.models import AgentDecision, Diagnosis, Evidence, FinalAnswer, Investigation
from nightwatch.agent.providers.base import LLMResponse, LLMUnavailableError, Message
from nightwatch.agent.providers.chain import ProviderChain
from nightwatch.agent.rules import rule_based
from nightwatch.agent.tools import ToolContext, catalog
from nightwatch.agent.tools.logs import (
    ERRORS_BY_VERSION,
    LAMBDA_REPORTS,
    REQUESTS_BY_VERSION,
    RUNTIME_ERRORS,
)
from nightwatch.common.audit import AuditLog
from nightwatch.common.timeutil import to_iso

_DECISION: TypeAdapter[Any] = TypeAdapter(AgentDecision)
_RETRY = "That reply was not one JSON object in either shape. Reply again with only the JSON."
# The first log queries cover this long before the incident. Longer windows pull in errors from
# earlier, unrelated incidents (the evaluation showed one outweighing a new deploy's errors).
LOG_LOOKBACK_MINUTES = 10
_NO_MORE_TOOLS = 'No more tools are available. Reply now with "action": "final".'
_LAST_TURN = '\n\nThis is your last turn: reply with "action": "final".'


@dataclass
class Budget:
    extra_tool_calls: int = 2  # tools the model may request on top of the plan
    llm_calls: int = 3
    seconds: float = 90.0


@dataclass
class Investigator:
    ctx: ToolContext
    ledger: EvidenceLedger
    llm: ProviderChain | None
    audit: AuditLog
    budget: Budget = field(default_factory=Budget)
    clock: Callable[[], float] = time.monotonic
    _calls: int = 0
    _tokens: int = 0
    _last: LLMResponse | None = None

    def run(self) -> Investigation:
        started = self.clock()
        for tool, args, label in initial_plan(self.ctx):
            if tool == "search_similar_incidents":  # search with what has been seen so far
                args = {"text": memory_query(self.ctx, self.ledger.items)}
            self.ledger.gather(self.ctx, tool, args, label)
        if self.llm is None:
            return self._rules(started, "no LLM provider is configured")

        messages = build_messages(self.ctx, self.ledger.items, self.budget.extra_tool_calls)
        extra_tools = 0
        for call in range(self.budget.llm_calls):
            last = call == self.budget.llm_calls - 1 or self.clock() - started > self.budget.seconds
            if last:
                messages[-1] = {**messages[-1], "content": messages[-1]["content"] + _LAST_TURN}
            try:
                response = self.llm.complete(messages, max_tokens=1800)
            except LLMUnavailableError as exc:
                return self._rules(started, f"LLM unavailable: {exc}")
            self._record(response, messages)
            decision = parse_decision(response.text)
            messages.append({"role": "assistant", "content": response.text[:4000]})
            if isinstance(decision, FinalAnswer):
                checked = guard.check(decision.diagnosis, self.ledger.by_id())
                return self._result(checked, "llm", started)
            if decision is None:
                messages.append({"role": "user", "content": _RETRY})
                continue
            if last or extra_tools >= self.budget.extra_tool_calls:
                messages.append({"role": "user", "content": _NO_MORE_TOOLS})
                continue
            extra_tools += 1
            found = self.ledger.gather(self.ctx, decision.tool, decision.args, requested_by="llm")
            messages.append(
                {
                    "role": "user",
                    "content": evidence_block(found) + "\n\nReturn the JSON object now.",
                }
            )
        return self._rules(started, "the model did not produce a valid diagnosis")

    def _record(self, response: LLMResponse, messages: list[Message]) -> None:
        self._calls += 1
        self._tokens += response.tokens
        self._last = response
        self.audit.record(
            "llm_call",
            self.ctx.incident.id,
            provider=response.provider,
            model=response.model,
            prompt_version=prompts.PROMPT_VERSION,
            prompt_sha=prompts.fingerprint(json.dumps(messages)),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency_ms=response.latency_ms,
        )

    def _rules(self, started: float, reason: str) -> Investigation:
        diagnosis = rule_based(self.ledger.items)
        return self._result(guard.check(diagnosis, self.ledger.by_id()), "rules", started, reason)

    def _result(
        self, diagnosis: Diagnosis, method: str, started: float, reason: str | None = None
    ) -> Investigation:
        return Investigation.model_validate(
            {
                "diagnosis": diagnosis,
                "method": method,
                "provider": self._last.provider if self._last else None,
                "model": self._last.model if self._last else None,
                "llm_calls": self._calls,
                "tokens": self._tokens,
                "duration_ms": int((self.clock() - started) * 1000),
                "prompt_version": prompts.PROMPT_VERSION,
                "fallback_reason": reason[:300] if reason else None,
                "evidence": [
                    {"id": e.id, "tool": e.tool, "brief": e.brief()} for e in self.ledger.items
                ],
                "similar": next(
                    (
                        e.data.get("incidents", [])
                        for e in reversed(self.ledger.items)
                        if e.tool == "search_similar_incidents" and isinstance(e.data, dict)
                    ),
                    [],
                ),
            }
        )


def initial_plan(ctx: ToolContext) -> list[tuple[str, dict[str, Any], str]]:
    """The deterministic evidence, in the order a human SRE would look (spec F3)."""
    service, incident = ctx.service, ctx.incident
    steps: list[tuple[str, dict[str, Any], str]] = [
        ("get_service_info", {"service": service.name}, "service")
    ]
    steps += [
        ("get_alarm_context", {"alarm_name": name}, "alarm") for name in sorted(incident.alarms)[:3]
    ]
    steps.append(("recent_changes", {"window_minutes": 60}, "changes"))
    if service.log_groups:
        group = service.log_groups[0]
        for query, label in (
            (ERRORS_BY_VERSION, "errors_by_version"),
            (REQUESTS_BY_VERSION, "requests_by_version"),
            (RUNTIME_ERRORS, "runtime_errors"),
            (LAMBDA_REPORTS, "lambda_reports"),
        ):
            args = {"log_group": group, "query": query, "minutes": LOG_LOOKBACK_MINUTES}
            steps.append(("query_logs", args, label))
    for metric in (incident_metrics(ctx) or default_metrics(ctx))[:3]:
        steps.append(("get_metric_series", metric, "metric"))
    if service.lambda_functions:
        steps.append(("describe_resource", {"name": service.lambda_functions[0]}, "resource"))
    # Last, so the search can use what the evidence shows (Investigator.run fills the text).
    steps.append(("search_similar_incidents", {"text": incident.title}, "similar"))
    return steps


def default_metrics(ctx: ToolContext) -> list[dict[str, Any]]:
    """Without alarms (a question in chat), look at the main function's errors and speed."""
    if not ctx.service.function:
        return []
    dimensions = {"FunctionName": ctx.service.function}
    return [
        {"namespace": "AWS/Lambda", "metric": "Errors", "dimensions": dimensions, "stat": "Sum"},
        {"namespace": "AWS/Lambda", "metric": "Duration", "dimensions": dimensions, "stat": "p95"},
        {"namespace": "AWS/Lambda", "metric": "Throttles", "dimensions": dimensions, "stat": "Sum"},
    ]


def memory_query(ctx: ToolContext, items: list[Evidence]) -> str:
    """What to look for in incident memory: the incident plus what changed and what failed."""
    wanted = ("changes", "errors_by_version", "runtime_errors")
    seen = [item.summary[:700] for item in items if item.ok and item.label in wanted]
    alarms = ", ".join(sorted(ctx.incident.alarms))
    return " ".join([ctx.question or ctx.incident.title, alarms, *seen])[:3000]


def incident_metrics(ctx: ToolContext) -> list[dict[str, Any]]:
    """The metrics the incident's alarms watch, without duplicates."""
    seen: list[dict[str, Any]] = []
    for alert in ctx.store.alerts(ctx.incident.id):
        for metric in alert.get("metrics") or []:
            entry = {
                "namespace": metric.get("namespace", ""),
                "metric": metric.get("name", ""),
                "dimensions": metric.get("dimensions") or {},
                "stat": metric.get("stat") or "Sum",
            }
            if entry["namespace"] and entry not in seen:
                seen.append(entry)
    return seen


def build_messages(ctx: ToolContext, items: list[Evidence], extra_tools: int) -> list[Message]:
    system = (
        prompts.load()
        .replace("{max_tool_calls}", str(extra_tools))
        .replace("{tool_catalog}", catalog())
    )
    incident = ctx.incident
    alarms = ", ".join(f"{name} ({state})" for name, state in sorted(incident.alarms.items()))
    brief = (
        f'Incident {incident.label} on service "{incident.service}", severity {incident.severity}, '
        f"opened {to_iso(incident.opened_at)}.\nAlarms: {alarms}. Firing alerts grouped: "
        f"{incident.alert_count}.\nTitle: {_escape(incident.title)}\n"
        f"Investigation started {to_iso(ctx.now)}."
    )
    if ctx.question:  # /nw ask: no incident, a question
        brief = (
            f'An engineer asked about service "{incident.service}" at {to_iso(ctx.now)}:\n'
            f"<question>{_escape(ctx.question)}</question>\n"
            "Answer it from the evidence, in the same JSON shape: the summary is your answer, "
            "and the hypotheses are your findings, each with its evidence."
        )
    evidence = "\n".join(evidence_block(item) for item in items)
    user = f"{brief}\n\nEvidence:\n{evidence}\n\nReturn the JSON object now."
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def evidence_block(item: Evidence) -> str:
    """Untrusted tool output, fenced so it can't pose as instructions (spec 10.3)."""
    body = _escape(item.summary)
    if item.tool == "query_logs" and item.ok:
        body = f"<log>\n{body}\n</log>"
    return f'<tool_result id="{item.id}" tool="{item.tool}" ok="{str(item.ok).lower()}">\n{body}\n</tool_result>'


def parse_decision(text: str) -> Any:
    """The model's reply as a ToolRequest or FinalAnswer, or None if it isn't one."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if isinstance(raw, dict) and "action" not in raw and "hypotheses" in raw:
        raw = {"action": "final", "diagnosis": raw}  # a bare diagnosis is still an answer
    try:
        return _DECISION.validate_python(raw)
    except ValidationError:
        return None


def _escape(text: str) -> str:
    # Swap angle brackets for look-alike quote marks so tool output can't open or close our tags.
    return text.replace("<", chr(0x2039)).replace(">", chr(0x203A))
