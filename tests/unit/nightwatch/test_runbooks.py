"""Runbooks are validated like code: allow-listed calls, trusted params, a schema that can't drift."""

from typing import Any

import pytest
from pydantic import ValidationError

from nightwatch.agent.services import service_info
from nightwatch.remediation.runbooks import Runbook, load_runbooks, schema
from tests.unit.test_layout import REPO_ROOT

GOOD: dict[str, Any] = {
    "id": "example-fix",
    "title": "Example",
    "description": "An example runbook.",
    "risk": "low",
    "signal": "deploy",
    "params": {"function_name": {"from": "signal.function"}, "n": {"from": "literal:1"}},
    "preview": "{{function_name}} -> {{n}}",
    "blast_radius": "1 function",
    "action": {
        "service": "lambda",
        "call": "update_alias",
        "args": {"FunctionName": "{{function_name}}", "Name": "live", "FunctionVersion": "{{n}}"},
    },
    "verify": {"namespace": "AWS/Lambda", "metric": "Errors"},
}


def test_the_shipped_runbooks_load_and_shoplite_can_use_them_all() -> None:
    books = load_runbooks()
    assert sorted(books) == [
        "raise-dynamodb-capacity",
        "reset-feature-flags",
        "reset-lambda-concurrency",
        "rollback-lambda-alias",
    ]
    assert sorted(service_info("shoplite").runbooks) == sorted(books)
    assert not any(book.auto_approve for book in books.values())  # every fix waits for a human


def test_schema_file_matches_the_code() -> None:
    on_disk = (REPO_ROOT / "runbooks" / "runbook.schema.json").read_text(encoding="utf-8")
    assert (
        on_disk == schema()
    ), "run: python -m nightwatch.remediation.runbooks > runbooks/runbook.schema.json"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"action": {"service": "lambda", "call": "delete_function", "args": {}}},
            "not an allowed",
        ),
        ({"params": {"function_name": {"from": "diagnosis.resource"}}}, "should match pattern"),
        ({"params": {"function_name": {"from": "signal.function"}}}, "undeclared params: n"),
        ({"risk": "medium", "auto_approve": True}, "only low-risk"),
        ({"surprise": True}, "Extra inputs"),
    ],
)
def test_bad_runbooks_are_refused(change: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Runbook.model_validate({**GOOD, **change})


def test_render_fills_placeholders_and_keeps_integers() -> None:
    book = Runbook.model_validate(GOOD)
    rendered = book.render(
        {"a": "{{function_name}}", "b": ["x {{n}}"], "c": "{{n}}", "d": True},
        {"function_name": "shoplite-api", "n": 5},
    )
    assert rendered == {"a": "shoplite-api", "b": ["x 5"], "c": 5, "d": True}
