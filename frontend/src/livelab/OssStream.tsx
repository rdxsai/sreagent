// The code-mode agent, rendered faithfully: the manager's topology/plan/synthesis
// records, then one panel per parallel worker showing the actual Python it wrote,
// what stdout came back, and the verdict it submitted. Chunk-by-chunk by nature
// (the oss agent doesn't stream tokens); spinners mark workers still thinking.

import { useState, type ReactNode } from "react";
import { Highlight, themes } from "prism-react-renderer";
import {
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  GitBranch,
  ListChecks,
  Loader2,
  Target,
  XCircle,
} from "lucide-react";
import type { RunView } from "./state";
import type { AgentRecord } from "./types";
import { cn } from "../lib/utils";

function Reasoning({ text }: { text?: string }) {
  if (!text?.trim()) return null;
  return (
    <div className="flex gap-2 text-slate-400">
      <Brain className="mt-0.5 h-3 w-3 shrink-0 text-slate-500" />
      <p className="whitespace-pre-wrap text-[12px] italic leading-relaxed">{text.trim()}</p>
    </div>
  );
}

function CodeBlock({ code }: { code: string }) {
  const [open, setOpen] = useState(false);
  const lines = code.trimEnd().split("\n");
  const preview = lines.slice(0, 3).join("\n");
  return (
    <div className="overflow-hidden rounded-md border border-slate-800 bg-slate-950/70">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-1.5 px-2 py-1 text-[10.5px] text-slate-500 hover:text-slate-300"
      >
        {open ? <ChevronDown className="h-3 w-3" /> : <ChevronRight className="h-3 w-3" />}
        python · {lines.length} lines
      </button>
      <Highlight theme={themes.vsDark} code={open ? code.trimEnd() : preview} language="python">
        {({ tokens, getLineProps, getTokenProps }) => (
          <pre className="overflow-x-auto px-3 pb-2 font-mono text-[11px] leading-snug" style={{ background: "transparent" }}>
            {tokens.map((line, i) => (
              <div key={i} {...getLineProps({ line })}>
                {line.map((token, k) => (
                  <span key={k} {...getTokenProps({ token })} />
                ))}
              </div>
            ))}
            {!open && lines.length > 3 && <div className="text-slate-600">…</div>}
          </pre>
        )}
      </Highlight>
    </div>
  );
}

function CodeIter({ r }: { r: AgentRecord }) {
  const failed = r.traceback != null && r.traceback !== "";
  return (
    <div className="space-y-1.5 border-l border-slate-800 pl-2.5">
      <Reasoning text={r.reasoning} />
      {r.code ? <CodeBlock code={r.code} /> : null}
      {r.stdout ? (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-md bg-slate-950/50 px-2.5 py-1.5 font-mono text-[10.5px] leading-snug text-slate-400">
          {String(r.stdout).trim()}
        </pre>
      ) : null}
      {failed && (
        <pre className="overflow-x-auto rounded-md border border-amber-500/20 bg-amber-500/[0.05] px-2.5 py-1.5 font-mono text-[10.5px] text-amber-300/80">
          {String(r.traceback).trim().split("\n").slice(-3).join("\n")}
        </pre>
      )}
    </div>
  );
}

function hypothesisName(h: unknown): string {
  if (typeof h === "string") return h;
  if (h && typeof h === "object") return String((h as any).candidate_service ?? "?");
  return "?";
}

function ManagerRecord({ r }: { r: AgentRecord }) {
  switch (r.step) {
    case "topology":
      return (
        <Line icon={<GitBranch className="h-3.5 w-3.5 text-sky-400" />}>
          built the dependency graph · source <b>{String(r.source)}</b> · {String(r.edges)} edges
          {Array.isArray(r.ranked) && r.ranked.length > 0 && (
            <span className="text-slate-500"> · anomaly ranking: {(r.ranked as string[]).slice(0, 3).join(", ")}…</span>
          )}
        </Line>
      );
    case "plan": {
      const hyps = (r.hypotheses as any[]) ?? [];
      return (
        <div className="rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2">
          <div className="mb-1 flex items-center gap-1.5 text-[11px] font-medium text-sky-300">
            <ListChecks className="h-3.5 w-3.5" /> investigation plan · {hyps.length} hypotheses
          </div>
          <Reasoning text={r.reasoning as string | undefined} />
          <ul className="mt-1 space-y-0.5">
            {hyps.map((h, i) => (
              <li key={i} className="text-[12px] text-slate-300">
                <span className="font-mono text-sky-200">{h.candidate_service}</span>
                <span className="text-slate-500"> · {h.signature}</span>
                {h.investigation_directive && (
                  <span className="text-slate-500"> — {h.investigation_directive}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      );
    }
    case "candidate_injected":
      return (
        <Line icon={<Target className="h-3.5 w-3.5 text-amber-400" />}>
          overlay added <span className="font-mono">{String(r.candidate)}</span> ({String(r.signature)} stepped
          at onset) — the manager missed it
        </Line>
      );
    case "signature_correction":
      return (
        <Line icon={<Target className="h-3.5 w-3.5 text-slate-400" />}>
          corrected <span className="font-mono">{String(r.candidate)}</span> to the observed signature
        </Line>
      );
    case "synthesize": {
      const result = (r.result as any) ?? {};
      return (
        <div className="rounded-md border border-slate-800 bg-slate-900/60 px-3 py-2">
          <div className="mb-1 text-[11px] font-medium text-sky-300">synthesis</div>
          <Reasoning text={r.reasoning as string | undefined} />
          <div className="mt-1 text-[12px] text-slate-300">
            ranked: {(result.ranked_services ?? []).join(" › ")}
          </div>
        </div>
      );
    }
    case "final_answer":
      return null; // the report card below carries the conclusion
    default:
      return (
        <Line icon={<Bot className="h-3.5 w-3.5 text-slate-500" />}>
          {String(r.step ?? r.kind)}
        </Line>
      );
  }
}

function Line({ icon, children }: { icon: ReactNode; children: ReactNode }) {
  return (
    <div className="flex items-start gap-2 text-[12px] leading-relaxed text-slate-300">
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{children}</span>
    </div>
  );
}

function WorkerPanel({ records, live }: { records: AgentRecord[]; live: boolean }) {
  const [open, setOpen] = useState(true);
  const kickoff = records.find((r) => r.kind === "worker");
  const service = hypothesisName(kickoff?.hypothesis);
  const iters = records.filter((r) => r.kind === "code_iter");
  const verdictRec = records.find((r) => r.kind === "verdict");
  const failed = records.find((r) => r.kind === "harness_fail");
  const verdict = (verdictRec?.verdict as any) ?? null;
  const running = live && !verdictRec && !failed;

  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/[0.04]">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <Bot className="h-4 w-4 shrink-0 text-indigo-400" />
        <span className="text-[12.5px] font-medium text-indigo-200">
          worker: <span className="font-mono">{service}</span>
        </span>
        {running ? (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400/70 motion-reduce:animate-none" />
        ) : verdict?.supported ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500/80" />
        ) : (
          <XCircle className="h-3.5 w-3.5 text-slate-500" />
        )}
        <span className="ml-auto text-[10.5px] text-slate-500">
          {iters.length} code iteration{iters.length === 1 ? "" : "s"}
        </span>
        {open ? <ChevronDown className="h-3.5 w-3.5 text-slate-600" /> : <ChevronRight className="h-3.5 w-3.5 text-slate-600" />}
      </button>
      {open && (
        <div className="space-y-2 border-t border-indigo-500/20 px-3 py-2.5">
          {kickoff && Array.isArray(kickoff.tool_subset) && (
            <div className="text-[10.5px] text-slate-500">
              tools: <span className="font-mono">{(kickoff.tool_subset as string[]).join(", ")}</span>
            </div>
          )}
          {iters.map((r, i) => (
            <CodeIter key={i} r={r} />
          ))}
          {failed && (
            <div className="text-[11.5px] text-amber-300/80">
              harness: {String(failed.reason)}
            </div>
          )}
          {verdict && (
            <div className="text-[12px] text-indigo-200">
              verdict: <span className="font-mono">{verdict.root_cause_service ?? service}</span> ·{" "}
              {verdict.supported ? "supported" : "not supported"} ·{" "}
              {Math.round((verdict.confidence ?? 0) * 100)}%
              {verdict.evidence?.length > 0 && (
                <ul className="mt-0.5 space-y-0.5">
                  {(verdict.evidence as string[]).slice(0, 3).map((e, i) => (
                    <li key={i} className="text-[11px] text-slate-400">
                      • {e}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ReportCard({ run }: { run: RunView }) {
  const r = run.report;
  if (!r) return null;
  return (
    <div className="space-y-2 rounded-lg border border-emerald-500/40 bg-emerald-500/[0.06] p-3.5">
      <div className="flex items-center gap-2 text-emerald-300">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-[13px] font-semibold">Root cause</span>
        <span
          className={cn(
            "ml-auto rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase",
            r.hit
              ? "border-emerald-500/40 text-emerald-300"
              : "border-amber-500/40 text-amber-300",
          )}
        >
          {r.hit ? "matches injected fault" : "differs from injected fault"}
        </span>
      </div>
      <div className="text-[13.5px] text-slate-200">
        <span className="font-mono text-emerald-200">{r.root_cause_service}</span>
        {r.fault_type && <span className="text-slate-400"> · {r.fault_type}</span>}
      </div>
      {r.justification && (
        <p className="text-[12px] leading-relaxed text-slate-300">{r.justification}</p>
      )}
      <div className="text-[11px] text-slate-500">
        ranked: {r.ranked_services.join(" › ")} · graph {r.graph_source} ·{" "}
        {(r.usage?.input ?? 0).toLocaleString()} in / {(r.usage?.output ?? 0).toLocaleString()} out tokens
      </div>
    </div>
  );
}

export function OssStream({ run }: { run: RunView }) {
  const managerRecords = run.agents["manager"] ?? [];
  const workerIds = run.agentOrder.filter((id) => id !== "manager");
  const investigating = run.phase === "investigating";

  if (managerRecords.length === 0 && workerIds.length === 0) {
    return (
      <div className="flex h-full items-center justify-center px-6 text-center text-[13px] text-slate-600">
        {investigating
          ? "The agent is reading the telemetry window…"
          : "The agent's plan, code, and findings stream here during the investigation."}
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {managerRecords
        .filter((r) => ["topology", "plan", "candidate_injected", "signature_correction"].includes(String(r.step)))
        .map((r, i) => (
          <ManagerRecord key={i} r={r} />
        ))}
      {workerIds.map((id) => (
        <WorkerPanel key={id} records={run.agents[id]} live={investigating} />
      ))}
      {managerRecords
        .filter((r) => r.step === "synthesize")
        .map((r, i) => (
          <ManagerRecord key={`s${i}`} r={r} />
        ))}
      <ReportCard run={run} />
    </div>
  );
}
