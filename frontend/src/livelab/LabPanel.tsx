// Lab control: preflight, the 13 service tiles, target/preset pickers, and the
// run controls. Everything an operator needs to trust the lab is real.

import { useState } from "react";
import { Loader2, Play, RotateCcw, Square, Wrench } from "lucide-react";
import type { RunView } from "./state";
import type { Status } from "./types";
import { cn } from "../lib/utils";

const STATE_DOT: Record<string, string> = {
  running: "bg-emerald-500",
  missing: "bg-slate-600",
  exited: "bg-rose-500",
  restarting: "bg-amber-500",
};

export function LabPanel({
  status,
  run,
  runActive,
  busy,
  onStart,
  onReplay,
  onAbort,
  onBoot,
  onClearFault,
}: {
  status: Status | null;
  run: RunView;
  runActive: boolean;
  busy: boolean;
  onStart: (target: string, preset: string) => void;
  onReplay: (sourceRunId: string) => void;
  onAbort: () => void;
  onBoot: () => void;
  onClearFault: (target: string) => void;
}) {
  const [target, setTarget] = useState("shipping");
  const [preset, setPreset] = useState("proven");
  const services = run.lab?.services ?? status?.lab.services ?? [];
  const ingest = run.lab?.ingest_age_s ?? status?.lab.ingest_age_s ?? null;
  const preflightOk = (status?.preflight ?? []).every((c) => c.ok);
  const failing = (status?.preflight ?? []).filter((c) => !c.ok);
  const labUp = services.length > 0 && services.every((s) => s.state === "running");

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Sock Shop lab
          </h3>
          <span className="font-mono text-[10.5px] text-slate-500">
            {ingest != null ? `ingest ${Math.round(ingest)}s ago` : "ingest: no data"}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1">
          {services.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-1.5 rounded border border-slate-800/70 bg-slate-950/40 px-1.5 py-1"
            >
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATE_DOT[s.state] ?? "bg-slate-600")} />
              <span className="truncate font-mono text-[10.5px] text-slate-300">{s.name}</span>
            </div>
          ))}
          {services.length === 0 && (
            <div className="col-span-2 text-[12px] text-slate-500">
              Lab is down. Boot it to pull up all 13 services.
            </div>
          )}
        </div>
        {!labUp && (
          <button
            onClick={onBoot}
            className="mt-2 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-[12.5px] text-slate-200 hover:bg-slate-800"
          >
            <Wrench className="h-3.5 w-3.5" /> Boot lab
          </button>
        )}
      </section>

      {failing.length > 0 && (
        <section className="rounded-lg border border-amber-500/30 bg-amber-500/[0.06] p-3">
          <h3 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-amber-400/80">
            Before you can run
          </h3>
          <ul className="space-y-1">
            {failing.map((c) => (
              <li key={c.name} className="text-[11.5px] leading-snug text-amber-200/80">
                {c.detail}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Live incident
        </h3>
        <label className="mb-1 block text-[11px] text-slate-500">
          CPU fault target
          <select
            value={target}
            onChange={(e) => setTarget(e.target.value)}
            disabled={runActive}
            className="mt-0.5 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-[12.5px] text-slate-200"
          >
            {(status?.targets ?? []).map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label className="mb-2 block text-[11px] text-slate-500">
          Protocol
          <select
            value={preset}
            onChange={(e) => setPreset(e.target.value)}
            disabled={runActive}
            className="mt-0.5 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 text-[12.5px] text-slate-200"
          >
            <option value="proven">proven · 3m baseline + 4m soak</option>
            <option value="quick">quick · 2m baseline + 3m soak</option>
          </select>
        </label>
        <p className="mb-2 text-[10.5px] leading-snug text-slate-600">
          Track record: 4 of 5 archived live runs localized the injected fault
          (one picked a neighbor). The agent sees only a generic symptom.
        </p>
        {runActive ? (
          <button
            onClick={onAbort}
            className="inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[13px] font-medium text-rose-300 hover:bg-rose-500/20"
          >
            <Square className="h-3.5 w-3.5" /> Abort run
          </button>
        ) : (
          <button
            onClick={() => onStart(target, preset)}
            disabled={busy || !preflightOk}
            className={cn(
              "inline-flex w-full items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[13px] font-medium",
              busy || !preflightOk
                ? "cursor-not-allowed bg-slate-800 text-slate-500"
                : "bg-sky-500 text-slate-950 hover:bg-sky-400",
            )}
          >
            {busy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            Run live incident
          </button>
        )}
        {!runActive && (
          <button
            onClick={() => onClearFault(target)}
            className="mt-1.5 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-slate-800 px-3 py-1.5 text-[11.5px] text-slate-400 hover:bg-slate-900"
          >
            <RotateCcw className="h-3 w-3" /> Clear fault on {target}
          </button>
        )}
      </section>

      {(status?.replays.length ?? 0) > 0 && !runActive && (
        <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
          <h3 className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            Replay a recorded run
          </h3>
          <div className="space-y-1">
            {status!.replays.slice(0, 5).map((r) => (
              <button
                key={r.run_id}
                onClick={() => onReplay(r.run_id)}
                className="block w-full truncate rounded-md border border-slate-800 px-2 py-1.5 text-left font-mono text-[11px] text-slate-400 hover:border-violet-500/40 hover:text-violet-300"
                title={r.run_id}
              >
                {r.run_id} · {r.phase}
              </button>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}
