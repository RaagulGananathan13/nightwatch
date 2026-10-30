"""Fix-as-pull-request (spec F10): when a Terraform setting is simply too small.

A runbook undoes a change, such as a bad deploy or a flag flip. Sometimes nothing changed and
the setting is just too small for what the service now does:
- read traffic outgrew the table's provisioned capacity;
- the function keeps running out of memory;
- requests keep running into the timeout.

Then the lasting fix is a reviewed Terraform change, not a click in the console that drifts.
Like the runbook planner, detection reads only evidence data from AWS APIs, never the model's
text. Each setting must be listed in the service's ``terraform`` catalogue, with its ceiling.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from nightwatch.agent.models import Evidence
from nightwatch.agent.services import Knob, ServiceInfo
from nightwatch.common.models import Incident

NEAR_LIMIT = 0.95  # "ran into the limit": within 5% of it


@dataclass(frozen=True)
class ConfigFix:
    knob: str  # a key of the service's terraform catalogue
    reason: str
    evidence_ids: list[str]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def detect(evidence: list[Evidence], incident: Incident, service: ServiceInfo) -> ConfigFix | None:
    """A Terraform setting the evidence shows is too small, if nothing else explains it."""
    found = {item.label: item for item in evidence if item.ok and item.label}
    changes = _data(found.get("changes"))
    kinds = {str(change.get("kind")) for change in changes.get("changes", [])}
    flags_on = any(value not in ("", "{}") for value in (changes.get("parameters") or {}).values())
    errors = " ".join(str(r.get("error_type", "")) for r in _rows(found.get("errors_by_version")))
    runtime = " ".join(str(r.get("error", "")) for r in _rows(found.get("runtime_errors")))
    reports = _rows(found.get("lambda_reports"))
    ids = [e.id for e in (found.get(k) for k in ("changes", "errors_by_version")) if e]

    throttled = "ProvisionedThroughputExceeded" in errors or any(
        "dynamodb-throttles" in alarm for alarm in incident.alarms
    )
    if "read_capacity" in service.terraform and throttled and "capacity" not in kinds:
        return ConfigFix(
            "read_capacity",
            "Reads were throttled while nobody changed the table's capacity: the traffic needs "
            "more than the read units Terraform provisions.",
            ids,
        )
    if flags_on or "deploy" in kinds:  # a flag or new code explains it; a runbook can undo that
        return None
    # The version that served the traffic: the one with the most invocations in the window.
    live = max(reports, key=lambda row: _number(row.get("invocations")), default={})
    used, size = _number(live.get("max_memory_mb")), _number(live.get("memory_mb"))
    out_of_memory = any(sign in runtime + errors for sign in ("OutOfMemory", "signal: killed"))
    if "memory" in service.terraform and out_of_memory and size and used >= NEAR_LIMIT * size:
        return ConfigFix(
            "memory",
            f"The function used {used:g} of its {size:g} MB and ran out of memory, with no "
            "recent deploy or flag to blame.",
            ids + [e.id for e in (found.get("lambda_reports"),) if e],
        )
    timeout = _number(_data(found.get("resource")).get("timeout_s"))
    if "timeout" in service.terraform and "Task timed out" in runtime and timeout:
        slowest = _number(live.get("max_ms"))
        if slowest >= NEAR_LIMIT * timeout * 1000:
            return ConfigFix(
                "timeout",
                f"Requests ran into the {timeout:g}-second timeout (slowest {slowest:g} ms) "
                "with no recent deploy or flag to blame.",
                ids + [e.id for e in (found.get("lambda_reports"),) if e],
            )
    return None


def read_value(text: str, knob: Knob) -> int:
    start, end = _block(text, knob)
    match = _attribute(knob).search(text, start, end)
    if not match:
        raise ValueError(f"{knob.attribute} isn't set in {knob.block}")
    return int(match.group(2))


def set_value(text: str, knob: Knob, value: int) -> str:
    """The same file with only that one number changed."""
    start, end = _block(text, knob)
    match = _attribute(knob).search(text, start, end)
    if not match:
        raise ValueError(f"{knob.attribute} isn't set in {knob.block}")
    return text[: match.start(2)] + str(value) + text[match.end(2) :]


def proposal(current: int, knob: Knob) -> int:
    """Double it, up to the catalogue's ceiling."""
    return min(current * 2, knob.max)


def _block(text: str, knob: Knob) -> tuple[int, int]:
    """Where the top-level block starts and ends (terraform fmt puts its "}" in column 0)."""
    header = re.compile(rf"^{re.escape(knob.block)}\s*\{{\s*$", re.MULTILINE)
    found = header.search(text)
    if not found:
        raise ValueError(f"{knob.file} has no {knob.block} block")
    end = text.find("\n}", found.end())
    return found.end(), (end if end != -1 else len(text))


def _attribute(knob: Knob) -> re.Pattern[str]:
    # A top-level attribute of the block: two spaces of indent, as terraform fmt writes it.
    return re.compile(rf"^(  {re.escape(knob.attribute)}\s*=\s*)(\d+)\b", re.MULTILINE)


def _data(item: Evidence | None) -> dict[str, Any]:
    return item.data if item and isinstance(item.data, dict) else {}


def _rows(item: Evidence | None) -> list[dict[str, Any]]:
    rows = _data(item).get("rows")
    return rows if isinstance(rows, list) else []


def _number(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
