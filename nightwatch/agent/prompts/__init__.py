"""Versioned prompts (spec 10.3). A new version is a new file, compared with ``make eval``."""

from __future__ import annotations

import hashlib
from pathlib import Path

PROMPT_VERSION = "investigate_v1"


def load(version: str = PROMPT_VERSION) -> str:
    return (Path(__file__).with_name(f"{version}.md")).read_text(encoding="utf-8")


def fingerprint(text: str) -> str:
    """Short hash recorded in the audit log, so every answer can be traced to its exact prompt."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]
