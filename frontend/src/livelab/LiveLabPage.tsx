// The live lab page: banner + timeline rail on top; lab controls and telemetry on
// the left, topology in the middle column flow, the agent's stream on the right.

import { useEffect, useRef } from "react";
import { Charts } from "./Charts";
import { LabPanel } from "./LabPanel";
import { OssStream } from "./OssStream";
import { PhaseStepper } from "./PhaseStepper";
import { RemediationCard } from "./RemediationCard";
import { StatusBanner } from "./StatusBanner";
import { TopologyMap } from "./Topology";
import { useLiveLab } from "./useLiveLab";

export function LiveLabPage() {
  const lab = useLiveLab();
  const streamEndRef = useRef<HTMLDivElement>(null);
  const agentFrames = Object.values(lab.run.agents).reduce((n, rs) => n + rs.length, 0);

  useEffect(() => {
    streamEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [agentFrames, lab.run.report, lab.run.action?.status]);

  const runActive = lab.runId != null && !lab.run.done;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <StatusBanner run={lab.run} mode={lab.mode} />
      <PhaseStepper run={lab.run} />
      {lab.statusError && !lab.status && (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] px-4 py-2 text-[12px] text-amber-200/80">
          Can't reach the Sentinel API: {lab.statusError}
        </div>
      )}
      <div className="grid min-h-0 flex-1 grid-cols-[280px_minmax(0,1fr)_minmax(360px,440px)] gap-3">
        <aside className="min-h-0 overflow-y-auto pr-0.5">
          <LabPanel
            status={lab.status}
            run={lab.run}
            runActive={runActive}
            busy={lab.busy}
            lab={lab.lab}
            onLab={lab.setLab}
            onStart={lab.start}
            onReplay={lab.replay}
            onAbort={lab.abort}
            onBoot={lab.boot}
            onClearFault={lab.clearFault}
          />
        </aside>
        <main className="min-h-0 space-y-3 overflow-y-auto pr-0.5">
          <Charts telemetry={lab.telemetry} run={lab.run} />
          <TopologyMap topology={lab.topology} telemetry={lab.telemetry} run={lab.run} />
        </main>
        <aside className="flex min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-900/40">
          <div className="border-b border-slate-800 px-4 py-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Agent investigation
          </div>
          <div className="min-h-0 flex-1 space-y-2.5 overflow-y-auto p-3.5">
            <OssStream run={lab.run} />
            <RemediationCard run={lab.run} mode={lab.mode} onDecide={(v) => lab.decide(v)} />
            {lab.run.error && (
              <div className="rounded-md border border-rose-500/30 bg-rose-500/[0.06] px-3 py-2 text-[12px] text-rose-300">
                {lab.run.error}
              </div>
            )}
            <div ref={streamEndRef} />
          </div>
        </aside>
      </div>
    </div>
  );
}
