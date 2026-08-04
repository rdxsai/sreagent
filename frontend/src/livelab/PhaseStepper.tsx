// The incident timeline rail: nine stages from boot to recovery. The active stage
// shows live elapsed time (and, for the timed waits, a countdown), so the minutes
// of baseline and soak read as protocol, not as a stalled page.

import { useEffect, useState } from "react";
import { Check, Loader2, X } from "lucide-react";
import type { RunView } from "./state";
import type { PhaseName } from "./types";
import { cn } from "../lib/utils";

type Stage = { label: string; phases: PhaseName[]; countdownS?: (run: RunView) => number | null };

const STAGES: Stage[] = [
  { label: "Boot", phases: ["preflight", "booting"] },
  { label: "Baseline", phases: ["baseline"], countdownS: (r) => r.timings?.baseline_s ?? null },
  { label: "Inject", phases: ["injecting"] },
  { label: "Soak", phases: ["soak"], countdownS: (r) => r.timings?.soak_s ?? null },
  { label: "Investigate", phases: ["investigating"] },
  { label: "Report", phases: ["report"] },
  { label: "Approve", phases: ["awaiting_approval"] },
  { label: "Execute", phases: ["executing"] },
  { label: "Recover", phases: ["recovering"] },
];

const TERMINAL: PhaseName[] = ["done", "failed", "cancelled"];

function stageIndexOf(phase: PhaseName | null): number {
  if (phase == null) return -1;
  if (TERMINAL.includes(phase)) return STAGES.length;
  return STAGES.findIndex((s) => s.phases.includes(phase));
}

function fmt(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function PhaseStepper({ run }: { run: RunView }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (run.done || run.phase == null) return;
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [run.done, run.phase]);

  const activeIdx = stageIndexOf(run.phase);
  const failed = run.phase === "failed" || run.phase === "cancelled";
  const enteredAt = run.phases.length ? run.phases[run.phases.length - 1].at_ms : null;
  const elapsedS = enteredAt != null ? (now - enteredAt) / 1000 : 0;

  return (
    <div className="flex items-stretch gap-1 overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/40 px-2 py-1.5">
      {STAGES.map((stage, i) => {
        const state =
          activeIdx > i || (activeIdx === STAGES.length && !failed)
            ? "past"
            : activeIdx === i
              ? failed
                ? "failed"
                : "active"
              : "todo";
        const countdown =
          state === "active" && stage.countdownS ? stage.countdownS(run) : null;
        return (
          <div
            key={stage.label}
            className={cn(
              "flex min-w-[74px] flex-1 flex-col items-center rounded-md px-2 py-1 text-center",
              state === "active" && "bg-sky-500/10",
              state === "failed" && "bg-rose-500/10",
            )}
          >
            <div className="flex items-center gap-1">
              {state === "past" && <Check className="h-3 w-3 text-emerald-500/80" />}
              {state === "active" && (
                <Loader2 className="h-3 w-3 animate-spin text-sky-400 motion-reduce:animate-none" />
              )}
              {state === "failed" && <X className="h-3 w-3 text-rose-400" />}
              <span
                className={cn(
                  "text-[11.5px] font-medium",
                  state === "past" && "text-slate-400",
                  state === "active" && "text-sky-300",
                  state === "failed" && "text-rose-300",
                  state === "todo" && "text-slate-600",
                )}
              >
                {stage.label}
              </span>
            </div>
            <span className="font-mono text-[10px] text-slate-500">
              {state === "active"
                ? countdown != null
                  ? `${fmt(countdown - elapsedS)} left`
                  : fmt(elapsedS)
                : " "}
            </span>
          </div>
        );
      })}
    </div>
  );
}
