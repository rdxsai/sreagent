// Fold the run's SSE frames into one renderable state. Pure, tested, and the only
// place that understands frame ordering (duplicates and replays of older seqs are
// dropped so a reconnect can safely resume from a snapshot + backlog).

import type {
  ActionFrame,
  AgentRecord,
  Frame,
  LabService,
  PhaseName,
  Recovery,
  Report,
  Timings,
} from "./types";

export type RunView = {
  lastSeq: number;
  phase: PhaseName | null;
  phases: { phase: PhaseName; at_ms: number; detail?: string }[];
  target: string | null;
  timings: Timings | null;
  window: { start_ms: number; end_ms: number; onset_s: number } | null;
  lab: { services: LabService[]; ingest_age_s: number | null } | null;
  agents: Record<string, AgentRecord[]>;
  agentOrder: string[];
  report: Report | null;
  action: ActionFrame | null;
  actionHistory: ActionFrame[];
  recovery: Recovery | null;
  error: string | null;
  done: boolean;
};

export function initialRunView(): RunView {
  return {
    lastSeq: 0,
    phase: null,
    phases: [],
    target: null,
    timings: null,
    window: null,
    lab: null,
    agents: {},
    agentOrder: [],
    report: null,
    action: null,
    actionHistory: [],
    recovery: null,
    error: null,
    done: false,
  };
}

export function reduce(state: RunView, frame: Frame): RunView {
  if (frame.seq !== 0 && frame.seq <= state.lastSeq) return state;
  const next: RunView = { ...state, lastSeq: Math.max(state.lastSeq, frame.seq) };
  const d = frame.data ?? {};
  switch (frame.event) {
    case "phase": {
      next.phase = d.phase;
      next.phases = [...state.phases, { phase: d.phase, at_ms: d.at_ms, detail: d.detail }];
      if (d.target) next.target = d.target;
      if (d.timings) next.timings = d.timings;
      if (d.window) next.window = d.window;
      break;
    }
    case "lab":
      next.lab = { services: d.services ?? [], ingest_age_s: d.ingest_age_s ?? null };
      break;
    case "agent": {
      const id = d.agent_id ?? "manager";
      const records = state.agents[id] ?? [];
      next.agents = { ...state.agents, [id]: [...records, d] };
      next.agentOrder = state.agentOrder.includes(id)
        ? state.agentOrder
        : [...state.agentOrder, id];
      break;
    }
    case "report":
      next.report = d;
      break;
    case "action":
      next.action = d;
      next.actionHistory = [...state.actionHistory, d];
      break;
    case "recovery":
      next.recovery = d;
      break;
    case "error":
      next.error = d.message ?? "unknown error";
      break;
    case "done":
      next.done = true;
      break;
  }
  return next;
}

export function reduceAll(state: RunView, frames: Frame[]): RunView {
  return frames.reduce(reduce, state);
}
