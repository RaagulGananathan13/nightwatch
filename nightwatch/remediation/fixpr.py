"""Lambda entry point: open the Terraform pull request an investigation called for (spec F10).

The investigation stores which setting is too small (``config_fix``). This step:
1. reads that Terraform file from the repository's main branch;
2. doubles the value, up to the catalogue's ceiling;
3. commits the change on its own branch and opens a pull request with the diagnosis and the
   evidence.

CI posts the Terraform plan on the pull request, including the free-tier guard. A person
reviews and merges it, then applies with ``make apply``. Nothing here changes AWS, but it
still respects the kill switch, so an evaluation run never opens pull requests.
"""

from __future__ import annotations

from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from nightwatch.agent.services import service_info
from nightwatch.agent.tools import AwsClients
from nightwatch.chat.cards import incident_message
from nightwatch.chat.discord import DiscordError, webhook_from_ssm
from nightwatch.common.audit import AuditLog
from nightwatch.common.config import Settings
from nightwatch.common.incidents import IncidentStore
from nightwatch.common.log import emit
from nightwatch.common.models import Incident
from nightwatch.common.secrets import SecretCache
from nightwatch.common.timeutil import utcnow
from nightwatch.remediation.calls import kill_switch_on
from nightwatch.remediation.config_fix import proposal, read_value, set_value
from nightwatch.remediation.github import GitHub

_secrets = SecretCache()
_aws = AwsClients()

WHAT = {
    "read_capacity": ("read capacity", "read units"),
    "memory": ("Lambda memory", "MB"),
    "timeout": ("Lambda timeout", "seconds"),
}


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    settings = Settings.from_env()
    incident_id = str(event["incident_id"])
    store = IncidentStore(settings.table_name, settings.tenant_id)
    audit = AuditLog(settings.table_name, settings.tenant_id, actor="nightwatch-fixpr")
    incident = store.get_incident(incident_id)
    fix = ((incident.investigation_result or {}).get("config_fix") or {}) if incident else {}
    if incident is None or not fix:
        return {"incident_id": incident_id, "opened": False}
    service = service_info(incident.service)
    knob = service.terraform.get(str(fix.get("knob")))
    if knob is None or not service.repository:
        return {"incident_id": incident_id, "opened": False}
    if kill_switch_on(_aws, settings.kill_switch_param):
        audit.record("fix_pr_skipped", incident_id, why="kill switch is on", knob=fix["knob"])
        return {"incident_id": incident_id, "opened": False}

    github = GitHub(_secrets.get(settings.github_token_param), service.repository)
    text, blob_sha = github.file(knob.file)
    current = read_value(text, knob)
    proposed = proposal(current, knob)
    if proposed <= current:
        audit.record("fix_pr_skipped", incident_id, why="already at its ceiling", value=current)
        return {"incident_id": incident_id, "opened": False}
    name, unit = WHAT.get(str(fix["knob"]), (str(fix["knob"]), ""))
    title = (
        f"Raise {incident.service} {name} from {current} to {proposed} {unit} ({incident.label})"
    )
    url = github.open_pull_request(
        branch=f"nightwatch/{incident.label.lower()}-{str(fix['knob']).replace('_', '-')}",
        path=knob.file,
        text=set_value(text, knob, proposed),
        blob_sha=blob_sha,
        message=f"raise {incident.service} {name} to {proposed} {unit}".strip(),
        title=title,
        body=pull_request_body(
            incident, fix, knob.file, knob.attribute, current, proposed, unit, _dashboard(settings)
        ),
    )
    store.set_fix_pr(incident_id, url)
    audit.record(
        "fix_pr_opened", incident_id, url=url, knob=fix["knob"], value=current, to=proposed
    )
    emit("INFO", "fix pull request opened", incident=incident.label, url=url)
    _show_on_discord(settings, store, incident_id)
    return {"incident_id": incident_id, "opened": True, "url": url}


def pull_request_body(
    incident: Incident,
    fix: dict[str, Any],
    path: str,
    attribute: str,
    current: int,
    proposed: int,
    unit: str,
    dashboard_url: str,
) -> str:
    """The PR description. Incident text is untrusted, so it can't mention people or add HTML."""
    result = incident.investigation_result or {}
    diagnosis = result.get("diagnosis") or {}
    evidence = {item.get("id"): item.get("brief") for item in result.get("evidence") or []}
    cited = [
        f"- **{eid}**: {_quiet(str(evidence.get(eid, '')))}" for eid in fix.get("evidence_ids", [])
    ]
    link = f"{dashboard_url.rstrip('/')}/#/incidents/{incident.id}" if dashboard_url else ""
    lines = [
        f"NightWatch opened this for **{incident.label}** ({incident.service}, opened "
        f"{incident.opened_at:%Y-%m-%d %H:%M} UTC).",
        "",
        f"**Why:** {_quiet(str(fix.get('reason', '')))}",
        "",
        "**Diagnosis**",
        "",
        f"> {_quiet(str(diagnosis.get('summary', 'not diagnosed')))}",
        "",
        "**Evidence**",
        "",
        *(cited or ["- (none cited)"]),
        "",
        f"**The change:** `{path}`: `{attribute}` {current} → {proposed} {unit}.",
        "",
        "Before merging, check the Terraform plan CI adds to this pull request. Its free-tier "
        "guard stops anything that would cost money. After merging, run `make apply`.",
    ]
    if link:
        lines += ["", f"All the evidence: {link}"]
    return "\n".join(lines)


def _dashboard(settings: Settings) -> str:
    try:
        return _secrets.get(settings.dashboard_url_param)
    except (BotoCoreError, ClientError):  # no dashboard: the PR just has no link
        return ""


def _quiet(text: str) -> str:
    """No @mentions (GitHub would notify someone), no HTML, one line."""
    return " ".join(text.replace("@", "&#64;").replace("<", "&lt;").split())[:600]


def _show_on_discord(settings: Settings, store: IncidentStore, incident_id: str) -> None:
    incident = store.get_incident(incident_id)
    hook = webhook_from_ssm(settings.discord_webhook_param, _secrets.get)
    if incident is None or hook is None or not incident.discord_message_id:
        return
    try:
        hook.edit(incident.discord_message_id, incident_message(incident, utcnow()))
    except DiscordError as exc:
        emit("WARNING", "could not update the Discord message", error=str(exc)[:200])
