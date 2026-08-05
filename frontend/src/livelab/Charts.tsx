// Live telemetry, spotlight form: the fault target (and the culprit, if the agent
// names a different service) carry color; the other services are recessive context
// lines. Grafana-style annotations mark injection and shade the fault-active band.
// Colors validated against the dark surface: amber #d97706, rose #e11d48.

import { useEffect, useMemo, useRef } from "react";
import * as echarts from "echarts/core";
import { LineChart } from "echarts/charts";
import {
  GridComponent,
  MarkAreaComponent,
  MarkLineComponent,
  TooltipComponent,
} from "echarts/components";
import { CanvasRenderer } from "echarts/renderers";
import type { RunView } from "./state";
import type { TelemetrySeries } from "./types";

echarts.use([LineChart, GridComponent, TooltipComponent, MarkLineComponent, MarkAreaComponent, CanvasRenderer]);

const FAULT = "#d97706";
const CULPRIT = "#e11d48";
const CONTEXT = "#475569";
const INK_MUTED = "#64748b";
const GRID = "#1e293b";

function fmtClock(ms: number): string {
  const d = new Date(ms);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}:${String(d.getSeconds()).padStart(2, "0")}`;
}

function useEChart(option: echarts.EChartsCoreOption | null) {
  const el = useRef<HTMLDivElement>(null);
  const chart = useRef<echarts.ECharts | null>(null);
  useEffect(() => {
    const onResize = () => chart.current?.resize();
    window.addEventListener("resize", onResize);
    return () => {
      window.removeEventListener("resize", onResize);
      chart.current?.dispose();
      chart.current = null;
    };
  }, []);
  // init lazily: the canvas div only mounts once there is data, so the chart must
  // be created on the first non-null option, not on component mount
  useEffect(() => {
    if (!option || !el.current) return;
    if (!chart.current) chart.current = echarts.init(el.current);
    chart.current.setOption(option, { notMerge: true });
  }, [option]);
  return el;
}

function buildOption(
  data: Record<string, [number, number][]>,
  unit: string,
  run: RunView,
): echarts.EChartsCoreOption | null {
  const services = Object.keys(data);
  if (services.length === 0) return null;
  const target = run.target;
  const culprit = run.report?.root_cause_service ?? null;
  const injectAt = run.phases.find((p) => p.phase === "injecting")?.at_ms ?? null;
  const clearAt = run.phases.find((p) => p.phase === "executing")?.at_ms ?? null;
  const faultOver = run.actionHistory.some((a) => a.status === "execute_result" && a.ok);

  const emphasized = (svc: string) => svc === target || svc === culprit;
  const colorOf = (svc: string) =>
    svc === culprit && culprit !== target ? CULPRIT : svc === target ? FAULT : CONTEXT;

  const series = services
    .sort((a, b) => Number(emphasized(a)) - Number(emphasized(b))) // context first, spotlight on top
    .map((svc) => ({
      name: svc,
      type: "line" as const,
      showSymbol: false,
      data: data[svc],
      lineStyle: { width: emphasized(svc) ? 2 : 1, color: colorOf(svc) },
      itemStyle: { color: colorOf(svc) },
      emphasis: { lineStyle: { width: 2 } },
      endLabel: emphasized(svc)
        ? {
            show: true,
            formatter: svc,
            color: colorOf(svc),
            fontSize: 10,
            fontFamily: "ui-monospace, monospace",
          }
        : undefined,
      z: emphasized(svc) ? 3 : 1,
      markLine:
        svc === target && injectAt != null
          ? {
              silent: true,
              symbol: "none",
              lineStyle: { color: FAULT, type: "dashed", width: 1 },
              label: {
                formatter: "fault injected",
                color: FAULT,
                fontSize: 10,
                position: "insideEndTop",
              },
              data: [{ xAxis: injectAt }],
            }
          : undefined,
      markArea:
        svc === target && injectAt != null
          ? {
              silent: true,
              itemStyle: { color: "rgba(217, 119, 6, 0.06)" },
              data: [[{ xAxis: injectAt }, faultOver && clearAt != null ? { xAxis: clearAt } : { xAxis: "max" }]],
            }
          : undefined,
    }));

  return {
    animation: false,
    grid: { left: 44, right: 74, top: 14, bottom: 24 },
    tooltip: {
      trigger: "axis",
      backgroundColor: "#0f172a",
      borderColor: GRID,
      textStyle: { color: "#cbd5e1", fontSize: 11 },
      order: "valueDesc",
      formatter: (params: any[]) => {
        const rows = params
          .filter((p) => p.value?.[1] != null)
          .sort((a, b) => b.value[1] - a.value[1])
          .slice(0, 6)
          .map(
            (p) =>
              `<span style="color:${p.color === CONTEXT ? INK_MUTED : p.color}">${p.seriesName}</span>` +
              `  <b>${p.value[1].toFixed(1)}${unit}</b>`,
          );
        return `${fmtClock(params[0]?.value?.[0] ?? 0)}<br/>${rows.join("<br/>")}`;
      },
    },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: GRID } },
      axisLabel: { color: INK_MUTED, fontSize: 10, formatter: (v: number) => fmtClock(v) },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      // container-boot transients can read thousands of percent for a minute;
      // capping the axis keeps the fault step legible (tooltips show true values)
      max: unit === "%" ? (v: { max: number }) => Math.min(Math.ceil(v.max), 500) : undefined,
      axisLabel: { color: INK_MUTED, fontSize: 10, formatter: `{value}${unit}` },
      splitLine: { lineStyle: { color: GRID } },
    },
    series,
  };
}

function Chart({
  title,
  caption,
  data,
  unit,
  run,
}: {
  title: string;
  caption?: string;
  data: Record<string, [number, number][]> | undefined;
  unit: string;
  run: RunView;
}) {
  const option = useMemo(() => buildOption(data ?? {}, unit, run), [data, unit, run]);
  const el = useEChart(option);
  return (
    <div className="rounded-lg border border-slate-800 bg-slate-900/40 p-3">
      <div className="mb-1 flex items-baseline justify-between">
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-slate-500">{title}</h3>
        {caption && <span className="text-[10px] text-slate-600">{caption}</span>}
      </div>
      {option ? (
        <div ref={el} className="h-44 w-full" />
      ) : (
        <div className="flex h-44 items-center justify-center text-[12px] text-slate-600">
          Telemetry appears here once the run starts.
        </div>
      )}
    </div>
  );
}

function toPercent(data: Record<string, [number, number][]> | undefined) {
  if (!data) return undefined;
  const out: Record<string, [number, number][]> = {};
  for (const [svc, points] of Object.entries(data)) {
    out[svc] = points.map(([t, v]) => [t, v * 100] as [number, number]);
  }
  return out;
}

export function Charts({ telemetry, run }: { telemetry: TelemetrySeries | null; run: RunView }) {
  const err = telemetry?.series.err;
  const hasErr = err != null && Object.values(err).some((pts) => pts.length > 0);
  const errFirst = run.scenario?.hero_metric === "error";
  const errChart = hasErr ? (
    <Chart
      key="err"
      title="Request error rate"
      caption="from spans, per service"
      data={toPercent(err)}
      unit="%"
      run={run}
    />
  ) : null;
  return (
    <div className="flex flex-col gap-3">
      {errFirst && errChart}
      <Chart
        title="Container CPU"
        caption="trails live by 60–90s (New Relic ingest)"
        data={telemetry?.series.cpu}
        unit="%"
        run={run}
      />
      {!errFirst && errChart}
      <Chart title="Container memory" data={telemetry?.series.mem} unit="%" run={run} />
    </div>
  );
}
