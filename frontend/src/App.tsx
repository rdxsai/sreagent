import { useEffect, useRef, useState } from "react";
import { Activity, Play, Loader2, Info, Eye, Database, BellRing } from "lucide-react";
import { fetchScenarios, runScenario } from "./api";
import type { AgentEvent, Scenario } from "./types";
import { ReasoningStream } from "./components/ReasoningStream";
import { cn } from "./lib/utils";

export default function App() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [running, setRunning] = useState(false);
  const esRef = useRef<EventSource | null>(null);

  useEffect(() => {
    fetchScenarios()
      .then((s) => {
        setScenarios(s);
        setSelectedId((cur) => cur ?? s[0]?.id ?? null);
      })
      .catch(() => setScenarios([]));
    return () => esRef.current?.close();
  }, []);

  const selected = scenarios.find((s) => s.id === selectedId) ?? null;

  function start() {
    if (!selected || running) return;
    esRef.current?.close();
    setEvents([]);
    setRunning(true);
    esRef.current = runScenario(
      selected.id,
      (e) => setEvents((prev) => [...prev, e]),
      () => setRunning(false),
    );
  }

  function select(id: string) {
    if (running) return;
    esRef.current?.close();
    setSelectedId(id);
    setEvents([]);
  }

  return (
    <div className="flex h-full flex-col">
      <Header />
      <div className="mx-auto flex w-full max-w-7xl flex-1 gap-6 overflow-hidden px-6 py-5">
        <Sidebar scenarios={scenarios} selectedId={selectedId} onSelect={select} disabled={running} />
        <main className="flex min-w-0 flex-1 flex-col">
          {selected ? (
            <>
              <ScenarioHeader scenario={selected} running={running} onRun={start} />
              <div className="mt-4 min-h-0 flex-1 overflow-y-auto rounded-xl border border-slate-800 bg-slate-900/40 p-5">
                <ReasoningStream events={events} running={running} />
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-slate-600">Loading scenarios…</div>
          )}
        </main>
      </div>
    </div>
  );
}

function Header() {
  return (
    <header className="border-b border-slate-800/80 bg-slate-950/80 px-6 py-3 backdrop-blur">
      <div className="mx-auto flex w-full max-w-7xl items-center gap-3">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-sky-500/15 text-sky-400">
          <Activity className="h-4 w-4" />
        </div>
        <div>
          <h1 className="text-sm font-semibold text-slate-100">Sentinel</h1>
          <p className="text-[11px] text-slate-500">Autonomous SRE incident investigation</p>
        </div>
        <div className="ml-auto flex items-center gap-4 text-[11px] text-slate-500">
          <span className="inline-flex items-center gap-1.5">
            <Database className="h-3.5 w-3.5" /> telemetry pre-recorded
          </span>
          <span className="inline-flex items-center gap-1.5">
            <BellRing className="h-3.5 w-3.5" /> alerts already fired
          </span>
        </div>
      </div>
    </header>
  );
}

function Sidebar({
  scenarios,
  selectedId,
  onSelect,
  disabled,
}: {
  scenarios: Scenario[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  disabled: boolean;
}) {
  return (
    <aside className="w-64 shrink-0 overflow-y-auto">
      <div className="mb-2 px-1 text-[11px] font-medium uppercase tracking-wide text-slate-600">Incidents</div>
      <div className="space-y-1.5">
        {scenarios.map((s) => (
          <button
            key={s.id}
            onClick={() => onSelect(s.id)}
            disabled={disabled}
            className={cn(
              "w-full rounded-lg border px-3 py-2 text-left transition",
              s.id === selectedId
                ? "border-sky-500/40 bg-sky-500/10"
                : "border-slate-800 bg-slate-900/40 hover:border-slate-700 hover:bg-slate-900",
              disabled && "cursor-not-allowed opacity-50",
            )}
          >
            <div className="text-[13px] font-medium text-slate-200">{s.title}</div>
            <div className="mt-0.5 truncate font-mono text-[10.5px] text-slate-600">{s.id}</div>
          </button>
        ))}
      </div>
    </aside>
  );
}

function ScenarioHeader({ scenario, running, onRun }: { scenario: Scenario; running: boolean; onRun: () => void }) {
  return (
    <div>
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-semibold text-slate-100">{scenario.title}</h2>
          {scenario.alert && (
            <div className="mt-1 inline-flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-0.5 text-[11.5px] text-amber-300">
              <BellRing className="h-3 w-3" />
              {scenario.alert}
              {scenario.alert_at_second != null && <span className="text-amber-400/60">@ {scenario.alert_at_second}s</span>}
            </div>
          )}
        </div>
        <button
          onClick={onRun}
          disabled={running}
          className={cn(
            "inline-flex shrink-0 items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition",
            running ? "cursor-not-allowed bg-slate-800 text-slate-500" : "bg-sky-500 text-slate-950 hover:bg-sky-400",
          )}
        >
          {running ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}
          {running ? "Investigating…" : "Run investigation"}
        </button>
      </div>

      <div className="mt-3 rounded-lg border border-slate-800 bg-slate-900/40 p-3.5">
        <div className="mb-1.5 inline-flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
          <Eye className="h-3.5 w-3.5" />
          For humans only — the agent does not see this description
        </div>
        <p className="text-[13.5px] leading-relaxed text-slate-300">{scenario.description}</p>
        <div className="mt-2.5 flex items-start gap-1.5 border-t border-slate-800 pt-2.5 text-[11.5px] text-slate-500">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <span>
            The agent is given only the symptom and the firing alert, then reads the recorded traces, logs, and metrics
            through its tools.
          </span>
        </div>
      </div>
    </div>
  );
}
