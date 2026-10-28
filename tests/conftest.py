"""Shared pytest setup.

Unit tests must never reach real AWS. Every test gets fake credentials and a fixed region,
so a missing mock fails loudly instead of quietly calling (and paying for) a real service.
"""

import pytest

FAKE_AWS_ENV = {
    "AWS_ACCESS_KEY_ID": "testing",
    "AWS_SECRET_ACCESS_KEY": "testing",
    "AWS_SESSION_TOKEN": "testing",
    "AWS_DEFAULT_REGION": "us-east-2",
    "AWS_REGION": "us-east-2",
}


@pytest.fixture(autouse=True)
def _fake_aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in FAKE_AWS_ENV.items():
        monkeypatch.setenv(name, value)
    # A named profile would override the fake keys above, so drop it for the test run.
    monkeypatch.delenv("AWS_PROFILE", raising=False)
