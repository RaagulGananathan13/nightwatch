"""Two safety checks around the LLM (spec F4 and section 11).

``check`` runs after the model answers. Every hypothesis must cite evidence that exists, and
every number, version or commit SHA it states must appear in that evidence. Anything
unsupported is marked "unverified" and its confidence is capped, so a hallucination can't
pass as a finding.

``redact`` runs before evidence reaches an external LLM. It removes emails, keys, tokens, IP
addresses and card-like numbers, so customer data and secrets never leave the account.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from nightwatch.agent.models import Diagnosis, Evidence, Hypothesis

UNVERIFIED_CONFIDENCE_CAP = 0.3

# Numbers worth checking: 15, 15.5, 92%, 22:00:07. Lookarounds skip numbers glued to words,
# like E3, p95, INC-002 or 5xx; single digits are too generic to check.
_NUMBER = re.compile(r"(?<![\w\-.:])(\d{2,}(?:[.:]\d+)*|\d+\.\d+)(%?)(?![\w.:]*[A-Za-z])")
_SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def check(diagnosis: Diagnosis, evidence: Mapping[str, Evidence]) -> Diagnosis:
    """Verify each hypothesis against its cited evidence. Returns a new, checked diagnosis."""
    checked = [_check_one(h, evidence) for h in diagnosis.hypotheses]
    # Verified findings first, then by confidence.
    checked.sort(key=lambda h: (not h.verified, -h.confidence))
    return diagnosis.model_copy(update={"hypotheses": checked})


def _check_one(hypothesis: Hypothesis, evidence: Mapping[str, Evidence]) -> Hypothesis:
    notes: list[str] = []
    cited = [i for i in hypothesis.evidence_ids if i in evidence]
    unknown = [i for i in hypothesis.evidence_ids if i not in evidence]
    if unknown:
        notes.append(f"cited evidence that doesn't exist: {', '.join(unknown)}")
    if not cited:
        notes.append("no evidence cited")
    else:
        text = " ".join(_text(evidence[i]) for i in cited)
        values = [float(n) for n in _ANY_NUMBER.findall(text)]
        lowered = text.lower()
        for sha in _SHA.findall(hypothesis.statement.lower()):
            if sha not in lowered:
                notes.append(f"'{sha}' is not in the cited evidence")
        for number, percent in _NUMBER.findall(hypothesis.statement):
            if _SHA.fullmatch(number) or _supported(number, percent, text, values):
                continue
            notes.append(f"'{number}{percent}' is not in the cited evidence")
    verified = bool(cited) and not any("not in the cited evidence" in n for n in notes)
    confidence = (
        hypothesis.confidence if verified else min(hypothesis.confidence, UNVERIFIED_CONFIDENCE_CAP)
    )
    return hypothesis.model_copy(
        update={
            "evidence_ids": cited,
            "verified": verified,
            "notes": notes,
            "confidence": confidence,
        }
    )


def _supported(number: str, percent: str, text: str, values: list[float]) -> bool:
    if number + percent in text or number in text:
        return True
    if ":" in number:  # a time of day must match exactly
        return False
    target = float(number)
    candidates = [target, target / 100] if percent else [target]
    return any(
        abs(value - want) <= max(0.051, abs(want) * 0.02) for want in candidates for value in values
    )


def _text(item: Evidence) -> str:
    return item.summary + " " + json.dumps(item.data, default=str)


# ---------------------------------------------------------------------- redaction

_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "[aws-key]"),
    (re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S+"), "aws_secret_access_key=[secret]"),
    (re.compile(r"\beyJ[\w-]+\.[\w-]+\.[\w-]+"), "[jwt]"),
    (re.compile(r"\bgsk_[A-Za-z0-9]{20,}"), "[api-key]"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "[github-token]"),
    (re.compile(r"https://(?:discord|discordapp)\.com/api/webhooks/\S+"), "[discord-webhook]"),
    (re.compile(r"(?i)\bbearer\s+[\w.~+/-]{16,}=*"), "Bearer [token]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|token|secret|password|passwd)(\"?\s*[:=]\s*\"?)[^\s\"',}]{6,}"
        ),
        r"\1\2[secret]",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[email]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"\+\d{1,3}[\s-]?\(?\d{2,4}\)?[\s-]?\d{3,4}[\s-]?\d{3,4}\b"), "[phone]"),
]
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")


def redact(value: Any) -> Any:
    """Redact strings anywhere inside ``value`` (strings, lists, dicts)."""
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, dict):
        return {key: redact(item) for key, item in value.items()}
    return value


def _redact_text(text: str) -> str:
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return _CARD.sub(lambda m: "[card]" if _luhn(m.group()) else m.group(), text)


def _luhn(candidate: str) -> bool:
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 19:
        return False
    total = 0
    for index, digit in enumerate(reversed(digits)):
        if index % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0
