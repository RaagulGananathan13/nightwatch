"""Contract tests (spec section 16): what other parts read can't change by accident.

The stored investigation (its diagnosis included) is read by the dashboard, the postmortem
writer and the MCP server. Its schema is committed; changing the model means regenerating the
file, so the change shows up in review. Discord's and Slack's signatures have their own tests
(test_approvals.py, test_slack.py).
"""

from pathlib import Path

from nightwatch.agent.models import schema

CONTRACT = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "investigation.schema.json"


def test_the_investigation_schema_is_the_committed_contract() -> None:
    assert (
        CONTRACT.read_text(encoding="utf-8") == schema()
    ), "run: python -m nightwatch.agent.models > docs/contracts/investigation.schema.json"
