// Page-level state: status polling while idle, one SSE stream + telemetry polling
// while a run is in flight. All frame folding happens in the pure reducer.

import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import * as api from "./api";
import { initialRunView, reduce, type RunView } from "./state";
import type { Frame, Status, TelemetrySeries, Topology } from "./types";

const STATUS_POLL_MS = 5000;
const TELEMETRY_POLL_MS = 15000;

export type LiveLab = {
  status: Status | null;
  statusError: string | null;
  runId: string | null;
  run: RunView;
  telemetry: TelemetrySeries | null;
  topology: Topology | null;
  mode: "live" | "replay" | null;
  busy: boolean;
  start: (target: string, preset: string) => Promise<void>;
  replay: (sourceRunId: string) => Promise<void>;
  abort: () => Promise<void>;
  decide: (verb: "approve" | "deny", reason?: string) => Promise<void>;
  boot: () => Promise<void>;
  clearFault: (target: string) => Promise<void>;
};

export function useLiveLab(): LiveLab {
  const [status, setStatus] = useState<Status | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [mode, setMode] = useState<"live" | "replay" | null>(null);
  const [telemetry, setTelemetry] = useState<TelemetrySeries | null>(null);
  const [topology, setTopology] = useState<Topology | null>(null);
  const [busy, setBusy] = useState(false);
  const [run, dispatch] = useReducer(reduce, undefined, initialRunView);
  const streamRef = useRef<api.StreamHandle | null>(null);
  const runActive = runId != null && !run.done;

  useEffect(() => {
    api.getTopology().then(setTopology).catch(() => setTopology(null));
  }, []);

  const refreshStatus = useCallback(() => {
    api
      .getStatus()
      .then((s) => {
        setStatus(s);
        setStatusError(null);
        // adopt a run already in flight (page reload, second viewer)
        if (s.run && !runId) {
          setRunId(s.run.run_id);
          setMode(s.run.mode);
        }
      })
      .catch((e) => setStatusError(String(e)));
  }, [runId]);

  useEffect(() => {
    refreshStatus();
    if (runActive) return; // the stream narrates; no need to poll status hard
    const t = setInterval(refreshStatus, STATUS_POLL_MS);
    return () => clearInterval(t);
  }, [refreshStatus, runActive]);

  // one SSE stream per adopted run; reducer dedups, so always start from 0
  useEffect(() => {
    if (!runId) return;
    dispatch({ seq: 0, event: "reset", data: {} });
    const handle = api.openRunStream(
      runId,
      0,
      (frame: Frame) => dispatch(frame),
      () => refreshStatus(),
    );
    streamRef.current = handle;
    return () => handle.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId]);

  // re-keyed on phase too: execute/recover can complete inside one poll interval,
  // and the fresh poll after each transition is what draws the recovery drop
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    const poll = () =>
      api
        .getTelemetry(runId)
        .then((t) => !cancelled && setTelemetry(t))
        .catch(() => undefined);
    poll();
    if (!runActive) return;
    const t = setInterval(poll, TELEMETRY_POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [runId, runActive, run.phase]);

  const start = useCallback(async (target: string, preset: string) => {
    setBusy(true);
    try {
      const { run_id } = await api.startRun(target, preset);
      setMode("live");
      setTelemetry(null);
      setRunId(run_id);
    } finally {
      setBusy(false);
    }
  }, []);

  const replay = useCallback(async (sourceRunId: string) => {
    setBusy(true);
    try {
      const { run_id } = await api.startReplay(sourceRunId);
      setMode("replay");
      setTelemetry(null);
      setRunId(run_id);
    } finally {
      setBusy(false);
    }
  }, []);

  const abort = useCallback(async () => {
    if (runId) await api.abortRun(runId);
  }, [runId]);

  const decideRun = useCallback(
    async (verb: "approve" | "deny", reason = "") => {
      if (runId) await api.decide(runId, verb, "dashboard", reason);
    },
    [runId],
  );

  const boot = useCallback(async () => {
    await api.bootLab();
    refreshStatus();
  }, [refreshStatus]);

  const clearFaultCb = useCallback(async (target: string) => {
    await api.clearFault(target);
  }, []);

  return {
    status,
    statusError,
    runId,
    run,
    telemetry,
    topology,
    mode,
    busy,
    start,
    replay,
    abort,
    decide: decideRun,
    boot,
    clearFault: clearFaultCb,
  };
}
