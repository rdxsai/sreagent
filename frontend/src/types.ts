export type Scenario = {
  id: string;
  title: string;
  description: string;
  symptom: string;
  alert: string | null;
  alert_at_second: number | null;
};

// One streamed event from the agent. `type` selects the payload fields used.
export type AgentEvent = {
  type:
    | "status"
    | "thinking"
    | "text"
    | "tool_call"
    | "tool_result"
    | "subagent_spawn"
    | "subagent_done"
    | "finding"
    | "terminal"
    | "done"
    | "error";
  agent: string;
  t: number;
  // payload (per type)
  text?: string;
  tool?: string;
  input?: unknown;
  summary?: string;
  is_error?: boolean;
  agent_id?: string;
  service?: string;
  kind?: string;
  change_id?: string;
  is_origin?: boolean;
  fault_type?: string | null;
  confidence?: number;
  suspect_change_id?: string | null;
  data?: any;
  message?: string;
  // enriched final report
  root_cause?: any;
  culprit?: Change | null;
  ruled_out?: Change[];
  evidence?: string[];
  timeline?: { second: number; kind: string; detail: string }[];
  findings?: any[];
  subagents?: number;
};

export type Change = {
  id: string;
  service?: string | null;
  summary?: string | null;
  time?: number | null;
  diff_touches?: string[];
};

