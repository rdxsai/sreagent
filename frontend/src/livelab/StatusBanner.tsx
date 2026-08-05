// The system-health banner: one line of truth about the lab, status-page style.
// It tracks the SYSTEM (operational -> degraded -> recovered), not the agent;
// the stepper below narrates the agent's progress.

import { AlertTriangle, CheckCircle2, CircleDot, ShieldCheck } from "lucide-react";
import type { RunView } from "./state";
import { cn } from "../lib/utils";

type Health = "operational" | "degraded" | "identified" | "recovered" | "ended";

function healthOf(run: RunView): { health: Health; title: string; sub: string } {
  const target = run.target ?? "the target";
  if (run.recovery?.recovered)
    return {
      health: "recovered",
      title: "Recovered",
      sub: `${target} back to normal after the approved remediation`,
    };
  if (run.phase === "failed")
    return { health: "ended", title: "Run failed", sub: run.error ?? "see the stream for details" };
  if (run.phase === "cancelled")
    return { health: "ended", title: "Run cancelled", sub: "fault cleared by lab hygiene" };
  if (run.report)
    return {
      health: "identified",
      title: `Root cause identified: ${run.report.root_cause_service}`,
      sub: `fault type ${run.report.fault_type ?? "unknown"} · system still degraded until remediated`,
    };
  const faultActive = run.phases.some((p) => p.phase === "injecting");
  if (faultActive)
    return {
      health: "degraded",
      title: "Degraded: injected fault active",
      sub: run.scenario?.fault_desc ?? `fault active on ${target}`,
    };
  return {
    health: "operational",
    title: "All systems operational",
    sub: "services steady under load",
  };
}

const STYLES: Record<Health, { box: string; icon: JSX.Element }> = {
  operational: {
    box: "border-emerald-500/30 bg-emerald-500/[0.07] text-emerald-300",
    icon: <ShieldCheck className="h-4 w-4" />,
  },
  degraded: {
    box: "border-amber-500/40 bg-amber-500/10 text-amber-300",
    icon: <AlertTriangle className="h-4 w-4" />,
  },
  identified: {
    box: "border-sky-500/40 bg-sky-500/10 text-sky-300",
    icon: <CircleDot className="h-4 w-4" />,
  },
  recovered: {
    box: "border-emerald-500/40 bg-emerald-500/10 text-emerald-300",
    icon: <CheckCircle2 className="h-4 w-4" />,
  },
  ended: {
    box: "border-slate-700 bg-slate-900/60 text-slate-300",
    icon: <CircleDot className="h-4 w-4" />,
  },
};

export function StatusBanner({ run, mode }: { run: RunView; mode: "live" | "replay" | null }) {
  const { health, title, sub } = healthOf(run);
  const s = STYLES[health];
  return (
    <div className={cn("flex items-center gap-3 rounded-lg border px-4 py-2.5", s.box)}>
      {s.icon}
      <div className="min-w-0">
        <div className="text-[13.5px] font-semibold leading-tight">{title}</div>
        <div className="truncate text-[11.5px] opacity-70">{sub}</div>
      </div>
      {mode === "replay" && (
        <span className="ml-auto shrink-0 rounded border border-violet-500/40 bg-violet-500/10 px-2 py-0.5 text-[10.5px] font-semibold uppercase tracking-wide text-violet-300">
          Replay
        </span>
      )}
    </div>
  );
}
