// The gated remediation: one reversible action, the exact op it would run, and a
// human decision. The buttons drive the same journal + gate as the Slack surface;
// nothing executes without the approval, and the card narrates each journal state.

import { useState } from "react";
import { Check, Loader2, ShieldAlert, ShieldCheck, X } from "lucide-react";
import type { RunView } from "./state";
import { cn } from "../lib/utils";

export function RemediationCard({
  run,
  mode,
  onDecide,
}: {
  run: RunView;
  mode: "live" | "replay" | null;
  onDecide: (verb: "approve" | "deny") => Promise<void>;
}) {
  const [deciding, setDeciding] = useState(false);
  const frame = run.action;
  if (!frame) return null;
  const a = frame.action;
  const awaiting = frame.status === "posted" && run.phase === "awaiting_approval";
  const outcome = frame.outcome;

  async function decide(verb: "approve" | "deny") {
    setDeciding(true);
    try {
      await onDecide(verb);
    } finally {
      setDeciding(false);
    }
  }

  return (
    <div className="space-y-2.5 rounded-lg border border-sky-500/40 bg-sky-500/[0.06] p-3.5">
      <div className="flex items-center gap-2 text-sky-300">
        <ShieldAlert className="h-4 w-4" />
        <span className="text-[13px] font-semibold">Proposed remediation</span>
        <span className="ml-auto flex gap-1">
          <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
            risk {a.risk}
          </span>
          {a.reversible && (
            <span className="rounded border border-slate-700 px-1.5 py-0.5 text-[10px] uppercase text-slate-400">
              reversible
            </span>
          )}
        </span>
      </div>

      <p className="text-[12.5px] text-slate-200">{a.description}</p>
      <pre className="rounded-md bg-slate-950/60 px-2.5 py-1.5 font-mono text-[11px] text-slate-300">
        {a.preview}
      </pre>
      {a.citations.length > 0 && (
        <div className="text-[11px] text-slate-500">evidence: {a.citations[0]}</div>
      )}

      {awaiting && mode === "live" && (
        <div className="flex gap-2 pt-0.5">
          <button
            onClick={() => decide("approve")}
            disabled={deciding}
            className={cn(
              "inline-flex flex-1 items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[13px] font-medium",
              deciding
                ? "cursor-not-allowed bg-slate-800 text-slate-500"
                : "bg-emerald-500 text-slate-950 hover:bg-emerald-400",
            )}
          >
            {deciding ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
            ) : (
              <Check className="h-3.5 w-3.5" />
            )}
            Approve restart
          </button>
          <button
            onClick={() => decide("deny")}
            disabled={deciding}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-md border border-slate-700 px-3 py-2 text-[13px] font-medium text-slate-300 hover:bg-slate-800"
          >
            <X className="h-3.5 w-3.5" /> Deny
          </button>
        </div>
      )}
      {awaiting && mode === "replay" && (
        <div className="text-[11.5px] text-slate-500">
          In the recorded run, the operator decided here.
        </div>
      )}

      {frame.status === "approved" && (
        <StatusLine tone="sky">
          approved by {frame.approver} · executing under the single-use gate…
        </StatusLine>
      )}
      {frame.status === "denied" && (
        <StatusLine tone="slate">denied by {frame.approver} · nothing executed · fault swept by lab hygiene</StatusLine>
      )}
      {frame.status === "expired" && (
        <StatusLine tone="slate">approval window expired · nothing executed</StatusLine>
      )}
      {frame.status === "execute_result" && (
        <StatusLine tone={frame.ok ? "emerald" : "amber"}>
          {frame.ok ? "executed" : `execution failed: ${outcome?.error ?? "unknown"}`}
          {outcome?.before != null && (
            <>
              {" "}
              · CPU {Math.round(outcome.before)}% → {outcome.after != null ? `${Math.round(outcome.after)}%` : "…"}
            </>
          )}
        </StatusLine>
      )}
      {run.recovery?.recovered && (
        <div className="flex items-center gap-1.5 text-[12px] text-emerald-300">
          <ShieldCheck className="h-3.5 w-3.5" /> recovery confirmed from live telemetry
        </div>
      )}
    </div>
  );
}

function StatusLine({ tone, children }: { tone: "sky" | "emerald" | "amber" | "slate"; children: React.ReactNode }) {
  const tones = {
    sky: "text-sky-300",
    emerald: "text-emerald-300",
    amber: "text-amber-300",
    slate: "text-slate-400",
  } as const;
  return <div className={cn("text-[12px]", tones[tone])}>{children}</div>;
}
