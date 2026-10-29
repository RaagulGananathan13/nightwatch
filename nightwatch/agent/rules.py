"""A rule-based diagnosis, used when no LLM can answer (ADR 0003).

It reads the same deterministic evidence the LLM would get and applies the checks an SRE makes
first: did a deploy just go live with most errors coming from it, did a setting or capacity
change, and what are the requests failing with. Every statement uses numbers copied from the
evidence, so the guard can verify it. The eval harness also uses it as a baseline the LLM has
to beat.
"""

from __future__ import annotations

from typing import Any

from nightwatch.agent.models import Diagnosis, Evidence, Hypothesis


def rule_based(evidence: list[Evidence]) -> Diagnosis:
    labelled = {item.label: item for item in evidence if item.ok and item.label}
    changes_ev = labelled.get("changes")
    errors_ev = labelled.get("errors_by_version")
    runtime_ev = labelled.get("runtime_errors")
    changes: list[dict[str, Any]] = (changes_ev.data or {}).get("changes", []) if changes_ev else []
    rows: list[dict[str, Any]] = (errors_ev.data or {}).get("rows", []) if errors_ev else []
    total = sum(_int(row.get("errors")) for row in rows)

    hypotheses: list[Hypothesis] = []
    deploys = [c for c in changes if c.get("kind") == "deploy" and c.get("live")]
    if deploys and changes_ev:
        deploy = deploys[-1]
        sha = str(deploy.get("sha") or "")
        mine = [r for r in rows if sha and str(r.get("version", "")).startswith(sha[:12])]
        statement = (
            f"Deploy of {deploy['resource']} version {deploy['version']} "
            f"({deploy.get('description', '')}) went live shortly before the incident"
        )
        ids = [changes_ev.id]
        confidence = 0.5
        if total and mine and errors_ev:
            # Quote only numbers that appear in the evidence, so the guard can verify them.
            biggest = max(mine, key=lambda row: _int(row.get("errors")))
            statement += (
                f"; {biggest.get('errors')} {biggest.get('error_type', 'error')} errors come "
                f"from version {sha} ({total} errors in all)"
            )
            ids.append(errors_ev.id)
            share = sum(_int(r.get("errors")) for r in mine) / total
            confidence = 0.75 if share >= 0.8 else 0.55
        hypotheses.append(
            Hypothesis(statement=statement + ".", confidence=confidence, evidence_ids=ids)
        )
    for change in [c for c in changes if c.get("kind") in ("config", "capacity")][-2:]:
        if changes_ev:
            hypotheses.append(
                Hypothesis(
                    statement=f"A change shortly before the incident: {change['summary']}.",
                    confidence=0.5,
                    evidence_ids=[changes_ev.id],
                )
            )
    if rows and errors_ev:
        top = max(rows, key=lambda row: _int(row.get("errors")))
        hypotheses.append(
            Hypothesis(
                statement=(
                    f"Failing requests raise {top.get('error_type', 'an error')} "
                    f"({top.get('errors')} errors in version {top.get('version', '?')})."
                ),
                confidence=0.4,
                evidence_ids=[errors_ev.id],
            )
        )
    runtime_rows = (runtime_ev.data or {}).get("rows", []) if runtime_ev else []
    if runtime_rows and runtime_ev:
        top_runtime = runtime_rows[0]
        hypotheses.append(
            Hypothesis(
                statement=f"The runtime reports: {str(top_runtime.get('error', ''))[:160]} ({top_runtime.get('count')} times).",
                confidence=0.35,
                evidence_ids=[runtime_ev.id],
            )
        )
    if not hypotheses:
        hypotheses.append(
            Hypothesis(statement="The evidence does not point to a cause.", confidence=0.1)
        )
    return Diagnosis(
        summary=f"Rule-based diagnosis, made without an LLM: {hypotheses[0].statement}",
        hypotheses=hypotheses[:3],
        impact="See the alarms and error counts in the evidence.",
        unknowns=["Made by fixed rules, not an LLM, so it can't connect less obvious clues."],
    )


def _int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except ValueError:
        return 0
