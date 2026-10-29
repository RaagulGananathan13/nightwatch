"""Two safety checks around the LLM (spec F4 and section 11).

``check`` runs after the model answers. Every hypothesis must cite evidence that exists, and
every number, version or commit SHA it states must appear in the evidence. If a fact is in
evidence the agent gathered but cited from the wrong item, the guard adds the right source
and notes it. A fact found nowhere marks the hypothesis "unverified" and caps its confidence,
so a hallucination can't pass as a finding.

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

# Numbers worth checking: 15, 15.5, 2,954, 92%, 22:00:07. Lookarounds skip numbers glued to
# words, like E3, p95, INC-002 or 5xx; single digits are too generic to check.
_NUMBER = re.compile(
    r"(?<![\w\-.:,])(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,}(?:[.:]\d+)*|\d+\.\d+)(%?)"
    r"(?![\w.:]*[A-Za-z])"
)
_SHA = re.compile(r"\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b")
_ANY_NUMBER = re.compile(r"\d+(?:\.\d+)?")
# Parts of evidence that contain digits but aren't quantities, so they can't back a count.
_NOT_QUANTITIES = re.compile(
    r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?|\b[0-9a-f]*[a-f][0-9a-f]*\d[0-9a-f]*\b"
    r"|\b[0-9a-f]*\d[0-9a-f]*[a-f][0-9a-f]*\b"
)


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
    unsupported: list[str] = []
    if not cited:
        notes.append("no evidence cited")
    else:
        for claim in _claims(hypothesis.statement):
            if any(_backs(evidence[i], claim) for i in cited):
                continue
            # Citation repair: a fact the agent did gather, just cited from the wrong item, is
            # not invented. Add its source and say so. A fact found nowhere stays unverified.
            source = next((e.id for e in evidence.values() if _backs(e, claim)), None)
            if source is None:
                unsupported.append(claim)
                notes.append(f"'{claim}' is not in any gathered evidence")
            else:
                cited.append(source)
                notes.append(f"added {source}, which contains '{claim}'")
    verified = bool(cited) and not unsupported
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


def _claims(statement: str) -> list[str]:
    """The checkable facts in a statement: commit SHAs, then numbers (with their % sign)."""
    shas = _SHA.findall(statement.lower())
    numbers = [n + p for n, p in _NUMBER.findall(statement) if not _SHA.fullmatch(n)]
    return [*shas, *numbers]


def _backs(item: Evidence, claim: str) -> bool:
    text = _text(item)
    if _SHA.fullmatch(claim):
        return claim in text.lower()
    number, percent = (claim[:-1], "%") if claim.endswith("%") else (claim, "")
    if ":" in number:  # a time of day must appear exactly
        return number in text
    quantities = _NOT_QUANTITIES.sub(" ", text.lower())
    values = [float(n) for n in _ANY_NUMBER.findall(quantities.replace(",", ""))]
    return _supported(number.replace(",", ""), percent, values)


def _supported(number: str, percent: str, values: list[float]) -> bool:
    """Does any evidence value round to the number as stated? "92%" accepts 92.3 or 0.923."""
    decimals = len(number.split(".")[1]) if "." in number else 0
    slack = 0.5 * 10**-decimals + 1e-9
    target = float(number)
    if percent:
        return any(abs(v - target) <= slack or abs(v * 100 - target) <= slack for v in values)
    return any(abs(v - target) <= slack for v in values)


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
