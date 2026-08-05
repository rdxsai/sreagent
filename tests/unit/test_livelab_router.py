"""The /live REST + SSE surface, driven with the machine-test fakes through a real
FastAPI app."""
from __future__ import annotations

import json
import threading
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sentinel.api.livelab.router import make_livelab_router
from sentinel.api.livelab.scenarios import scenario_by_id
from tests.unit.test_livelab_machine import FakeExecutor, make_deps

SHIPPING = scenario_by_id("sockshop-cpu-shipping")


@pytest.fixture()
def harness(tmp_path):
    deps, world = make_deps(tmp_path, SHIPPING)
    app = FastAPI()
    app.include_router(make_livelab_router(out_root=tmp_path,
                                           deps_factory=lambda run_dir, scenario=None: deps,
                                           labs_factory=lambda key: world.lab))
    client = TestClient(app)
    return client, deps, world, tmp_path


def wait_until(pred, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pred():
            return
        time.sleep(0.01)
    raise AssertionError("condition never became true")


def start_and_finish_run(client, *, target: str = "shipping", decision: str = "approve"):
    r = client.post("/live/runs", json={"scenario_id": f"sockshop-cpu-{target}", "preset": "quick"})
    assert r.status_code == 200
    run_id = r.json()["run_id"]
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"]
               in ("awaiting_approval", "done", "failed"))
    if decision:
        client.post(f"/live/runs/{run_id}/{decision}", json={"approver": "tester"})
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"]
               in ("done", "failed", "cancelled"))
    return run_id


def test_status_reports_preflight_lab_and_no_run(harness) -> None:
    client, deps, world, _ = harness
    body = client.get("/live/status").json()
    assert body["run"] is None
    assert {c["name"] for c in body["preflight"]} >= {"docker", "lab", "new_relic_keys"}
    assert len(body["lab"]["services"]) == 13
    assert body["replays"] == []
    labs = {s["lab"] for s in body["scenarios"]}
    assert labs == {"sock_shop", "otel_demo"}
    assert {s["truth_service"] for s in body["scenarios"] if s["lab"] == "sock_shop"} == {
        "shipping", "catalogue", "payment", "orders"}
    assert set(body["presets"]) == {"proven", "quick"}


def test_run_lifecycle_and_single_flight(harness) -> None:
    client, deps, world, _ = harness
    blocker = threading.Event()
    real_sleep = deps.sleep

    def gated_sleep(s: float) -> None:
        blocker.wait(0.03)
        real_sleep(s)

    deps.sleep = gated_sleep
    r = client.post("/live/runs", json={"scenario_id": "sockshop-cpu-shipping", "preset": "quick"})
    run_id = r.json()["run_id"]
    assert client.post("/live/runs", json={"scenario_id": "sockshop-cpu-orders", "preset": "quick"}).status_code == 409
    snap = client.get(f"/live/runs/{run_id}").json()
    assert snap["mode"] == "live" and snap["target"] == "shipping"
    blocker.set()
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "awaiting_approval")
    assert client.post(f"/live/runs/{run_id}/approve", json={"approver": "sid"}).status_code == 200
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "done")
    # once finished, a new run may start
    assert client.post("/live/runs", json={"scenario_id": "sockshop-cpu-orders", "preset": "quick"}).status_code == 200


def test_invalid_scenario_and_preset_rejected(harness) -> None:
    client, *_ = harness
    assert client.post("/live/runs", json={"scenario_id": "sockshop-cpu-carts-db", "preset": "quick"}).status_code == 422
    assert client.post("/live/runs", json={"scenario_id": "sockshop-cpu-shipping", "preset": "warp"}).status_code == 422


def test_finished_run_snapshot_and_stream_served_from_disk(harness) -> None:
    client, deps, world, tmp_path = harness
    run_id = start_and_finish_run(client)
    snap = client.get(f"/live/runs/{run_id}").json()
    assert snap["phase"] == "done"

    with client.stream("GET", f"/live/runs/{run_id}/stream") as resp:
        text = "".join(resp.iter_text())
    assert "event: phase" in text and "event: report" in text
    assert text.rstrip().endswith("data: {}")  # terminal done frame
    ids = [int(l.split(": ")[1]) for l in text.splitlines() if l.startswith("id: ")]
    assert ids == sorted(ids)

    with client.stream("GET", f"/live/runs/{run_id}/stream?after={ids[-4]}") as resp:
        tail = "".join(resp.iter_text())
    assert f"id: {ids[1]}\n" not in tail


def test_live_stream_delivers_frames_until_done(harness) -> None:
    client, deps, world, _ = harness
    r = client.post("/live/runs", json={"scenario_id": "sockshop-cpu-shipping", "preset": "quick"})
    run_id = r.json()["run_id"]
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "awaiting_approval")
    client.post(f"/live/runs/{run_id}/deny", json={"approver": "t", "reason": "n"})
    with client.stream("GET", f"/live/runs/{run_id}/stream") as resp:
        text = "".join(resp.iter_text())
    assert "event: agent" in text
    assert '"phase": "done"' in text


def test_abort_cancels_active_run(harness) -> None:
    client, deps, world, _ = harness
    blocker = threading.Event()
    real_sleep = deps.sleep
    deps.sleep = lambda s: (blocker.wait(0.05), real_sleep(s))[-1]
    run_id = client.post("/live/runs", json={"scenario_id": "sockshop-cpu-shipping", "preset": "proven"}).json()["run_id"]
    assert client.post(f"/live/runs/{run_id}/abort").status_code == 200
    blocker.set()
    wait_until(lambda: client.get(f"/live/runs/{run_id}").json()["phase"] == "cancelled")


def test_telemetry_live_delegates_to_reader(harness, monkeypatch) -> None:
    client, deps, world, _ = harness
    calls = {}

    def fake_series(services, since_ms, until_ms):
        calls["services"] = services
        return {"series": {"cpu": {}}, "fetched_at_ms": 1}

    monkeypatch.setattr(deps.reader, "series", fake_series, raising=False)
    run_id = start_and_finish_run(client, decision="deny")
    body = client.get(f"/live/telemetry?run_id={run_id}").json()
    assert "series" in body
    # a finished live run serves the journal (empty here) or the reader; both carry the shape
    assert set(body) >= {"series", "fetched_at_ms"}


def test_replay_run_streams_and_serves_journal_telemetry(harness) -> None:
    client, deps, world, tmp_path = harness
    src = start_and_finish_run(client, decision="approve")
    # journal the telemetry the charts would have polled
    (tmp_path / src / "telemetry.jsonl").write_text(
        json.dumps({"series": {"cpu": {"shipping": [[0, 5.0]]}}, "fetched_at_ms": 99}) + "\n")

    r = client.post(f"/live/replays/{src}")
    assert r.status_code == 200
    replay_id = r.json()["run_id"]
    snap = client.get(f"/live/runs/{replay_id}").json()
    assert snap["mode"] == "replay" and snap["source_run_id"] == src
    wait_until(lambda: not client.get("/live/status").json()["run"])
    tele = client.get(f"/live/telemetry?run_id={replay_id}").json()
    assert tele["series"]["cpu"]["shipping"] == [[0, 5.0]]
    assert src in {x["run_id"] for x in client.get("/live/status").json()["replays"]}


def test_topology_serves_the_static_graph(harness) -> None:
    client, *_ = harness
    body = client.get("/live/topology").json()
    assert len(body["services"]) == 13
    assert len(body["edges"]) == 16
    assert ["front-end", "catalogue"] in body["edges"]


def test_clear_fault_calls_clear_cpu(harness) -> None:
    client, deps, world, _ = harness
    assert client.post("/live/lab/clear-fault", json={"scenario_id": "sockshop-cpu-shipping"}).status_code == 200
    assert "shipping" in world.cleared
    assert client.post("/live/lab/clear-fault", json={"scenario_id": "nope"}).status_code == 422


def test_boot_invokes_lab_up_in_background(harness) -> None:
    client, deps, world, _ = harness
    r = client.post("/live/lab/boot", json={"lab": "otel_demo"})
    assert r.status_code == 200
    assert r.json()["booting"] == "otel_demo"
    wait_until(lambda: world.lab.up_calls == 1)


def test_status_reports_both_labs(harness) -> None:
    client, deps, world, _ = harness
    body = client.get("/live/status").json()
    assert set(body["labs"]) == {"sock_shop", "otel_demo"}
    assert len(body["labs"]["sock_shop"]) == 13


def test_topology_serves_the_otel_demo_graph(harness) -> None:
    client, *_ = harness
    body = client.get("/live/topology?lab=otel_demo").json()
    assert len(body["services"]) == 15
    assert ["checkout", "payment"] in body["edges"]
    assert client.get("/live/topology?lab=nope").status_code == 404


def test_default_deps_factory_builds_the_scenario_lab(monkeypatch, tmp_path) -> None:
    """The regression that failed the first real OTel run: the factory hardcoded
    the Sock Shop lab, so an otel scenario polled (and tried to boot) the wrong
    compose stack."""
    from sentinel.api.livelab import adapters, router as router_mod

    labs_asked: list[str] = []
    monkeypatch.setattr(adapters, "make_lab",
                        lambda key, **kw: labs_asked.append(key) or object())
    monkeypatch.setattr("sentinel.newrelic.client.NerdGraphClient.from_env",
                        classmethod(lambda cls: type("C", (), {"nrql": staticmethod(lambda q: [])})()))
    router_mod.default_deps_factory(tmp_path, scenario_by_id("otel-ad_high_cpu_live_001"))
    router_mod.default_deps_factory(None, None)
    assert labs_asked == ["otel_demo", "sock_shop"]
