"""Option B: the LiveExecutor. An approved mutate action changes REAL state on the running
OTel Demo and the executor confirms recovery from live telemetry.

  restart           -> docker restart <container-for-service>
  remove_impairment -> clear the injected fault (flagd flags back to off)  [the demo's remediation]
  scale             -> docker compose up -d --scale (best effort)

The side effects (flag reset, docker, Prometheus read) are injected callables so the gate's
security tests run against LiveExecutor with fakes, no real state touched. A kill switch
(SENTINEL_FORCE_DRYRUN=1) forces DryRunExecutor regardless of config, for safe rehearsal.

Recovery confirmation reads a health metric for the target service before applying the op and
again after a settle window, so execute_result carries an honest before/after.
"""

from __future__ import annotations

import os
import subprocess
import time
from typing import Callable

import httpx

from sentinel.actions.executor import DryRunExecutor, Executor, Outcome, _concrete_op
from sentinel.actions.models import SuggestedAction

_PROM = os.environ.get("SENTINEL_DEMO_PROM", "http://localhost:9090")
_FLAGD = os.environ.get("SENTINEL_OTEL_FLAGD_UI_BASE_URL", "http://localhost:8081/feature")

# service name -> OTel demo container name (only where they differ)
_CONTAINER = {"email": "email", "adservice": "ad", "ad": "ad", "productcatalog": "product-catalog"}


def _container_of(service: str) -> str:
    return _CONTAINER.get(service, service)


def _prom_value(query: str, *, prom: str = _PROM) -> float | None:
    try:
        r = httpx.get(f"{prom}/api/v1/query", params={"query": query}, timeout=10).json()
        res = r.get("data", {}).get("result", [])
        if not res:
            return None
        return sum(float(m["value"][1]) for m in res)
    except Exception:
        return None


def _flagd_reset() -> None:
    from labs.otel.flagd import FlagdClient
    FlagdClient(_FLAGD).reset_all_flags("off")


def _docker_restart(container: str) -> None:
    subprocess.run(["docker", "restart", container], check=False, capture_output=True, timeout=60)


class LiveExecutor:
    backend = "live"

    def __init__(
        self,
        *,
        health_query: str | None = None,       # PromQL for the target's health (the alert's expr)
        settle_s: float = 60.0,
        flag_reset: Callable[[], None] = _flagd_reset,
        restart: Callable[[str], None] = _docker_restart,
        health: Callable[[str], float | None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._health_query = health_query
        self._settle_s = settle_s
        self._flag_reset = flag_reset
        self._restart = restart
        self._sleep = sleep
        self._health = health or (lambda _svc: _prom_value(self._health_query) if self._health_query else None)

    def render(self, action: SuggestedAction) -> str:
        return _concrete_op(action)

    def execute(self, action: SuggestedAction) -> Outcome:
        t0 = time.time()
        before = self._health(action.target_service)
        try:
            if action.kind == "remove_impairment":
                self._flag_reset()
            elif action.kind == "restart":
                self._restart(_container_of(action.target_service))
            elif action.kind == "scale":
                n = int(action.params.get("replicas", 2))
                subprocess.run(["docker", "compose", "up", "-d", "--scale",
                                f"{_container_of(action.target_service)}={n}"],
                               check=False, capture_output=True, timeout=120)
            else:
                return Outcome(ok=False, error=f"live executor cannot run kind={action.kind}")
        except Exception as exc:  # a failed op is a failed outcome, not a crash
            return Outcome(ok=False, before=before, error=f"{type(exc).__name__}: {exc}",
                           duration_s=round(time.time() - t0, 2))

        self._sleep(self._settle_s)          # let the system settle
        after = self._health(action.target_service)
        # recovered if we have a health signal and it dropped meaningfully (health = badness, e.g. lag)
        recovered = before is not None and after is not None and after < before * 0.5
        return Outcome(ok=True, before=before, after=after, duration_s=round(time.time() - t0, 2),
                       error="" if (recovered or before is None) else "applied; recovery not yet confirmed")


def make_live_executor(**kw) -> Executor:
    """Factory with the kill switch: SENTINEL_FORCE_DRYRUN=1 forces a dry run regardless."""
    if os.environ.get("SENTINEL_FORCE_DRYRUN") == "1":
        return DryRunExecutor()
    return LiveExecutor(**kw)
