import { useEffect, useState } from "react";

import { Markdown } from "../Markdown.tsx";
import { between, percent, utc } from "../format.ts";
import { href } from "../router.ts";
import type { Evidence, IncidentDetail } from "../types.ts";
import { type TokenSource, useApi } from "../useApi.ts";

export function Incident({ id, focus, token }: { id: string; focus?: string; token: TokenSource }) {
  const { data, error } = useApi<IncidentDetail>(`incidents/${id}`, token);

  useEffect(() => {
    if (data && focus) document.getElementById(`ev-${focus}`)?.scrollIntoView({ behavior: "smooth" });
  }, [data, focus]);

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const { incident, evidence, timeline, actions, alerts, postmortem } = data;
  const result = incident.investigation_result;
  const diagnosis = result?.diagnosis;
  const label = incident.number !== null ? `INC-${String(incident.number).padStart(3, "0")}` : incident.id;

  return (
    <article>
      <p className="muted small">
        <a href={href("incidents")}>← Incidents</a>
      </p>
      <h1>
        {label} <span className={`badge ${incident.status}`}>{incident.status}</span>{" "}
        <span className={`badge ${incident.severity}`}>{incident.severity}</span>
      </h1>
      <p className="muted">
        {incident.service} · {incident.title} · opened {utc(incident.opened_at)} · lasted{" "}
        {between(incident.opened_at, incident.resolved_at)}
        {incident.acked_by_name && ` · taken by ${incident.acked_by_name}`}
      </p>

      <section className="card">
        <h2>Diagnosis</h2>
        {!diagnosis && <p className="muted">Investigation: {incident.investigation ?? "not started"}.</p>}
        {diagnosis && (
          <>
            <p>{diagnosis.summary}</p>
            <ol className="hypotheses">
              {diagnosis.hypotheses.map((hypothesis, index) => (
                <li key={index}>
                  <span className={hypothesis.verified ? "ok" : "warn"}>{hypothesis.verified ? "✓ verified" : "? unverified"}</span>{" "}
                  <strong>{percent(hypothesis.confidence)}</strong> {hypothesis.statement}{" "}
                  {hypothesis.evidence_ids.map((evidenceId) => (
                    <a key={evidenceId} className="chip" href={href("incidents", id, evidenceId)}>
                      {evidenceId}
                    </a>
                  ))}
                  {hypothesis.notes.length > 0 && <div className="muted small">Guard: {hypothesis.notes.join("; ")}</div>}
                </li>
              ))}
            </ol>
            {diagnosis.impact && <p><strong>Impact:</strong> {diagnosis.impact}</p>}
            {diagnosis.unknowns.length > 0 && <p className="muted"><strong>Couldn't check:</strong> {diagnosis.unknowns.join(" · ")}</p>}
            {result?.config_fix && (
              <p>
                <strong>Terraform setting too small:</strong> <code>{result.config_fix.knob}</code>. {result.config_fix.reason}{" "}
                {incident.fix_pr_url ? (
                  <a href={incident.fix_pr_url} rel="noreferrer" target="_blank">
                    Review the fix pull request
                  </a>
                ) : (
                  <span className="muted">No pull request yet.</span>
                )}
              </p>
            )}
            {result?.similar && result.similar.length > 0 && (
              <div>
                <strong>Looks like:</strong>
                <ul className="plain">
                  {result.similar.map((match) => (
                    <li key={match.id}>
                      <a href={href("incidents", match.id)}>{match.label}</a>, {match.when} (similarity {match.score.toFixed(2)}): {match.cause}{" "}
                      <span className="muted">Fix: {match.fix}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            <p className="muted small">
              {result?.method === "llm"
                ? `${result.model} · ${result.llm_calls} LLM call(s) · ${result.tokens.toLocaleString()} tokens · ${result.prompt_version}`
                : "Rule-based diagnosis (no LLM)"}
              {result && ` · ${(result.duration_ms / 1000).toFixed(1)} s`}
            </p>
          </>
        )}
      </section>

      {actions.length > 0 && (
        <section className="card">
          <h2>Fixes</h2>
          {actions.map((action) => (
            <div key={action.id}>
              <p>
                <strong>{action.title}</strong> <span className="badge">{action.status.replace(/_/g, " ")}</span>{" "}
                <span className="muted small">risk {action.risk} · runbook {action.runbook_id}</span>
              </p>
              <pre>{action.preview}</pre>
              <p className="muted small">
                Why: {action.reason} · Blast radius: {action.blast_radius}
                {action.decided_by_name && ` · Decided by ${action.decided_by_name}`}
              </p>
              {action.verification && <p>Verification: {action.verification.detail}</p>}
              {action.result && <p className="muted">{action.result}</p>}
            </div>
          ))}
        </section>
      )}

      <section className="card">
        <h2>Evidence ({evidence.length})</h2>
        <p className="muted small">Everything the investigation looked at, in order. The diagnosis may only cite these.</p>
        {evidence.map((item) => (
          <EvidenceItem key={item.id} item={item} focused={item.id === focus} />
        ))}
      </section>

      <section className="card">
        <h2>Timeline</h2>
        <table>
          <tbody>
            {[...alerts.map((alert) => ({ at: alert.occurred_at, who: "CloudWatch", what: `${alert.alarm_name} → ${alert.state}` })),
              ...timeline.map((entry) => ({ at: entry.at, who: entry.actor, what: describe(entry.event, entry.details) }))]
              .sort((a, b) => a.at.localeCompare(b.at))
              .map((row, index) => (
                <tr key={index}>
                  <td className="nowrap">{utc(row.at).slice(11)}</td>
                  <td className="muted small">{row.who}</td>
                  <td>{row.what}</td>
                </tr>
              ))}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Postmortem</h2>
        {postmortem ? <Markdown source={postmortem} /> : <p className="muted">Written when the incident resolves.</p>}
      </section>
    </article>
  );
}

function EvidenceItem({ item, focused }: { item: Evidence; focused: boolean }) {
  const [open, setOpen] = useState(focused);
  useEffect(() => setOpen((was) => was || focused), [focused]);
  return (
    <div id={`ev-${item.id}`} className={focused ? "evidence focused" : "evidence"}>
      <div className="row">
        <strong>{item.id}</strong>
        <span className="muted small">
          {item.tool}
          {item.label && ` · ${item.label}`} · {item.duration_ms} ms {item.requested_by === "llm" && "· asked for by the model"}
        </span>
        <span className={item.ok ? "ok" : "warn"}>{item.ok ? "ok" : "failed"}</span>
      </div>
      <pre>{item.summary}</pre>
      <button className="link" onClick={() => setOpen(!open)}>
        {open ? "Hide" : "Show"} the query and raw data
      </button>
      {open && <pre className="raw">{JSON.stringify({ args: item.args, data: item.data }, null, 2)}</pre>}
    </div>
  );
}

function describe(event: string, details: Record<string, unknown>): string {
  const extra = ["alarm", "user", "runbook", "verdict", "why", "key", "model"]
    .map((key) => details[key])
    .filter((value) => value !== undefined && value !== null && value !== "")
    .join(", ");
  const text = event.replace(/_/g, " ");
  return extra ? `${text}: ${extra}` : text;
}
