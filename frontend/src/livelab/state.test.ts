import { describe, expect, it } from "vitest";
import { initialRunView, reduce, reduceAll } from "./state";
import type { Frame } from "./types";

const frames: Frame[] = [
  {
    seq: 1,
    event: "phase",
    data: {
      phase: "baseline",
      at_ms: 1000,
      target: "shipping",
      timings: { baseline_s: 120, soak_s: 180, lag_s: 60 },
    },
  },
  { seq: 2, event: "lab", data: { services: [{ name: "shipping", state: "running" }], ingest_age_s: 20 } },
  { seq: 3, event: "phase", data: { phase: "injecting", at_ms: 2000, detail: "3 CPU hogs" } },
  { seq: 4, event: "agent", data: { agent_id: "manager", kind: "manager", step: "topology" } },
  { seq: 5, event: "agent", data: { agent_id: "worker-1", parent_id: "manager", kind: "code_iter", code: "x=1" } },
  { seq: 6, event: "agent", data: { agent_id: "manager", kind: "manager", step: "plan" } },
  {
    seq: 7,
    event: "report",
    data: { root_cause_service: "shipping", fault_type: "cpu", ranked_services: ["shipping"], hit: true },
  },
  { seq: 8, event: "action", data: { status: "posted", action: { kind: "restart", id: "a1" } } },
  { seq: 9, event: "action", data: { status: "approved", action: { kind: "restart", id: "a1" } } },
  { seq: 10, event: "recovery", data: { before: 300, after: 40, recovered: true } },
  { seq: 11, event: "phase", data: { phase: "done", at_ms: 9000 } },
  { seq: 12, event: "done", data: {} },
];

describe("reduce", () => {
  it("folds a full run into renderable state", () => {
    const s = reduceAll(initialRunView(), frames);
    expect(s.phase).toBe("done");
    expect(s.phases.map((p) => p.phase)).toEqual(["baseline", "injecting", "done"]);
    expect(s.target).toBe("shipping");
    expect(s.timings?.soak_s).toBe(180);
    expect(s.lab?.services[0].name).toBe("shipping");
    expect(s.agentOrder).toEqual(["manager", "worker-1"]);
    expect(s.agents["manager"]).toHaveLength(2);
    expect(s.agents["worker-1"][0].code).toBe("x=1");
    expect(s.report?.hit).toBe(true);
    expect(s.action?.status).toBe("approved");
    expect(s.actionHistory.map((a) => a.status)).toEqual(["posted", "approved"]);
    expect(s.recovery?.recovered).toBe(true);
    expect(s.done).toBe(true);
    expect(s.lastSeq).toBe(12);
  });

  it("drops duplicate and stale frames so reconnects are idempotent", () => {
    const once = reduceAll(initialRunView(), frames);
    const twice = reduceAll(once, frames.slice(0, 8));
    expect(twice).toEqual(once);
    expect(twice.agents["manager"]).toHaveLength(2);
  });

  it("keeps error and the phase that carried it", () => {
    const s = reduceAll(initialRunView(), [
      { seq: 1, event: "phase", data: { phase: "investigating", at_ms: 1 } },
      { seq: 2, event: "error", data: { message: "llm exploded" } },
      { seq: 3, event: "phase", data: { phase: "failed", at_ms: 2 } },
    ]);
    expect(s.error).toBe("llm exploded");
    expect(s.phase).toBe("failed");
  });

  it("does not mutate the previous state", () => {
    const base = initialRunView();
    const next = reduce(base, frames[0]);
    expect(base.phases).toHaveLength(0);
    expect(next.phases).toHaveLength(1);
  });
});
