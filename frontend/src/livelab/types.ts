// TS mirror of the backend's locked SSE event contract (sentinel/api/livelab).

export type Frame = { seq: number; event: string; data: any };

export type PhaseName =
  | "preflight"
  | "booting"
  | "baseline"
  | "injecting"
  | "soak"
  | "investigating"
  | "report"
  | "awaiting_approval"
  | "executing"
  | "recovering"
  | "done"
  | "failed"
  | "cancelled";

export type Timings = { baseline_s: number; soak_s: number; lag_s: number };

export type LabService = { name: string; state: string };

// One TraceLogger record from the code-mode agent, broadcast verbatim.
export type AgentRecord = {
  run_id: string;
  agent_id: string;
  parent_id: string | null;
  kind: "manager" | "worker" | "code_iter" | string;
  step?: string;
  seq?: number;
  reasoning?: string;
  code?: string;
  stdout?: string;
  traceback?: string | null;
  [key: string]: unknown;
};

export type Report = {
  root_cause_service: string | null;
  fault_type: string | null;
  justification: string;
  ranked_services: string[];
  verdicts: any[];
  graph_source: string | null;
  graph_edges: number;
  usage: { input?: number; output?: number };
  hit: boolean;
};

export type SuggestedAction = {
  id: string;
  kind: string;
  effect: string;
  target_service: string;
  params: Record<string, string | number>;
  description: string;
  risk: string;
  reversible: boolean;
  preview: string;
  citations: string[];
};

export type ActionFrame = {
  action: SuggestedAction;
  status: "posted" | "approved" | "denied" | "expired" | "execute_result";
  approver?: string;
  ok?: boolean;
  outcome?: { before?: number | null; after?: number | null; error?: string };
};

export type Recovery = { before: number | null; after: number | null; recovered: boolean };

export type ScenarioInfo = {
  id: string;
  lab: string;
  label: string;
  fault_desc: string;
  truth_service: string;
  hero_metric: "cpu" | "error";
  symptom: string;
};

export type RunSnapshot = {
  run_id: string;
  mode: "live" | "replay";
  source_run_id?: string;
  target: string;
  scenario?: { id: string; lab: string; label: string; fault_desc: string; hero_metric: string };
  preset: string;
  timings: Timings;
  started_ms: number;
  phase: PhaseName;
  phases: { phase: PhaseName; at_ms: number; detail?: string }[];
  report: Report | null;
  action: ActionFrame | null;
  recovery: Recovery | null;
  last_seq: number;
};

export type PreflightCheck = { name: string; ok: boolean; detail: string };

export type Status = {
  run: RunSnapshot | null;
  preflight: PreflightCheck[];
  lab: { services: LabService[]; ingest_age_s: number | null };
  labs: Record<string, LabService[]>;
  ingest_age_s: number | null;
  replays: { run_id: string; target: string; phase: string; preset: string; started_ms: number }[];
  scenarios: ScenarioInfo[];
  presets: Record<string, Timings>;
};

export type TelemetrySeries = {
  series: {
    cpu?: Record<string, [number, number][]>;
    mem?: Record<string, [number, number][]>;
    err?: Record<string, [number, number][]>;
  };
  fetched_at_ms: number;
};

export type Topology = { services: string[]; edges: [string, string][] };
