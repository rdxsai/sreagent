// The 13-service map, laid out with dagre — the same static topology the agent
// receives. Node color tracks latest CPU; the culprit pulses when the report lands.

import { useMemo } from "react";
import Dagre from "@dagrejs/dagre";
import { Background, Handle, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { RunView } from "./state";
import type { TelemetrySeries, Topology as TopologyData } from "./types";
import { cn } from "../lib/utils";

type ServiceNodeData = { label: string; cpu: number | null; role: "culprit" | "target" | "plain" };

function ServiceNode({ data }: NodeProps) {
  const d = data as ServiceNodeData;
  const heat =
    d.cpu == null
      ? "border-slate-700 bg-slate-900"
      : d.cpu >= 150
        ? "border-rose-500/70 bg-rose-500/10"
        : d.cpu >= 50
          ? "border-amber-500/60 bg-amber-500/10"
          : "border-slate-700 bg-slate-900";
  return (
    <div
      className={cn(
        "rounded-md border px-2 py-1 font-mono text-[10px] text-slate-200",
        heat,
        d.role === "culprit" && "animate-pulse border-rose-500 bg-rose-500/20 motion-reduce:animate-none",
      )}
    >
      <Handle type="target" position={Position.Top} className="!h-1 !w-1 !border-0 !bg-slate-600" />
      {d.label}
      {d.cpu != null && <span className="ml-1 text-slate-500">{Math.round(d.cpu)}%</span>}
      <Handle type="source" position={Position.Bottom} className="!h-1 !w-1 !border-0 !bg-slate-600" />
    </div>
  );
}

const nodeTypes = { service: ServiceNode };

function layout(topology: TopologyData, cpuBySvc: Record<string, number>, run: RunView) {
  const g = new Dagre.graphlib.Graph().setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: "TB", nodesep: 18, ranksep: 34 });
  for (const svc of topology.services) g.setNode(svc, { width: 108, height: 26 });
  for (const [a, b] of topology.edges) g.setEdge(a, b);
  Dagre.layout(g);

  const culprit = run.report?.root_cause_service;
  const nodes: Node[] = topology.services.map((svc) => {
    const pos = g.node(svc);
    return {
      id: svc,
      type: "service",
      position: { x: pos.x - 54, y: pos.y - 13 },
      data: {
        label: svc,
        cpu: cpuBySvc[svc] ?? null,
        role: svc === culprit ? "culprit" : svc === run.target ? "target" : "plain",
      } satisfies ServiceNodeData,
    };
  });
  const edges: Edge[] = topology.edges.map(([a, b]) => ({
    id: `${a}->${b}`,
    source: a,
    target: b,
    style: { stroke: "#334155", strokeWidth: 1 },
  }));
  return { nodes, edges };
}

export function TopologyMap({
  topology,
  telemetry,
  run,
}: {
  topology: TopologyData | null;
  telemetry: TelemetrySeries | null;
  run: RunView;
}) {
  const cpuBySvc = useMemo(() => {
    const out: Record<string, number> = {};
    const cpu = telemetry?.series.cpu ?? {};
    for (const [svc, points] of Object.entries(cpu)) {
      if (points.length) out[svc] = points[points.length - 1][1];
    }
    return out;
  }, [telemetry]);

  const graph = useMemo(
    () => (topology ? layout(topology, cpuBySvc, run) : null),
    [topology, cpuBySvc, run],
  );

  if (!graph) return null;
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">
          Service topology
        </h3>
        <span className="text-[10px] text-slate-600">
          the same static graph the agent plans over
        </span>
      </div>
      <div className="h-72">
        <ReactFlow
          nodes={graph.nodes}
          edges={graph.edges}
          nodeTypes={nodeTypes}
          fitView
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          zoomOnScroll={false}
          panOnDrag={false}
          preventScrolling={false}
          colorMode="dark"
        >
          <Background color="#1e293b" gap={18} size={1} />
        </ReactFlow>
      </div>
    </div>
  );
}
