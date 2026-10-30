// Shapes returned by the dashboard API (nightwatch/api/handler.py).

export interface IncidentSummary {
  id: string;
  label: string;
  service: string;
  title: string;
  status: "open" | "resolved";
  severity: string;
  opened_at: string;
  resolved_at: string | null;
  alert_count: number;
  alarms: Record<string, string>;
  investigation: string | null;
  cause: string | null;
  confidence: number | null;
  verified: boolean | null;
  similar: string[];
  postmortem: boolean;
  acked_by: string | null;
}

export interface Hypothesis {
  statement: string;
  confidence: number;
  evidence_ids: string[];
  verified: boolean;
  notes: string[];
}

export interface Similar {
  label: string;
  id: string;
  score: number;
  cause: string;
  fix: string;
  when: string;
}

export interface InvestigationResult {
  diagnosis: {
    summary: string;
    hypotheses: Hypothesis[];
    impact: string;
    suggested_actions: { description: string; risk: string }[];
    unknowns: string[];
  };
  method: "llm" | "rules";
  model: string | null;
  llm_calls: number;
  tokens: number;
  duration_ms: number;
  prompt_version: string;
  similar?: Similar[];
}

export interface Incident {
  id: string;
  number: number | null;
  service: string;
  title: string;
  status: "open" | "resolved";
  severity: string;
  opened_at: string;
  resolved_at: string | null;
  alert_count: number;
  alarms: Record<string, string>;
  reopen_count: number;
  investigation: string | null;
  investigation_result: InvestigationResult | null;
  postmortem_key: string | null;
  acked_by_name: string | null;
}

export interface Evidence {
  id: string;
  tool: string;
  label: string;
  args: Record<string, unknown>;
  summary: string;
  data: unknown;
  ok: boolean;
  requested_by: "plan" | "llm";
  started_at: string;
  duration_ms: number;
}

export interface AuditEntry {
  event: string;
  actor: string;
  at: string;
  details: Record<string, unknown>;
}

export interface Action {
  id: string;
  runbook_id: string;
  title: string;
  risk: string;
  reason: string;
  preview: string;
  blast_radius: string;
  status: string;
  created_at: string;
  decided_by_name: string | null;
  decided_at: string | null;
  executed_at: string | null;
  result: string;
  verification: { verdict: string; detail: string } | null;
}

export interface Alert {
  alarm_name: string;
  state: string;
  severity: string;
  occurred_at: string;
  reason: string;
}

export interface IncidentDetail {
  incident: Incident;
  alerts: Alert[];
  evidence: Evidence[];
  timeline: AuditEntry[];
  actions: Action[];
  postmortem: string | null;
}

export interface Runbook {
  id: string;
  title: string;
  description: string;
  risk: string;
  auto_approve: boolean;
  signal: string;
  preview: string;
  blast_radius: string;
  action: { service: string; call: string; args: Record<string, unknown> };
  verify: { namespace: string; metric: string; stat: string; max_value: number; minutes: number };
  rollback: { service: string; call: string } | null;
}

export interface Service {
  name: string;
  description: string;
  lambda_functions: string[];
  dynamodb_tables: string[];
  ssm_parameters: string[];
  runbooks: string[];
  approvers: string[];
}

export interface SettingsView {
  kill_switch: string;
  llm_providers: string;
  llm_daily_requests: number;
  approval_timeout_minutes: number;
  services: Record<string, Service>;
}

export interface EvalRun {
  scenario: string;
  name: string;
  ground_truth: string;
  expect: string[];
  expected_fix: string | null;
  status: string;
  incident?: string;
  alerts?: number;
  detect_seconds?: number;
  diagnosis_seconds?: number | null;
  top1?: string | null;
  top3?: string | null;
  top_hypothesis?: string;
  confidence?: number;
  hypotheses?: number;
  verified?: number;
  llm_calls?: number;
  tokens?: number;
  fix_proposed?: string | null;
  ran_at: string;
  commit: string;
  model?: string | null;
}

export interface EvalResults {
  runs: Record<string, EvalRun>;
  notes?: string[];
}
