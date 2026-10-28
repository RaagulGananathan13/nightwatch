"""Phase 0 contract: the repository layout from spec section 15 exists and imports cleanly."""

import importlib
import importlib.metadata
import os
import re
from pathlib import Path

import pytest

import nightwatch

REPO_ROOT = Path(__file__).resolve().parents[2]

SUBPACKAGES = [
    "nightwatch.ingest",
    "nightwatch.agent",
    "nightwatch.agent.tools",
    "nightwatch.agent.providers",
    "nightwatch.remediation",
    "nightwatch.chat",
    "nightwatch.postmortem",
    "nightwatch.memory",
    "nightwatch.coach",
    "nightwatch.predict",
    "nightwatch.mcp_server",
    "nightwatch.common",
]

TOP_LEVEL_DIRS = [
    "infra",
    "shoplite",
    "runbooks",
    "stepfunctions",
    "dashboard",
    "chaos",
    "eval",
    "docs/decisions",
    ".github/workflows",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports_and_says_what_it_is_for(name: str) -> None:
    module = importlib.import_module(name)
    assert module.__doc__, f"{name} needs a docstring explaining its job"


@pytest.mark.parametrize("folder", TOP_LEVEL_DIRS)
def test_top_level_folder_exists(folder: str) -> None:
    assert (REPO_ROOT / folder).is_dir()


def test_version_is_single_sourced() -> None:
    # pyproject.toml reads the version from nightwatch.__version__, so the installed
    # package metadata and the module attribute can never drift apart.
    assert re.fullmatch(r"\d+\.\d+\.\d+", nightwatch.__version__)
    assert importlib.metadata.version("nightwatch") == nightwatch.__version__


def test_unit_tests_run_with_fake_aws_credentials() -> None:
    assert os.environ["AWS_ACCESS_KEY_ID"] == "testing"
    assert "AWS_PROFILE" not in os.environ
