"""Contract for the fault-typing fix in run_rca: when the manager mis-guesses a candidate's
signature, the overlay's deterministic onset family must (1) re-target the worker, (2) keep an
anomalous origin as the root cause even if its worker refutes, and (3) type the fault. Hermetic:
a synthetic store + the committed sock_shop topology, with plan/worker/synthesize stubbed.
"""
from __future__ import annotations

from sentinel.fixtures.schemas import MetricRow
from sentinel.oss import rca
from sentinel.oss.schemas import Hypothesis, Plan, Synthesis
from sentinel.oss.worker import WorkerRun


class _Store:
    """Only the surface run_rca -> resolve_topology -> onset_effects touches."""

    def __init__(self, series):
        self._series = series

    def list_metric_keys(self):
        return sorted({(s, m, "ratio") for (s, m) in self._series})

    def list_services(self):
        return sorted({s for (s, _m) in self._series})

    def metric_series(self, service, metric):
        return [MetricRow(time=t, service=service, metric=metric, value=v, unit="ratio")
                for t, v in self._series.get((service, metric), [])]

    def all_spans(self):
        return []


def test_run_rca_corrects_mis_signed_signature_and_types_fault(tmp_path, monkeypatch):
    onset = 8
    # catalogue cpu steps 0.03 -> 0.34 at onset; front-end stays flat. 8 pre + 8 post points.
    step = [(t, 0.03) for t in range(onset)] + [(t, 0.34) for t in range(onset, 16)]
    flat = [(t, 0.03) for t in range(16)]
    store = _Store({("catalogue", "cpu_utilization"): step,
                    ("front-end", "cpu_utilization"): flat})

    # Manager mis-guesses catalogue as a LATENCY origin (the real failure observed live).
    def fake_plan(*_a, **_k):
        return Plan(hypotheses=[Hypothesis(candidate_service="catalogue", signature="latency",
                                           tool_subset=["metrics_summary_all"],
                                           investigation_directive="check catalogue latency")])

    captured = {}

    def fake_run_worker(_client, _preset, _store, *, hypothesis, **_k):
        captured["hypothesis"] = hypothesis
        # worker refutes the origin (harness/reasoning miss) -- must not sink an anomalous origin
        return WorkerRun(verdict={"supported": False, "observed_signatures": {}},
                         harness_fail=False, iters=1, usage={"input": 0, "output": 0})

    def fake_synthesize(*_a, **_k):
        return Synthesis(root_cause_service="catalogue", ranked_services=["catalogue"],
                         fault_type=None, justification="stub")

    monkeypatch.setattr(rca, "client_for", lambda _m: (None, None))
    monkeypatch.setattr(rca, "plan", fake_plan)
    monkeypatch.setattr(rca, "run_worker", fake_run_worker)
    monkeypatch.setattr(rca, "synthesize", fake_synthesize)

    result = rca.run_rca(store, incident="something is slow", out_dir=tmp_path,
                         system="sock_shop", onset=onset, run_id="t", worker_concurrency=1)

    # 1. the worker was re-targeted from the manager's latency guess to the overlay's resource step
    assert "[resource]" in captured["hypothesis"]
    assert "cpu" in captured["hypothesis"]
    # 2. an anomalous origin survives its own worker's refutation (deterministic pick)
    assert result.root_cause_service == "catalogue"
    # 3. fault typed deterministically from the overlay even though synthesize returned null
    assert result.synthesis["fault_type"] == "resource saturation"
    # 4. the origin's verdict is reconciled to evidence-backed despite the LLM saying unsupported
    cat_v = next(v for v in result.verdicts if v.get("candidate_service") == "catalogue")
    assert cat_v["supported"] is True
    assert cat_v["observed_signatures"]["resource"] is True
    assert cat_v["signature"] == "resource"


def test_run_rca_injects_missing_anomalous_origin(tmp_path, monkeypatch):
    """When the manager omits the anomalous #1 origin entirely, run_rca injects a hypothesis for
    it (signed by the stepped family) so a worker confirms it with evidence."""
    onset = 8
    step = [(t, 0.03) for t in range(onset)] + [(t, 0.34) for t in range(onset, 16)]
    flat = [(t, 0.03) for t in range(16)]
    store = _Store({("catalogue", "cpu_utilization"): step,
                    ("front-end", "cpu_utilization"): flat})

    # Manager proposes only a quiet victim, never the anomalous origin.
    def fake_plan(*_a, **_k):
        return Plan(hypotheses=[Hypothesis(candidate_service="front-end", signature="latency",
                                           tool_subset=["metrics_summary_all"],
                                           investigation_directive="check front-end")])

    seen = []

    def fake_run_worker(_client, _preset, _store, *, hypothesis, **_k):
        seen.append(hypothesis)
        # even the injected origin's worker fumbles the label; the overlay must reconcile it
        return WorkerRun(verdict={"supported": False, "observed_signatures": {}},
                         harness_fail=False, iters=1, usage={"input": 0, "output": 0})

    monkeypatch.setattr(rca, "client_for", lambda _m: (None, None))
    monkeypatch.setattr(rca, "plan", fake_plan)
    monkeypatch.setattr(rca, "run_worker", fake_run_worker)
    monkeypatch.setattr(rca, "synthesize",
                        lambda *_a, **_k: Synthesis(root_cause_service="catalogue",
                                                    ranked_services=["catalogue"], fault_type=None))

    result = rca.run_rca(store, incident="x", out_dir=tmp_path, system="sock_shop", onset=onset,
                         run_id="t3", worker_concurrency=1)

    # the anomalous origin the manager missed was injected as a [resource] candidate and investigated
    assert any(h.startswith("[resource] Candidate origin: catalogue") and "cpu" in h for h in seen)
    assert result.root_cause_service == "catalogue"
    assert result.synthesis["fault_type"] == "resource saturation"
    # its verdict is reconciled to supported despite the worker's unsupported label
    cat_v = next(v for v in result.verdicts if v.get("candidate_service") == "catalogue")
    assert cat_v["supported"] is True and cat_v["observed_signatures"]["resource"] is True


def test_run_rca_keeps_manager_signature_for_quiet_candidate(tmp_path, monkeypatch):
    """A non-anomalous (quiet) candidate keeps the manager's framing: no false correction."""
    onset = 8
    flat = [(t, 0.03) for t in range(16)]
    store = _Store({("front-end", "cpu_utilization"): flat})

    def fake_plan(*_a, **_k):
        return Plan(hypotheses=[Hypothesis(candidate_service="front-end", signature="latency",
                                           tool_subset=["metrics_summary_all"],
                                           investigation_directive="rule out front-end latency")])

    captured = {}

    def fake_run_worker(_client, _preset, _store, *, hypothesis, **_k):
        captured["hypothesis"] = hypothesis
        return WorkerRun(verdict={"supported": False, "observed_signatures": {}},
                         harness_fail=False, iters=1, usage={"input": 0, "output": 0})

    monkeypatch.setattr(rca, "client_for", lambda _m: (None, None))
    monkeypatch.setattr(rca, "plan", fake_plan)
    monkeypatch.setattr(rca, "run_worker", fake_run_worker)
    monkeypatch.setattr(rca, "synthesize",
                        lambda *_a, **_k: Synthesis(root_cause_service="front-end",
                                                    ranked_services=["front-end"], fault_type=None))

    rca.run_rca(store, incident="x", out_dir=tmp_path, system="sock_shop", onset=onset,
                run_id="t2", worker_concurrency=1)
    # quiet candidate: manager's latency framing is untouched (no overlay step to correct to)
    assert "[latency]" in captured["hypothesis"]
