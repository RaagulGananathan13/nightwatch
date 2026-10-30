"""Score one chaos run, and turn all runs into ``eval/results.md`` (spec F16).

What each number means:
- **Top-1 / top-3 accuracy**: the top hypothesis (or any of the top 3) names the scenario's
  ground-truth cause. "Names" means it contains one of the scenario's ``expect`` phrases,
  which describe the cause, not the symptom.
- **Time to diagnosis**: from the alarm (the incident opening) to the diagnosis being posted
  in Discord. **Fault to diagnosis** adds the time the alarms took to fire.
- **Evidence validity**: hypotheses the evidence guard verified, so every cited number and
  commit is found in the evidence (spec F4). "As written" leaves out those the guard had to
  repair by adding a citation the model missed.
- **Right fix proposed**: NightWatch proposed the runbook that fixes this scenario.
"""

from __future__ import annotations

from datetime import datetime
from statistics import mean
from typing import Any, Protocol

from chaos.runner import Outcome, Scenario, mentions
from nightwatch.common.timeutil import from_iso, to_iso
from nightwatch.remediation.actions import Action


class EvalLab(Protocol):
    def audit(self, incident_id: str) -> list[dict[str, Any]]: ...
    def actions(self, incident_id: str) -> list[Action]: ...


def score(lab: EvalLab, scenario: Scenario, outcome: Outcome, commit: str) -> dict[str, Any]:
    run: dict[str, Any] = {
        "scenario": scenario.slug,
        "name": scenario.name,
        "ground_truth": scenario.ground_truth,
        "expect": list(scenario.expect),
        "expected_fix": scenario.fix,
        "ran_at": to_iso(outcome.started),
        "commit": commit,
        "chaos_verdict": outcome.code,
        "incidents": len(outcome.incidents),
    }
    if outcome.code == 2 or outcome.faulted is None:
        return {**run, "status": "not run"}
    if not outcome.incidents:
        return {**run, "status": "no incident"}

    incident = outcome.incidents[0]
    result = incident.investigation_result or {}
    diagnosis = result.get("diagnosis") or {}
    hypotheses: list[dict[str, Any]] = diagnosis.get("hypotheses") or []
    events = lab.audit(incident.id)
    posted = next((e["at"] for e in events if e["event"] == "diagnosis_posted"), None)
    actions = lab.actions(incident.id)
    statements = [str(h.get("statement", "")) for h in hypotheses]
    run.update(
        {
            "status": "diagnosed" if hypotheses else "no diagnosis",
            "incident": incident.label,
            "incident_id": incident.id,
            "alerts": incident.alert_count,
            "detect_seconds": _seconds(outcome.faulted, incident.opened_at),
            "diagnosis_seconds": (
                _seconds(incident.opened_at, from_iso(posted)) if posted else None
            ),
            "top1": mentions(statements[0], scenario.expect) if statements else None,
            "top3": mentions(" ".join(statements[:3]), scenario.expect),
            "top_hypothesis": statements[0] if statements else "",
            "confidence": float(hypotheses[0].get("confidence", 0)) if hypotheses else 0.0,
            "hypotheses": len(hypotheses),
            "verified": sum(1 for h in hypotheses if h.get("verified")),
            "verified_as_written": sum(1 for h in hypotheses if _as_written(h)),
            "method": result.get("method"),
            "model": result.get("model"),
            "prompt_version": result.get("prompt_version"),
            "llm_calls": int(result.get("llm_calls") or 0),
            "tokens": int(result.get("tokens") or 0),
            "fix_proposed": actions[-1].runbook_id if actions else None,
        }
    )
    return run


def summarize(runs: list[dict[str, Any]]) -> dict[str, Any]:
    ran = [r for r in runs if r.get("status") != "not run"]
    diagnosed = [r for r in ran if r.get("status") == "diagnosed"]
    timed = [r for r in diagnosed if r.get("diagnosis_seconds") is not None]
    hypotheses = sum(r["hypotheses"] for r in diagnosed)
    return {
        "scenarios": len(ran),
        "detected": sum(1 for r in ran if r.get("incident")),
        "top1": sum(1 for r in ran if r.get("top1")),
        "top3": sum(1 for r in ran if r.get("top3")),
        "mean_diagnosis_seconds": _mean([r["diagnosis_seconds"] for r in timed]),
        "mean_detect_seconds": _mean([r["detect_seconds"] for r in timed]),
        "mean_fault_to_diagnosis_seconds": _mean(
            [r["detect_seconds"] + r["diagnosis_seconds"] for r in timed]
        ),
        "hypotheses": hypotheses,
        "verified": sum(r["verified"] for r in diagnosed),
        "verified_as_written": sum(r["verified_as_written"] for r in diagnosed),
        "fixes_right": sum(1 for r in ran if r.get("fix_proposed") == r.get("expected_fix")),
        "alerts": sum(r.get("alerts", 0) for r in ran),
        "incidents": sum(r.get("incidents", 0) for r in ran),
        "mean_llm_calls": _mean([r["llm_calls"] for r in diagnosed]),
        "mean_tokens": _mean([r["tokens"] for r in diagnosed]),
        "models": sorted({r["model"] for r in diagnosed if r.get("model")}),
        "prompt_versions": sorted(
            {r["prompt_version"] for r in diagnosed if r.get("prompt_version")}
        ),
        "commits": sorted({r["commit"] for r in ran if r.get("commit")}),
        "dates": sorted({r["ran_at"][:10] for r in ran}),
    }


def render(runs: list[dict[str, Any]], notes: list[str] | None = None) -> str:
    s = summarize(runs)
    n = s["scenarios"]
    if not n:
        return "# NightWatch evaluation results\n\nNo scenarios have been run yet (`make eval`).\n"
    lines = [
        "# NightWatch evaluation results",
        "",
        f"Measured on {', '.join(s['dates'])} against the live dev stack by `make eval`: "
        f"{n} chaos scenarios, each a real fault injected into ShopLite (commit "
        f"{', '.join(s['commits'])}, prompt {', '.join(s['prompt_versions']) or '-'}, model "
        f"{', '.join(s['models']) or '-'}). Every number below comes from those runs.",
        "",
        "| Metric | Result |",
        "|---|---|",
        f"| Top-1 root-cause accuracy | **{_rate(s['top1'], n)}** |",
        f"| Top-3 root-cause accuracy | {_rate(s['top3'], n)} |",
        f"| Mean time to diagnosis (alarm → diagnosis in Discord) | **{_time(s['mean_diagnosis_seconds'])}** |",
        f"| Mean time from fault to diagnosis | {_time(s['mean_fault_to_diagnosis_seconds'])} |",
        f"| Evidence validity (hypotheses the guard verified) | **{_rate(s['verified'], s['hypotheses'])}** |",
        f"| … verified as the model wrote them, before citation repair | {_rate(s['verified_as_written'], s['hypotheses'])} |",
        f"| Faults detected (an incident opened) | {_rate(s['detected'], n)} |",
        f"| Right fix proposed | {_rate(s['fixes_right'], n)} |",
        f"| Alerts grouped into incidents | {s['alerts']} alerts → {s['incidents']} incidents |",
        f"| LLM use per incident | {_number(s['mean_llm_calls'])} calls, {_number(s['mean_tokens'], 0)} tokens |",
        "",
        "## Scenarios",
        "",
        "| Scenario | Incident | Fault → alarm | Alarm → diagnosis | Top-1 | Top-3 | Verified | LLM | Fix proposed |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in runs:
        if r.get("status") == "not run":
            lines.append(f"| {r['name']} | not run | | | | | | | |")
            continue
        if not r.get("incident"):
            lines.append(f"| {r['name']} | **none** | | | ✗ | ✗ | | | |")
            continue
        fix = r.get("fix_proposed") or "none"
        fix_mark = "✓" if fix == r.get("expected_fix") else f"✗ (expected {r.get('expected_fix')})"
        lines.append(
            f"| {r['name']} | {r['incident']} ({r.get('alerts', 0)} alerts) "
            f"| {_time(r.get('detect_seconds'))} | {_time(r.get('diagnosis_seconds'))} "
            f"| {_mark(r.get('top1'))} | {_mark(r.get('top3'))} "
            f"| {r.get('verified', 0)}/{r.get('hypotheses', 0)} "
            f"| {r.get('llm_calls', 0)} calls, {r.get('tokens', 0):,} tokens | {fix} {fix_mark} |"
        )
    lines += ["", "## What NightWatch said", ""]
    for r in runs:
        if r.get("status") == "not run":
            continue
        lines += [
            f"**{r['name']}**. Ground truth: {r['ground_truth']}",
            "",
            f"> {r.get('top_hypothesis') or '(no diagnosis)'} "
            f"({round(float(r.get('confidence', 0)) * 100)}% confidence)",
            "",
        ]
    if notes:
        lines += ["## Notes", ""] + [f"- {note}" for note in notes] + [""]
    lines += [
        "## How it is scored",
        "",
        "A hypothesis names the cause when it contains one of the scenario's phrases "
        "(any case; `a+b` means both). The phrases name the cause, not the symptom.",
        "",
        "| Scenario | Counts as naming the cause | Right fix |",
        "|---|---|---|",
    ]
    for r in runs:
        phrases = ", ".join(f"`{p}`" for p in r.get("expect", []))
        lines.append(f"| {r['name']} | {phrases} | `{r.get('expected_fix')}` |")
    lines += [
        "",
        "Remediation was switched off with the kill switch during the run, so fixes were "
        "proposed and recorded but never executed, and nobody was asked to approve them. "
        "Approving a fix is measured separately (`make chaos-fix`, see the README).",
        "",
    ]
    return "\n".join(lines)


def _as_written(hypothesis: dict[str, Any]) -> bool:
    repaired = any(str(note).startswith("added ") for note in hypothesis.get("notes") or [])
    return bool(hypothesis.get("verified")) and not repaired


def _seconds(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds(), 1)


def _mean(values: list[float]) -> float | None:
    return round(mean(values), 1) if values else None


def _rate(part: int, whole: int) -> str:
    return f"{part}/{whole} ({round(100 * part / whole)}%)" if whole else "-"


def _time(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    if seconds < 90:
        return f"{seconds:.0f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min {rest:02d} s"


def _number(value: float | None, digits: int = 1) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def _mark(match: str | None) -> str:
    return "✓" if match else "✗"
