"""Build isolated, persisted LGTM stacks for a set of RCAEval scenarios.

Per scenario: convert at the full 24-min window -> bring up a stack with its OWN
compose project (so volumes are separate), OWN host ports -> feed via OTLP ->
verify Prometheus has the data -> stop (named volumes persist across stop). Writes
rcaeval/library/registry.json mapping case_id -> {project, ports, onset}. The test
runner brings a scenario up on demand and queries its ports.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from labs.lgtm.feed import feed  # noqa: E402
from sentinel_rcaeval.re2 import convert_re2_case  # noqa: E402

LGTM = ROOT / "labs/lgtm"
LIB = ROOT / "rcaeval/library"
RAW = {"ob": ROOT / "rcaeval/raw/RE2-OB/RE2-OB", "ss": ROOT / "rcaeval/raw/RE2-SS/RE2-SS"}
COMPOSE = str(LGTM / "docker-compose.yml")
ONSET = 720

# (case_id, system, service, fault)  -- tough-5 (run w/ Opus) + unseen-5
SCENARIOS = [
    ("ob_productcatalogservice_delay_1", "ob", "productcatalogservice", "delay"),
    ("ob_currencyservice_loss_1", "ob", "currencyservice", "loss"),
    ("ob_checkoutservice_delay_1", "ob", "checkoutservice", "delay"),
    ("ob_emailservice_cpu_1", "ob", "emailservice", "cpu"),
    ("ob_emailservice_mem_1", "ob", "emailservice", "mem"),
    ("ob_currencyservice_socket_1", "ob", "currencyservice", "socket"),
    ("ob_recommendationservice_socket_1", "ob", "recommendationservice", "socket"),
    ("ss_orders_delay_1", "ss", "orders", "delay"),
    ("ss_payment_loss_1", "ss", "payment", "loss"),
    ("ss_catalogue_cpu_1", "ss", "catalogue", "cpu"),
]


def _ports(i: int) -> dict:
    # i starts at 1 so we never collide with the existing rec_cpu stack (i=0 ports)
    return {"prom": 9090 + i, "loki": 3100 + i, "tempo": 3200 + i, "otlp": 4318 + i}


def _compose(project: str, ports: dict, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "COMPOSE_PROJECT_NAME": project,
           "PROM_PORT": str(ports["prom"]), "LOKI_PORT": str(ports["loki"]),
           "TEMPO_PORT": str(ports["tempo"]), "OTLP_PORT": str(ports["otlp"])}
    return subprocess.run(["docker", "compose", "-f", COMPOSE, *args],
                          env=env, capture_output=True, text=True)


def _wait_ready(ports: dict, timeout: int = 120) -> bool:
    c = httpx.Client(timeout=5)
    urls = {"prom": f"http://localhost:{ports['prom']}/-/ready",
            "loki": f"http://localhost:{ports['loki']}/ready",
            "tempo": f"http://localhost:{ports['tempo']}/ready"}
    ok: set = set()
    deadline = time.time() + timeout
    while time.time() < deadline and len(ok) < 3:
        for k, u in urls.items():
            if k in ok:
                continue
            try:
                if c.get(u).status_code == 200:
                    ok.add(k)
            except Exception:
                pass
        if len(ok) < 3:
            time.sleep(3)
    return len(ok) == 3


def _feed_with_retry(scenario_dir: Path, otlp_port: int, tries: int = 5) -> dict:
    last = None
    for _ in range(tries):
        try:
            return feed(scenario_dir, f"http://localhost:{otlp_port}", ONSET)
        except Exception as exc:  # collector may need a moment after the stores
            last = exc
            time.sleep(4)
    raise RuntimeError(f"feed failed: {last}")


def _verify_prom(ports: dict) -> int:
    c = httpx.Client(timeout=10)
    r = c.get(f"http://localhost:{ports['prom']}/api/v1/label/job/values").json()
    return len(r.get("data", []))


def main() -> int:
    LIB.mkdir(parents=True, exist_ok=True)
    reg_path = LIB / "registry.json"
    registry = json.loads(reg_path.read_text()) if reg_path.exists() else {}
    for i, (cid, sysid, svc, fault) in enumerate(SCENARIOS, start=1):
        ports = _ports(i)
        project = "lgtm-" + cid.replace("_", "-")
        raw = RAW[sysid] / f"{svc}_{fault}" / "1"
        print(f"\n=== [{i}/{len(SCENARIOS)}] {cid}  project={project} ports={ports} ===", flush=True)
        if not raw.exists():
            print(f"[skip] no raw at {raw}", flush=True)
            continue
        try:
            out = convert_re2_case(cid, svc, fault, raw, LIB, pre=720, post=720, cap=100000)
            up = _compose(project, ports, "up", "-d")
            if up.returncode != 0:
                print(f"[FAIL] compose up: {up.stderr[-300:]}", flush=True)
                continue
            if not _wait_ready(ports):
                print("[FAIL] stores not ready", flush=True)
                _compose(project, ports, "stop")
                continue
            w = _feed_with_retry(out, ports["otlp"])
            svc_count = _verify_prom(ports)
            _compose(project, ports, "stop")  # persist volumes, free RAM
            registry[cid] = {"project": project, "ports": ports, "onset": ONSET,
                             "system": sysid, "service": svc, "fault": fault,
                             "fed": w["fed"], "services_in_prometheus": svc_count}
            reg_path.write_text(json.dumps(registry, indent=2))
            print(f"[ok] fed={w['fed']} prometheus_services={svc_count} -> stopped (volumes persist)", flush=True)
        except Exception as exc:
            print(f"[ERROR] {cid}: {exc!r}", flush=True)
            _compose(project, ports, "stop")
    print(f"\nDONE: {len(registry)} scenarios in {reg_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
