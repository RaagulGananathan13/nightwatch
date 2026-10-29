"""Choose the runbook that fixes what the evidence shows (spec F5), never what the LLM says.

Evidence (AWS API results gathered by the read-only tools) decides *what* can be fixed: a
deploy that went live shortly before the incident, a feature-flag change, a capacity drop, or
a function throttled to zero. The diagnosis decides only *whether*: a hypothesis with at least
50% confidence must cite that evidence, or the LLM must name the runbook. A named runbook is
a hint for choosing between signals and nothing more. Parameters come from evidence data, the
service catalogue, the resource's current state, or the runbook itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from nightwatch.agent.models import Diagnosis, Evidence
from nightwatch.agent.services import ServiceInfo
from nightwatch.common.timeutil import from_iso
from nightwatch.remediation.calls import already_done, check_target, read_state
from nightwatch.remediation.runbooks import Runbook, load_runbooks

RECENT = timedelta(minutes=30)  # a change older than this is unlikely to be the cause
MIN_CONFIDENCE = 0.5  # unverified hypotheses are capped at 0.3, so this also means verified


@dataclass(frozen=True)
class Signal:
    kind: str  # matches a runbook's `signal`
    values: dict[str, str]  # what runbook params may read as signal.<name>
    evidence_id: str
    at: datetime | None
    reason: str


@dataclass(frozen=True)
class Proposal:
    runbook: Runbook
    params: dict[str, str | int]
    call: dict[str, Any]
    rollback: dict[str, Any] | None
    verify: dict[str, Any]
    preview: str
    blast_radius: str
    before: dict[str, str]
    reason: str
    evidence_id: str


def plan(
    evidence: list[Evidence],
    diagnosis: Diagnosis,
    service: ServiceInfo,
    opened_at: datetime,
    aws: Any,
    books: dict[str, Runbook] | None = None,
) -> Proposal | None:
    books = load_runbooks() if books is None else books
    enabled = [books[name] for name in service.runbooks if name in books]
    cited = {
        evidence_id
        for hypothesis in diagnosis.hypotheses
        if hypothesis.confidence >= MIN_CONFIDENCE
        for evidence_id in hypothesis.evidence_ids
    }
    hinted = {action.runbook_id for action in diagnosis.suggested_actions if action.runbook_id}
    options = [
        (book.id in hinted, signal.at or opened_at, book, signal)
        for signal in signals(evidence, service, opened_at)
        for book in enabled
        if book.signal == signal.kind and (signal.evidence_id in cited or book.id in hinted)
    ]
    # The LLM's pick first, then the most recent change.
    for _, _, book, signal in sorted(options, key=lambda o: (o[0], o[1]), reverse=True):
        proposal = prepare(book, signal, service, aws)
        if proposal:
            return proposal
    return None


def signals(evidence: list[Evidence], service: ServiceInfo, opened_at: datetime) -> list[Signal]:
    """Fixable changes found in the structured results of recent_changes and describe_resource."""
    found: list[Signal] = []
    for item in evidence:
        if not item.ok or not isinstance(item.data, dict):
            continue
        if item.tool == "recent_changes":
            for change in item.data.get("changes", []):
                signal = _from_change(change, item.id, service, opened_at)
                if signal:
                    found.append(signal)
        elif item.tool == "describe_resource" and item.data.get("reserved_concurrency") == 0:
            name = str(item.args.get("name", ""))
            if name in service.lambda_functions:
                reason = f"{item.id}: {name} has reserved concurrency 0, so every call is throttled"
                found.append(Signal("concurrency_block", {"function": name}, item.id, None, reason))
    return found


def prepare(book: Runbook, signal: Signal, service: ServiceInfo, aws: Any) -> Proposal | None:
    """Fill the runbook's params and render every template. None if it doesn't apply."""
    known = {
        "name": service.name,
        "lambda_alias": service.lambda_alias,
        "function": service.function,
    }
    params: dict[str, str | int] = {}
    for name, param in book.params.items():
        source, _, key = param.source.partition(".")
        if param.source.startswith("literal:"):
            value: str | None = param.source.removeprefix("literal:")
        elif source == "signal":
            value = signal.values.get(key)
        elif source == "service":
            value = known.get(key)
        else:
            continue  # state.*, read below once we know the target
        typed = _typed(value, param.type)
        if typed is None:
            return None
        params[name] = typed

    # The action itself may not depend on current state: state is for previews and undo.
    call = book.render(book.action.model_dump(), params)
    check_target(service, call)
    before = read_state(aws, call)
    if already_done(call, before):
        return None
    for name, param in book.params.items():
        if param.source.startswith("state."):
            typed = _typed(before.get(param.source.removeprefix("state.")), param.type)
            if typed is None:
                return None
            params[name] = typed
    rollback = book.render(book.rollback.model_dump(), params) if book.rollback else None
    if rollback:
        check_target(service, rollback)
    return Proposal(
        runbook=book,
        params=params,
        call=call,
        rollback=rollback,
        verify=book.render(book.verify.model_dump(), params),
        preview=book.render(book.preview, params),
        blast_radius=book.render(book.blast_radius, params),
        before=before,
        reason=signal.reason,
        evidence_id=signal.evidence_id,
    )


def _from_change(
    change: dict[str, Any], evidence_id: str, service: ServiceInfo, opened_at: datetime
) -> Signal | None:
    try:
        at = from_iso(str(change.get("at")))
    except ValueError:
        return None
    if not opened_at - RECENT <= at <= opened_at + timedelta(minutes=5):
        return None
    kind, resource = change.get("kind"), str(change.get("resource", ""))
    when = f"{_minutes(opened_at - at)} before the incident"
    rollback_to = str(change.get("rollback_to", ""))
    if kind == "deploy" and change.get("live") and rollback_to.isdigit():
        if resource not in service.lambda_functions:
            return None
        reason = (
            f"{evidence_id}: {resource} version {change.get('version')} went live {when}; "
            f"version {rollback_to} was live before it"
        )
        values = {"function": resource, "rollback_to": rollback_to}
        return Signal("deploy", values, evidence_id, at, reason)
    if kind == "config" and resource in service.ssm_parameters:
        reason = f"{evidence_id}: {resource} was changed {when}"
        return Signal("flag_change", {"parameter": resource}, evidence_id, at, reason)
    if kind == "capacity" and "lowered" in str(change.get("summary", "")):
        if resource not in service.dynamodb_tables:
            return None
        reason = f"{evidence_id}: table {resource} capacity was lowered {when}"
        return Signal("capacity_drop", {"table": resource}, evidence_id, at, reason)
    return None


def _typed(value: str | None, kind: str) -> str | int | None:
    if value is None or value == "":
        return None
    if kind == "integer":
        return int(value) if value.isdigit() else None
    return value


def _minutes(span: timedelta) -> str:
    seconds = max(0, int(span.total_seconds()))
    return f"{seconds // 60}m {seconds % 60:02d}s"
