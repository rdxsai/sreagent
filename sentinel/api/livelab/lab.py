"""Docker compose control for the labs. Thin, injectable wrappers: the state
machine and router depend on this seam, tests inject a fake runner, and the real
runner is subprocess.run. One Lab instance per lab, parameterized by compose file,
app-service list, and (optionally) a boot command run in a working directory (the
OTel Demo boots via its own `make start`)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from labs.sockshop.faults import APP_SERVICES

COMPOSE_FILE = Path("labs/sockshop/docker-compose.yml")


class Lab:
    def __init__(self, *, run=subprocess.run, compose_file: Path = COMPOSE_FILE,
                 services: tuple[str, ...] = APP_SERVICES,
                 boot_cmd: list[str] | None = None, workdir: Path | None = None) -> None:
        self._run = run
        self._compose_file = compose_file
        self._services = services
        self._boot_cmd = boot_cmd
        self._workdir = workdir

    def _compose(self, *args: str, timeout: int = 180):
        cmd = ["docker", "compose", "-f", str(self._compose_file), *args]
        return self._run(cmd, capture_output=True, text=True, timeout=timeout,
                         **({"cwd": str(self._workdir)} if self._workdir else {}))

    def up(self) -> None:
        if self._boot_cmd is not None:
            self._run(self._boot_cmd, capture_output=True, text=True, timeout=900,
                      **({"cwd": str(self._workdir)} if self._workdir else {}))
            return
        self._compose("up", "-d", timeout=600)

    def ps(self) -> list[dict]:
        """[{name, state}] for every compose container, tolerant of both the
        JSON-lines (compose >= 2.21) and JSON-array output forms."""
        proc = self._compose("ps", "-a", "--format", "json")
        text = (proc.stdout or "").strip()
        if not text:
            return []
        try:
            rows = json.loads(text)
            if isinstance(rows, dict):
                rows = [rows]
        except json.JSONDecodeError:
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        return [{"name": r.get("Service") or r.get("Name", ""),
                 "state": (r.get("State") or "unknown").lower()} for r in rows]

    def app_services(self) -> list[dict]:
        """This lab's app services, each present (with its compose state) or 'missing'."""
        states = {row["name"]: row["state"] for row in self.ps()}
        return [{"name": s, "state": states.get(s, "missing")} for s in self._services]

    def daemon_up(self) -> bool:
        try:
            proc = self._run(["docker", "info"], capture_output=True, text=True, timeout=10)
            return proc.returncode == 0
        except Exception:
            return False

    def restart(self, container: str) -> None:
        self._run(["docker", "restart", container], capture_output=True, text=True, timeout=60)
