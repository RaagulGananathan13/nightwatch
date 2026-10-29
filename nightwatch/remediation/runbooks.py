"""Runbooks as code (spec F5): YAML files in ``runbooks/``, validated before anything can run.

A runbook names one AWS call from a short allow-list, the parameters it needs, how to
preview the change, how to verify it worked and how to undo it. Parameters can only come from
trusted places:

- ``signal.*``: what the evidence shows, read from AWS APIs (for example the version that was
  live before a deploy);
- ``state.*``: the resource's current state, read when the fix is planned;
- ``service.*``: the service catalogue;
- ``literal:...``: a fixed value.

Never from LLM text, so a prompt injected through a log line can't choose what gets changed.
Templates are plain ``{{name}}`` substitution with no expressions.

    python -m nightwatch.remediation.runbooks > runbooks/runbook.schema.json
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Mapping
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

# The only AWS calls a runbook may make. The remediator's IAM role allows exactly these.
ALLOWED_CALLS: frozenset[tuple[str, str]] = frozenset(
    {
        ("lambda", "update_alias"),
        ("lambda", "delete_function_concurrency"),
        ("ssm", "put_parameter"),
        ("dynamodb", "update_table"),
    }
)
Signal = Literal["deploy", "flag_change", "capacity_drop", "concurrency_block"]

_PLACEHOLDER = re.compile(r"\{\{(\w+)\}\}")
_SOURCE = re.compile(r"^(signal|state|service)\.\w+$|^literal:.*$")
_DIRS = [
    Path(__file__).resolve().parents[1] / "runbooks",  # bundled into the Lambda zip
    Path(__file__).resolve().parents[2] / "runbooks",  # the repo, for local runs and tests
]


class Param(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["string", "integer"] = "string"
    source: str = Field(alias="from", pattern=_SOURCE.pattern)


class Call(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: Literal["lambda", "ssm", "dynamodb"]
    call: str
    args: dict[str, Any]

    @model_validator(mode="after")
    def _allowed(self) -> Call:
        if (self.service, self.call) not in ALLOWED_CALLS:
            raise ValueError(f"{self.service}.{self.call} is not an allowed remediation call")
        return self


class Verify(BaseModel):
    model_config = ConfigDict(extra="forbid")

    namespace: Literal["AWS/Lambda", "AWS/DynamoDB"]
    metric: str
    dimensions: dict[str, str] = Field(default_factory=dict)
    stat: Literal["Sum", "Maximum", "Average"] = "Sum"
    max_value: float = 0.0  # every datapoint after the fix must be at or below this
    minutes: int = Field(default=2, ge=1, le=15)


class Runbook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[a-z0-9-]{3,60}$")
    title: str = Field(max_length=80)
    description: str = Field(max_length=300)
    risk: Literal["low", "medium", "high"]
    auto_approve: bool = False
    signal: Signal  # the evidence pattern this runbook fixes
    params: dict[str, Param]
    preview: str  # exactly what will change, shown on the approval card
    blast_radius: str
    action: Call
    verify: Verify
    rollback: Call | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Runbook:
        if self.auto_approve and self.risk != "low":
            raise ValueError("only low-risk runbooks may skip human approval (spec principle 1)")
        used = _placeholders(self.model_dump(exclude={"params", "id", "title", "description"}))
        missing = used - set(self.params)
        if missing:
            raise ValueError(f"templates use undeclared params: {', '.join(sorted(missing))}")
        return self

    def render(self, template: Any, params: Mapping[str, str | int]) -> Any:
        """Fill ``{{name}}`` placeholders in strings, lists and dicts. A string that is only a
        placeholder takes the value itself, so integer params stay integers."""
        if isinstance(template, str):
            whole = _PLACEHOLDER.fullmatch(template)
            if whole:
                return params[whole.group(1)]
            return _PLACEHOLDER.sub(lambda m: str(params[m.group(1)]), template)
        if isinstance(template, list):
            return [self.render(item, params) for item in template]
        if isinstance(template, dict):
            return {key: self.render(value, params) for key, value in template.items()}
        return template


@cache
def load_runbooks(directory: Path | None = None) -> dict[str, Runbook]:
    folder = directory or next((d for d in _DIRS if d.is_dir()), _DIRS[-1])
    books: dict[str, Runbook] = {}
    for path in sorted(folder.glob("*.yaml")):
        book = Runbook.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if book.id != path.stem:
            raise ValueError(f"{path.name}: id '{book.id}' must match the file name")
        books[book.id] = book
    return books


def _placeholders(value: Any) -> set[str]:
    if isinstance(value, str):
        return set(_PLACEHOLDER.findall(value))
    if isinstance(value, list):
        return set().union(*(_placeholders(item) for item in value)) if value else set()
    if isinstance(value, dict):
        return set().union(*(_placeholders(item) for item in value.values())) if value else set()
    return set()


def schema() -> str:
    """JSON schema for runbook files, for editors and reviewers (spec F5)."""
    return json.dumps(Runbook.model_json_schema(by_alias=True), indent=2) + "\n"


if __name__ == "__main__":
    sys.stdout.write(schema())
