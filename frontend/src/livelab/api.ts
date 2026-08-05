// REST + SSE client for the /live surface. The stream client auto-reconnects from
// the last seen seq, so a dropped connection (or a mid-run page reload paired with
// the snapshot endpoint) never loses or duplicates frames.

import type { Frame, RunSnapshot, Status, TelemetrySeries, Topology } from "./types";

const EVENT_NAMES = ["phase", "lab", "agent", "report", "action", "recovery", "error", "done"];

async function json<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const getStatus = () => fetch("/live/status").then((r) => json<Status>(r));
export const getTopology = (lab: string) =>
  fetch(`/live/topology?lab=${encodeURIComponent(lab)}`).then((r) => json<Topology>(r));
export const getSnapshot = (runId: string) =>
  fetch(`/live/runs/${runId}`).then((r) => json<RunSnapshot>(r));
export const getTelemetry = (runId: string) =>
  fetch(`/live/telemetry?run_id=${encodeURIComponent(runId)}`).then((r) => json<TelemetrySeries>(r));

export const startRun = (scenarioId: string, preset: string) =>
  fetch("/live/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario_id: scenarioId, preset }),
  }).then((r) => json<{ run_id: string }>(r));

export const startReplay = (sourceRunId: string) =>
  fetch(`/live/replays/${encodeURIComponent(sourceRunId)}`, { method: "POST" }).then((r) =>
    json<{ run_id: string }>(r),
  );

export const abortRun = (runId: string) =>
  fetch(`/live/runs/${runId}/abort`, { method: "POST" }).then((r) => json<{ ok: boolean }>(r));

export const bootLab = (lab: string) =>
  fetch("/live/lab/boot", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ lab }),
  }).then((r) => json<{ ok: boolean }>(r));

export const clearFault = (scenarioId: string) =>
  fetch("/live/lab/clear-fault", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scenario_id: scenarioId }),
  }).then((r) => json<{ ok: boolean }>(r));

export const decide = (runId: string, verb: "approve" | "deny", approver: string, reason = "") =>
  fetch(`/live/runs/${runId}/${verb}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approver, reason }),
  }).then((r) => json<{ ok: boolean }>(r));

export type StreamHandle = { close: () => void };

export function openRunStream(
  runId: string,
  after: number,
  onFrame: (frame: Frame) => void,
  onEnd: () => void,
): StreamHandle {
  let lastSeq = after;
  let closed = false;
  let es: EventSource | null = null;
  let retry: ReturnType<typeof setTimeout> | null = null;

  function open() {
    es = new EventSource(`/live/runs/${runId}/stream?after=${lastSeq}`);
    for (const name of EVENT_NAMES) {
      es.addEventListener(name, (msg: MessageEvent) => {
        const seq = Number(msg.lastEventId || 0);
        if (seq > 0) lastSeq = Math.max(lastSeq, seq);
        onFrame({ seq, event: name, data: JSON.parse(msg.data) });
        if (name === "done") {
          close();
          onEnd();
        }
      });
    }
    es.onerror = () => {
      es?.close();
      if (!closed) retry = setTimeout(open, 1500);
    };
  }

  function close() {
    closed = true;
    if (retry != null) clearTimeout(retry);
    es?.close();
  }

  open();
  return { close };
}
