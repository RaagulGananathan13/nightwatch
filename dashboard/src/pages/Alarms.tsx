import { utc } from "../format.ts";
import { type TokenSource, useApi } from "../useApi.ts";

interface AlarmStats {
  alarm: string;
  fired: number;
  incidents: number;
  acted_on: number;
  cleared_by_itself: number;
  flaps: number;
  noise: number;
  suggestion: string;
  median_ack_minutes: number | null;
}

interface AlarmReport {
  alarms: AlarmStats[];
  fired: number;
  incidents: number;
  noisy: string[];
  noisy_share: number;
  period_days: number;
  since: string;
  until: string;
}

/** The alert-quality coach's latest weekly report (spec F11). */
export function Alarms({ token }: { token: TokenSource }) {
  const { data, error } = useApi<{ report: AlarmReport | null }>("alarms", token);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  const report = data.report;
  if (!report) return <p className="muted">No alert-quality report yet. The coach runs every Monday.</p>;
  return (
    <section>
      <h1>Alarm noise</h1>
      <p className="muted">
        {utc(report.since).slice(0, 10)} to {utc(report.until).slice(0, 10)}: {report.fired} firings across {report.incidents}{" "}
        incidents.{" "}
        {report.noisy.length > 0
          ? `${report.noisy.length} alarm(s) caused ${report.noisy_share}% of the pages that cleared by themselves.`
          : "No alarm stood out as noise."}
      </p>
      <table>
        <thead>
          <tr>
            <th>Alarm</th>
            <th>Noise</th>
            <th>Fired</th>
            <th>Led to action</th>
            <th>Cleared by itself</th>
            <th>Flaps</th>
            <th>Suggestion</th>
          </tr>
        </thead>
        <tbody>
          {report.alarms.map((alarm) => (
            <tr key={alarm.alarm}>
              <td>
                <code>{alarm.alarm}</code>
              </td>
              <td className={alarm.noise >= 50 ? "warn" : "ok"}>{alarm.noise}/100</td>
              <td>{alarm.fired}</td>
              <td>
                {alarm.acted_on} of {alarm.incidents}
              </td>
              <td>{alarm.cleared_by_itself}</td>
              <td>{alarm.flaps}</td>
              <td className="small">{alarm.suggestion}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">
        Noise is the share of an alarm's incidents that cleared within 15 minutes with nobody acting, plus 10 points per flap.
      </p>
    </section>
  );
}
