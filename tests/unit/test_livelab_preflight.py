"""Preflight: every failure mode names the problem and the fix."""
from __future__ import annotations

from sentinel.api.livelab.preflight import run_preflight


class FakeLab:
    def __init__(self, daemon: bool = True, missing: list[str] | None = None) -> None:
        self._daemon = daemon
        self._missing = missing or []

    def daemon_up(self) -> bool:
        return self._daemon

    def app_services(self) -> list[dict]:
        from labs.sockshop.faults import APP_SERVICES
        return [{"name": s, "state": "missing" if s in self._missing else "running"}
                for s in APP_SERVICES]


class FakeReader:
    def __init__(self, age: float | None) -> None:
        self._age = age

    def ingest_age_s(self) -> float | None:
        return self._age


FULL_ENV = {"NEW_RELIC_USER_KEY": "u", "NEW_RELIC_ACCOUNT_ID": "1",
            "NEW_RELIC_LICENSE_KEY": "l", "OPEN_ROUTER_API_KEY": "o"}


def by_name(checks):
    return {c.name: c for c in checks}


def test_all_green() -> None:
    checks = by_name(run_preflight(lab=FakeLab(), reader=FakeReader(30.0), env=FULL_ENV))
    assert all(c.ok for c in checks.values())
    assert set(checks) == {"docker", "lab", "new_relic_keys", "openrouter_key", "ingest"}


def test_docker_down_is_actionable() -> None:
    checks = by_name(run_preflight(lab=FakeLab(daemon=False), reader=None, env=FULL_ENV))
    assert checks["docker"].ok is False
    assert "Docker" in checks["docker"].detail


def test_missing_services_are_named_with_the_compose_command() -> None:
    checks = by_name(run_preflight(lab=FakeLab(missing=["payment", "user"]),
                                   reader=None, env=FULL_ENV))
    assert checks["lab"].ok is False
    assert "payment" in checks["lab"].detail and "user" in checks["lab"].detail
    assert "docker compose -f labs/sockshop/docker-compose.yml up -d" in checks["lab"].detail


def test_missing_env_keys_are_named() -> None:
    env = {k: v for k, v in FULL_ENV.items() if k != "NEW_RELIC_USER_KEY"}
    checks = by_name(run_preflight(lab=FakeLab(), reader=None, env=env))
    assert checks["new_relic_keys"].ok is False
    assert "NEW_RELIC_USER_KEY" in checks["new_relic_keys"].detail


def test_stale_ingest_fails_with_age() -> None:
    checks = by_name(run_preflight(lab=FakeLab(), reader=FakeReader(500.0), env=FULL_ENV))
    assert checks["ingest"].ok is False
    assert "500" in checks["ingest"].detail


def test_no_ingest_data_fails() -> None:
    checks = by_name(run_preflight(lab=FakeLab(), reader=FakeReader(None), env=FULL_ENV))
    assert checks["ingest"].ok is False


def test_reader_omitted_skips_ingest_check() -> None:
    checks = by_name(run_preflight(lab=FakeLab(), reader=None, env=FULL_ENV))
    assert "ingest" not in checks
