import { utc } from "../format.ts";
import { type TokenSource, useApi } from "../useApi.ts";

interface Forecast {
  name: string;
  status: "ok" | "watch" | "warn" | "over";
  sentence: string;
  crossing: string | null;
}

const LABEL: Record<Forecast["status"], string> = {
  ok: "✓ fine",
  watch: "watch",
  warn: "⚠ heading over",
  over: "✗ over",
};

/** Today's forecasts (spec F12): free tiers, error budgets and capacity, each against its limit. */
export function Forecasts({ token }: { token: TokenSource }) {
  const { data, error } = useApi<{ report: { at: string; forecasts: Forecast[] } | null }>("forecasts", token);
  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading…</p>;
  if (!data.report) return <p className="muted">No forecast yet. It runs every morning at 07:00 UTC.</p>;
  return (
    <section>
      <h1>Forecasts</h1>
      <p className="muted">
        A straight-line trend on the last 14 days, against each limit. Made {utc(data.report.at)}.
      </p>
      <table>
        <tbody>
          {data.report.forecasts.map((forecast) => (
            <tr key={forecast.name}>
              <td>
                <strong>{forecast.name}</strong>
              </td>
              <td className={forecast.status === "ok" || forecast.status === "watch" ? "ok nowrap" : "warn nowrap"}>
                {LABEL[forecast.status]}
              </td>
              <td>{forecast.sentence}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
