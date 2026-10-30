import type { SettingsView } from "../types.ts";
import { type TokenSource, useApi } from "../useApi.ts";

export function Settings({ token }: { token: TokenSource }) {
  const { data, error } = useApi<SettingsView>("settings", token);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const switchedOff = data.kill_switch.trim().toLowerCase() !== "false";
  return (
    <section>
      <h1>Settings</h1>
      <p className="muted">Read-only: these change through Terraform, SSM or services.yaml, so every change is reviewed.</p>
      <section className="card">
        <h2>Fixes</h2>
        <p>
          Kill switch:{" "}
          {switchedOff ? (
            <span className="warn">ON: no fix will run ({data.kill_switch})</span>
          ) : (
            <span className="ok">off: approved fixes can run</span>
          )}
        </p>
        <p className="muted small">
          Set <code>/nightwatch/kill_switch</code> to <code>true</code> in SSM to stop every fix. Approval requests expire after{" "}
          {data.approval_timeout_minutes} min.
        </p>
      </section>
      <section className="card">
        <h2>AI</h2>
        <p>
          Models, tried in order: <code>{data.llm_providers}</code>
        </p>
        <p className="muted small">At most {data.llm_daily_requests} requests per model per day, well inside the free tier.</p>
      </section>
      <section className="card">
        <h2>Watched services</h2>
        {Object.values(data.services).map((service) => (
          <div key={service.name}>
            <h3>{service.name}</h3>
            <p className="muted">{service.description}</p>
            <p className="small">
              Lambda: {service.lambda_functions.join(", ") || "-"} · tables: {service.dynamodb_tables.join(", ") || "-"} · parameters:{" "}
              {service.ssm_parameters.join(", ") || "-"}
            </p>
            <p className="small">
              Runbooks: {service.runbooks.join(", ") || "none"} · {service.approvers.length} approver(s)
            </p>
          </div>
        ))}
      </section>
    </section>
  );
}
