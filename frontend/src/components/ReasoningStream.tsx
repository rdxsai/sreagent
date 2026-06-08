import { useEffect, useMemo, useRef, useState } from "react";
import {
  Brain,
  Wrench,
  Bot,
  CheckCircle2,
  AlertTriangle,
  ChevronRight,
  ChevronDown,
  Target,
  Loader2,
} from "lucide-react";
import type { AgentEvent } from "../types";
import { cn } from "../lib/utils";

type Block =
  | { kind: "thinking"; text: string }
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: string; input?: unknown; result?: string; isError?: boolean }
  | { kind: "spawn"; agentId: string; service: string; subKind?: string }
  | { kind: "finding"; service: string; isOrigin?: boolean; faultType?: string | null; confidence?: number; suspect?: string | null }
  | { kind: "report"; data: any }
  | { kind: "worker_finding"; data: any }
  | { kind: "change_verdict"; data: any }
  | { kind: "status"; message: string };

function buildBlocks(events: AgentEvent[]): Block[] {
  const blocks: Block[] = [];
  for (const e of events) {
    const last = blocks[blocks.length - 1];
    if (e.type === "thinking") {
      if (last && last.kind === "thinking") last.text += e.text ?? "";
      else blocks.push({ kind: "thinking", text: e.text ?? "" });
    } else if (e.type === "text") {
      if (last && last.kind === "text") last.text += e.text ?? "";
      else blocks.push({ kind: "text", text: e.text ?? "" });
    } else if (e.type === "tool_call") {
      blocks.push({ kind: "tool", tool: e.tool ?? "", input: e.input });
    } else if (e.type === "tool_result") {
      for (let i = blocks.length - 1; i >= 0; i--) {
        const b = blocks[i];
        if (b.kind === "tool" && b.tool === e.tool && b.result === undefined) {
          b.result = e.summary;
          b.isError = e.is_error;
          break;
        }
      }
    } else if (e.type === "subagent_spawn") {
      blocks.push({ kind: "spawn", agentId: e.agent_id ?? "", service: e.service ?? "", subKind: e.kind });
    } else if (e.type === "finding") {
      blocks.push({
        kind: "finding",
        service: e.service ?? "",
        isOrigin: e.is_origin,
        faultType: e.fault_type,
        confidence: e.confidence,
        suspect: e.suspect_change_id,
      });
    } else if (e.type === "terminal") {
      if (e.tool === "report_root_cause") blocks.push({ kind: "report", data: e.data });
      else if (e.tool === "report_finding") blocks.push({ kind: "worker_finding", data: e.data });
      else if (e.tool === "report_change_verdict") blocks.push({ kind: "change_verdict", data: e.data });
    } else if (e.type === "status") {
      blocks.push({ kind: "status", message: e.message ?? "" });
    }
  }
  return blocks;
}

function ThinkingBlock({ text }: { text: string }) {
  return (
    <div className="flex gap-2 text-slate-400">
      <Brain className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
      <p className="whitespace-pre-wrap text-[13px] italic leading-relaxed">{text}</p>
    </div>
  );
}

function TextBlock({ text }: { text: string }) {
  const clean = text.replace(/<feedback>[\s\S]*?<\/feedback>/g, "").trim();
  if (!clean) return null;
  return <p className="whitespace-pre-wrap text-[13px] leading-relaxed text-slate-200">{clean}</p>;
}

function ToolBlock({ b }: { b: Extract<Block, { kind: "tool" }> }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded-md border border-slate-800 bg-slate-900/60">
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left"
      >
        <Wrench className="h-3.5 w-3.5 shrink-0 text-sky-400" />
        <span className="font-mono text-[12.5px] text-sky-300">{b.tool}</span>
        {b.isError ? (
          <AlertTriangle className="h-3 w-3 text-amber-400" />
        ) : b.result !== undefined ? (
          <CheckCircle2 className="h-3 w-3 text-emerald-500/70" />
        ) : (
          <Loader2 className="h-3 w-3 animate-spin text-slate-500" />
        )}
        {b.result !== undefined &&
          (open ? <ChevronDown className="ml-auto h-3 w-3 text-slate-600" /> : <ChevronRight className="ml-auto h-3 w-3 text-slate-600" />)}
      </button>
      {open && (
        <div className="space-y-1 border-t border-slate-800 px-3 py-2 font-mono text-[11.5px]">
          {b.input != null && Object.keys(b.input as object).length > 0 && (
            <div className="text-slate-500">args: {JSON.stringify(b.input)}</div>
          )}
          <div className={cn("break-all", b.isError ? "text-amber-300/80" : "text-slate-400")}>{b.result}</div>
        </div>
      )}
    </div>
  );
}

function FindingPill({ b }: { b: Extract<Block, { kind: "finding" }> }) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-2 rounded-md border px-2.5 py-1 text-[12px]",
        b.isOrigin ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300" : "border-slate-700 bg-slate-800/40 text-slate-400",
      )}
    >
      <Target className="h-3.5 w-3.5" />
      <span className="font-mono">{b.service}</span>
      <span>{b.isOrigin ? "origin" : "not origin"}</span>
      {b.faultType && <span className="text-slate-500">· {b.faultType}</span>}
      {typeof b.confidence === "number" && <span className="text-slate-500">· {(b.confidence * 100).toFixed(0)}%</span>}
      {b.suspect && <span className="text-slate-500">· {b.suspect}</span>}
    </div>
  );
}

function ReportCard({ data }: { data: any }) {
  const rc = data?.root_cause ?? {};
  const where = rc.kind === "edge" ? `${rc.caller} → ${rc.callee}` : rc.service;
  return (
    <div className="rounded-lg border border-emerald-500/40 bg-emerald-500/5 p-4">
      <div className="mb-2 flex items-center gap-2 text-emerald-300">
        <CheckCircle2 className="h-4 w-4" />
        <span className="text-sm font-semibold">Root cause identified</span>
      </div>
      <div className="grid gap-1.5 text-[13px]">
        <Row label="Where" value={`${where} (${rc.kind})`} />
        {rc.type && <Row label="Fault" value={rc.type} />}
        <Row label="Culprit change" value={data?.culprit_change_id} mono />
        {Array.isArray(data?.ruled_out_change_ids) && (
          <Row label="Ruled out" value={data.ruled_out_change_ids.join(", ")} mono dim />
        )}
      </div>
    </div>
  );
}

function Row({ label, value, mono, dim }: { label: string; value?: string; mono?: boolean; dim?: boolean }) {
  return (
    <div className="flex gap-2">
      <span className="w-28 shrink-0 text-slate-500">{label}</span>
      <span className={cn(mono && "font-mono", dim ? "text-slate-500" : "text-slate-200")}>{value}</span>
    </div>
  );
}

function SubagentPanel({ agentId, service, subKind, allEvents }: { agentId: string; service: string; subKind?: string; allEvents: AgentEvent[] }) {
  const [open, setOpen] = useState(true);
  const events = useMemo(() => allEvents.filter((e) => e.agent === agentId), [allEvents, agentId]);
  const done = allEvents.some((e) => e.type === "subagent_done" && e.agent_id === agentId);
  return (
    <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/[0.04]">
      <button onClick={() => setOpen((v) => !v)} className="flex w-full items-center gap-2 px-3 py-2 text-left">
        <Bot className="h-4 w-4 shrink-0 text-indigo-400" />
        <span className="text-[13px] font-medium text-indigo-200">
          {subKind === "change" ? "change investigator" : "investigator"}: <span className="font-mono">{service}</span>
        </span>
        {done ? (
          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-500/70" />
        ) : (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400/70" />
        )}
        {open ? <ChevronDown className="ml-auto h-3.5 w-3.5 text-slate-600" /> : <ChevronRight className="ml-auto h-3.5 w-3.5 text-slate-600" />}
      </button>
      {open && (
        <div className="space-y-2 border-t border-indigo-500/20 px-3 py-2.5 pl-5">
          <Blocks blocks={buildBlocks(events)} allEvents={allEvents} />
        </div>
      )}
    </div>
  );
}

function Blocks({ blocks, allEvents }: { blocks: Block[]; allEvents: AgentEvent[] }) {
  return (
    <>
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "thinking":
            return <ThinkingBlock key={i} text={b.text} />;
          case "text":
            return <TextBlock key={i} text={b.text} />;
          case "tool":
            return <ToolBlock key={i} b={b} />;
          case "finding":
            return <FindingPill key={i} b={b} />;
          case "report":
            return <ReportCard key={i} data={b.data} />;
          case "worker_finding":
            return (
              <div key={i} className="text-[12px] text-indigo-300/80">
                submitted finding: <span className="font-mono">{b.data?.service}</span> ·{" "}
                {b.data?.is_origin ? "origin" : "not origin"} · {((b.data?.confidence ?? 0) * 100).toFixed(0)}%
              </div>
            );
          case "change_verdict":
            return (
              <div key={i} className="text-[12px] text-indigo-300/80">
                verdict: <span className="font-mono">{b.data?.change_id}</span> ·{" "}
                {b.data?.is_culprit ? "culprit" : "not culprit"} · {((b.data?.confidence ?? 0) * 100).toFixed(0)}%
              </div>
            );
          case "spawn":
            return <SubagentPanel key={i} agentId={b.agentId} service={b.service} subKind={b.subKind} allEvents={allEvents} />;
          case "status":
            return (
              <div key={i} className="text-[12px] text-slate-500">
                {b.message}
              </div>
            );
        }
      })}
    </>
  );
}

export function ReasoningStream({ events, running }: { events: AgentEvent[]; running: boolean }) {
  const endRef = useRef<HTMLDivElement>(null);
  const managerEvents = useMemo(() => events.filter((e) => e.agent === "manager"), [events]);
  const blocks = useMemo(() => buildBlocks(managerEvents), [managerEvents]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [events.length]);

  if (events.length === 0) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-slate-600">
        Press Run to watch the agent investigate.
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      <Blocks blocks={blocks} allEvents={events} />
      {running && <span className="cursor text-slate-600" />}
      <div ref={endRef} />
    </div>
  );
}
