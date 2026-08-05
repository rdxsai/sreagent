// Lab control: pick a lab, watch its service tiles, pick a scenario, run it.
// Everything an operator needs to trust the lab is real.

import { useMemo, useState } from "react";
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

const LAB_NAMES: Record<string, string> = {
  sock_shop: "Sock Shop",
  otel_demo: "OTel Demo",
};

export function LabPanel({
  status,
  run,
  runActive,
  busy,
  lab,
  onLab,
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
  lab: string;
  onLab: (lab: string) => void;
  onStart: (scenarioId: string, preset: string) => void;
  onReplay: (sourceRunId: string) => void;
  onAbort: () => void;
  onBoot: () => void;
  onClearFault: (scenarioId: string) => void;
}) {
  const scenarios = useMemo(
    () => (status?.scenarios ?? []).filter((s) => s.lab === lab),
    [status, lab],
  );
  const [scenarioId, setScenarioId] = useState<string | null>(null);
  const selected =
    scenarios.find((s) => s.id === scenarioId) ?? scenarios[0] ?? null;
  const [preset, setPreset] = useState("proven");

  const runLab = run.scenario?.lab;
  const tiles =
    (runActive && runLab === lab ? run.lab?.services : null) ??
    status?.labs?.[lab] ??
    [];
  const ingest = run.lab?.ingest_age_s ?? status?.ingest_age_s ?? null;
  const preflightOk = (status?.preflight ?? []).every((c) => c.ok || c.name === "lab");
  const failing = (status?.preflight ?? []).filter((c) => !c.ok && c.name !== "lab");
  const labUp = tiles.length > 0 && tiles.every((s) => s.state === "running");
  const boot = status?.boot ?? null;
  const booting = boot?.state === "booting" && boot.lab === lab;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-1 rounded-lg border border-slate-800 bg-slate-900/60 p-0.5">
        {Object.entries(LAB_NAMES).map(([key, name]) => (
          <button
            key={key}
            onClick={() => !runActive && onLab(key)}
            disabled={runActive}
            className={cn(
              "flex-1 rounded-md px-2 py-1 text-[11.5px] font-medium transition",
              key === lab ? "bg-sky-500/15 text-sky-300" : "text-slate-500 hover:text-slate-300",
              runActive && "cursor-not-allowed opacity-60",
            )}
          >
            {name}
          </button>
        ))}
      </div>

      <section className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
        <div className="mb-2 flex items-baseline justify-between">
          <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
            {LAB_NAMES[lab]} lab
          </h3>
          <span className="font-mono text-[10.5px] text-slate-500">
            {ingest != null ? `ingest ${Math.round(ingest)}s ago` : "ingest: no data"}
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1">
          {tiles.map((s) => (
            <div
              key={s.name}
              className="flex items-center gap-1.5 rounded border border-slate-800/70 bg-slate-950/40 px-1.5 py-1"
            >
              <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", STATE_DOT[s.state] ?? "bg-slate-600")} />
              <span className="truncate font-mono text-[10.5px] text-slate-300">{s.name}</span>
            </div>
          ))}
          {tiles.length === 0 && (
            <div className="col-span-2 text-[12px] text-slate-500">
              Lab is down (or its checkout is missing). Boot it to pull the services up.
            </div>
          )}
        </div>
        {!labUp && (
          <button
            onClick={onBoot}
            disabled={booting}
            className={cn(
              "mt-2 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-[12.5px] text-slate-200 hover:bg-slate-800",
              booting && "cursor-not-allowed opacity-60",
            )}
          >
            {booting ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
            ) : (
              <Wrench className="h-3.5 w-3.5" />
            )}
            {booting ? "Booting… watch the tiles come up" : `Boot ${LAB_NAMES[lab]}`}
          </button>
        )}
        {boot?.state === "failed" && boot.lab === lab && (
          <p className="mt-1.5 text-[11px] leading-snug text-rose-300/90">
            Boot failed: {boot.detail}
          </p>
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
          Scenario
          <select
            value={selected?.id ?? ""}
            onChange={(e) => setScenarioId(e.target.value)}
            disabled={runActive}
            className="mt-0.5 w-full rounded-md border border-slate-700 bg-slate-950 px-2 py-1.5 font-mono text-[12px] text-slate-200"
          >
            {scenarios.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
        {selected && (
          <p className="mb-1.5 text-[10.5px] leading-snug text-slate-600">{selected.fault_desc}</p>
        )}
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
          The agent sees only a generic symptom and localizes across the whole lab;
          the report card shows honestly whether it matched the injected fault.
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
            onClick={() => selected && onStart(selected.id, preset)}
            disabled={busy || !preflightOk || !selected}
            className={cn(
              "inline-flex w-full items-center justify-center gap-1.5 rounded-md px-3 py-2 text-[13px] font-medium",
              busy || !preflightOk || !selected
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
        {!runActive && selected && (
          <button
            onClick={() => onClearFault(selected.id)}
            className="mt-1.5 inline-flex w-full items-center justify-center gap-1.5 rounded-md border border-slate-800 px-3 py-1.5 text-[11.5px] text-slate-400 hover:bg-slate-900"
          >
            <RotateCcw className="h-3 w-3" /> Clear this fault manually
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
