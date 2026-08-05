"""Preflight for a live run: fail fast, name the problem, name the fix. Shown in
the dashboard's lab panel before Run is enabled."""
from __future__ import annotations

import os
from dataclasses import dataclass

_NR_KEYS = ("NEW_RELIC_USER_KEY", "NEW_RELIC_ACCOUNT_ID", "NEW_RELIC_LICENSE_KEY")
_BOOT_CMD = "docker compose -f labs/sockshop/docker-compose.yml up -d"
_FRESH_S = 120.0


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def run_preflight(*, lab, reader=None, env=os.environ) -> list[Check]:
    checks: list[Check] = []

    daemon = lab.daemon_up()
    checks.append(Check("docker", daemon,
                        "Docker daemon reachable" if daemon
                        else "Docker daemon is not running; start Docker Desktop"))

    if daemon:
        not_running = [s["name"] for s in lab.app_services() if s["state"] != "running"]
        checks.append(Check("lab", not not_running,
                            "all 13 lab services running" if not not_running
                            else f"not running: {', '.join(not_running)}; run `{_BOOT_CMD}`"))
    else:
        checks.append(Check("lab", False, f"unknown (docker down); run `{_BOOT_CMD}` once docker is up"))

    missing_nr = [k for k in _NR_KEYS if not env.get(k)]
    checks.append(Check("new_relic_keys", not missing_nr,
                        "New Relic keys present" if not missing_nr
                        else f"missing in .env: {', '.join(missing_nr)}"))

    has_or = bool(env.get("OPEN_ROUTER_API_KEY"))
    checks.append(Check("openrouter_key", has_or,
                        "OpenRouter key present" if has_or
                        else "missing in .env: OPEN_ROUTER_API_KEY (the gpt-oss agent needs it)"))

    if reader is not None:
        age = reader.ingest_age_s()
        if age is None:
            checks.append(Check("ingest", False,
                                "no Metric data in New Relic in the last 10 minutes; "
                                "is the lab's otel-collector exporting?"))
        else:
            checks.append(Check("ingest", age <= _FRESH_S,
                                f"latest metric is {age:.0f}s old"
                                + ("" if age <= _FRESH_S else " (stale; expected under 120s)")))
    return checks
