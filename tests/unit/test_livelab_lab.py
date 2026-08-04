"""Docker compose control for the Sock Shop lab, with the subprocess runner
injected so no docker is required."""
from __future__ import annotations

import json
from types import SimpleNamespace

from sentinel.api.livelab.lab import COMPOSE_FILE, Lab


def fake_run(stdout: str = "", returncode: int = 0):
    calls: list[list[str]] = []

    def run(cmd, **kwargs):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=returncode, stdout=stdout, stderr="")

    run.calls = calls
    return run


def ps_line(service: str, state: str = "running") -> str:
    return json.dumps({"Name": service, "Service": service, "State": state, "Health": ""})


def test_up_invokes_compose_with_the_lab_file() -> None:
    run = fake_run()
    Lab(run=run).up()
    assert run.calls[0][:4] == ["docker", "compose", "-f", str(COMPOSE_FILE)]
    assert "up" in run.calls[0] and "-d" in run.calls[0]


def test_ps_parses_json_lines() -> None:
    out = "\n".join([ps_line("shipping"), ps_line("catalogue", "exited")])
    lab = Lab(run=fake_run(stdout=out))
    assert lab.ps() == [{"name": "shipping", "state": "running"},
                       {"name": "catalogue", "state": "exited"}]


def test_ps_parses_json_array_form() -> None:
    out = json.dumps([{"Name": "shipping", "Service": "shipping", "State": "running"}])
    assert Lab(run=fake_run(stdout=out)).ps() == [{"name": "shipping", "state": "running"}]


def test_app_services_reports_missing_containers() -> None:
    lab = Lab(run=fake_run(stdout=ps_line("shipping")))
    services = lab.app_services()
    by_name = {s["name"]: s["state"] for s in services}
    assert len(services) == 13
    assert by_name["shipping"] == "running"
    assert by_name["payment"] == "missing"


def test_daemon_up_reflects_docker_info_exit_code() -> None:
    assert Lab(run=fake_run(returncode=0)).daemon_up() is True
    assert Lab(run=fake_run(returncode=1)).daemon_up() is False


def test_restart_targets_the_container() -> None:
    run = fake_run()
    Lab(run=run).restart("shipping")
    assert run.calls == [["docker", "restart", "shipping"]]
