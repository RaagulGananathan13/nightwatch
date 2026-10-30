import { useEffect, useState } from "react";

import { duration } from "../format.ts";
import type { EvalResults, EvalRun } from "../types.ts";

/** eval/results.json, shipped with the dashboard at build time (scripts/copy-eval.mjs). */
export function Eval() {
  const [results, setResults] = useState<EvalResults | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetch("/eval-results.json", { cache: "no-store" })
      .then((response) => (response.ok ? response.json() : Promise.reject(new Error("No evaluation results yet: run make eval."))))
      .then((body: EvalResults) => setResults(body))
      .catch((problem: unknown) => setError(problem instanceof Error ? problem.message : String(problem)));
  }, []);

  if (error) return <p className="muted">{error}</p>;
  if (!results) return <p className="muted">Loading…</p>;
  const runs = Object.values(results.runs).filter((run) => run.status !== "not run");
  const timed = runs.filter((run) => typeof run.diagnosis_seconds === "number");
  const mean = (values: number[]) => (values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0);
  const hypotheses = runs.reduce((sum, run) => sum + (run.hypotheses ?? 0), 0);
  const verified = runs.reduce((sum, run) => sum + (run.verified ?? 0), 0);
  const metrics = [
    ["Top-1 root cause", `${runs.filter((run) => run.top1).length}/${runs.length}`],
    ["Alarm → diagnosis", duration(mean(timed.map((run) => run.diagnosis_seconds ?? 0)))],
    ["Fault → diagnosis", duration(mean(timed.map((run) => (run.detect_seconds ?? 0) + (run.diagnosis_seconds ?? 0))))],
    ["Evidence validity", `${verified}/${hypotheses}`],
    ["Right fix proposed", `${runs.filter((run) => run.fix_proposed && run.fix_proposed === run.expected_fix).length}/${runs.length}`],
  ];

  return (
    <section>
      <h1>Evaluation</h1>
      <p className="muted">
        Real faults injected into ShopLite by <code>make eval</code>, each scored against its written ground truth.
        Measured {[...new Set(runs.map((run) => run.ran_at.slice(0, 10)))].join(", ")} on commit{" "}
        {[...new Set(runs.map((run) => run.commit))].join(", ")}.
      </p>
      <div className="metrics">
        {metrics.map(([name, value]) => (
          <div key={name} className="metric">
            <div className="value">{value}</div>
            <div className="muted small">{name}</div>
          </div>
        ))}
      </div>
      <section className="card">
        <h2>Time from fault to diagnosis</h2>
        <Chart runs={timed} />
      </section>
      <section className="card">
        <h2>Scenarios</h2>
        {runs.map((run) => (
          <div key={run.scenario} className="scenario">
            <p>
              <strong>{run.name}</strong> {run.top1 ? <span className="ok">✓ top-1</span> : <span className="warn">✗ top-1</span>}{" "}
              <span className="muted small">
                {run.incident} · {run.llm_calls ?? 0} LLM call(s), {(run.tokens ?? 0).toLocaleString()} tokens · fix{" "}
                {run.fix_proposed ?? "none"}
              </span>
            </p>
            <p className="muted small">Ground truth: {run.ground_truth}</p>
            <blockquote>{run.top_hypothesis ?? "(no diagnosis)"}</blockquote>
          </div>
        ))}
      </section>
      {results.notes && results.notes.length > 0 && (
        <section className="card">
          <h2>Notes</h2>
          <ul className="plain">
            {results.notes.map((note) => (
              <li key={note}>• {note}</li>
            ))}
          </ul>
        </section>
      )}
    </section>
  );
}

/** Stacked bars: fault → alarm (CloudWatch) then alarm → diagnosis (NightWatch). */
function Chart({ runs }: { runs: EvalRun[] }) {
  const longest = Math.max(1, ...runs.map((run) => (run.detect_seconds ?? 0) + (run.diagnosis_seconds ?? 0)));
  const row = 28;
  const labelWidth = 190;
  const width = 640;
  const scale = (seconds: number) => (seconds / longest) * (width - labelWidth - 70);
  return (
    <svg className="chart" viewBox={`0 0 ${width} ${runs.length * row + 30}`} role="img" aria-label="Time from fault to diagnosis per scenario">
      {runs.map((run, index) => {
        const detect = run.detect_seconds ?? 0;
        const diagnose = run.diagnosis_seconds ?? 0;
        const y = index * row + 4;
        return (
          <g key={run.scenario}>
            <text x={0} y={y + 15} className="chart-label">{run.name}</text>
            <rect x={labelWidth} y={y} width={scale(detect)} height={18} className="bar-detect" />
            <rect x={labelWidth + scale(detect)} y={y} width={scale(diagnose)} height={18} className="bar-diagnose" />
            <text x={labelWidth + scale(detect + diagnose) + 6} y={y + 14} className="chart-value">
              {duration(detect + diagnose)}
            </text>
          </g>
        );
      })}
      <g transform={`translate(${labelWidth}, ${runs.length * row + 14})`}>
        <rect width={12} height={10} className="bar-detect" />
        <text x={16} y={9} className="chart-value">fault → alarm (CloudWatch)</text>
        <rect x={190} width={12} height={10} className="bar-diagnose" />
        <text x={206} y={9} className="chart-value">alarm → diagnosis (NightWatch)</text>
      </g>
    </svg>
  );
}
