import { useState } from "react";

import { ago, between, percent } from "../format.ts";
import { href } from "../router.ts";
import type { IncidentSummary } from "../types.ts";
import { type TokenSource, useApi } from "../useApi.ts";

export function Incidents({ token }: { token: TokenSource }) {
  const [status, setStatus] = useState<"open" | "resolved">("open");
  const { data, error } = useApi<{ incidents: IncidentSummary[] }>(`incidents?status=${status}`, token);

  return (
    <section>
      <div className="row">
        <h1>Incidents</h1>
        <div className="tabs" role="tablist">
          {(["open", "resolved"] as const).map((name) => (
            <button
              key={name}
              role="tab"
              aria-selected={status === name}
              className={status === name ? "tab active" : "tab"}
              onClick={() => setStatus(name)}
            >
              {name === "open" ? "Open" : "Resolved"}
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && data.incidents.length === 0 && (
        <p className="muted">{status === "open" ? "No open incidents. All quiet." : "No resolved incidents yet."}</p>
      )}
      {data && data.incidents.length > 0 && (
        <table className="incidents">
          <thead>
            <tr>
              <th>Incident</th>
              <th>Likely cause</th>
              <th>Opened</th>
              <th>Lasted</th>
              <th>Alerts</th>
            </tr>
          </thead>
          <tbody>
            {data.incidents.map((incident) => (
              <tr key={incident.id}>
                <td>
                  <a href={href("incidents", incident.id)}>
                    <strong>{incident.label}</strong>
                  </a>{" "}
                  <span className={`badge ${incident.severity}`}>{incident.severity}</span>
                  <div className="muted small">
                    {incident.service} · {incident.title}
                  </div>
                </td>
                <td>
                  {incident.cause ? (
                    <>
                      <span className={incident.verified ? "ok" : "warn"} title={incident.verified ? "Verified against evidence" : "Not verified"}>
                        {incident.verified ? "✓" : "?"}
                      </span>{" "}
                      {incident.cause.length > 140 ? `${incident.cause.slice(0, 139)}…` : incident.cause}
                      <span className="muted small"> ({percent(incident.confidence)})</span>
                    </>
                  ) : (
                    <span className="muted">{incident.investigation ?? "not investigated"}</span>
                  )}
                  {incident.similar.length > 0 && <div className="muted small">Looks like {incident.similar.join(", ")}</div>}
                </td>
                <td title={incident.opened_at}>{ago(incident.opened_at)}</td>
                <td>{between(incident.opened_at, incident.resolved_at)}</td>
                <td>
                  {incident.alert_count}
                  {incident.postmortem && <span title="Postmortem written"> 📝</span>}
                  {incident.acked_by && <span title={`Taken by ${incident.acked_by}`}> 👀</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
