"""Live smoke checks for a running OpenTelemetry Demo lab."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from labs.otel.source import (
    JAEGER_API_BASE_PATH,
    OPENSEARCH_CONTAINER_NAME,
    OPENSEARCH_CONTAINER_PORT,
    docker_published_url,
    frontend_proxy_base_url,
    prometheus_base_url,
)


@dataclass(frozen=True)
class SmokeTarget:
    name: str
    url: str


@dataclass(frozen=True)
class SmokeResult:
    name: str
    url: str
    status_code: int | None
    ok: bool
    error: str | None = None


def default_targets(
    *,
    frontend_base_url: str | None = None,
    prometheus_url: str | None = None,
    opensearch_url: str | None = None,
) -> list[SmokeTarget]:
    frontend = (frontend_base_url or frontend_proxy_base_url()).rstrip("/")
    prometheus = (prometheus_url or prometheus_base_url()).rstrip("/")
    if opensearch_url is None:
        opensearch_url = docker_published_url(OPENSEARCH_CONTAINER_NAME, OPENSEARCH_CONTAINER_PORT)
    opensearch = opensearch_url.rstrip("/")

    return [
        SmokeTarget("frontend", f"{frontend}/"),
        SmokeTarget("flagd_ui_api", f"{frontend}/feature/api/read"),
        SmokeTarget("prometheus_ready", f"{prometheus}/-/ready"),
        SmokeTarget("prometheus_query", f"{prometheus}/api/v1/query?query=up"),
        SmokeTarget("jaeger_ui", f"{frontend}/jaeger/ui/"),
        SmokeTarget("jaeger_services_api", f"{frontend}{JAEGER_API_BASE_PATH}/services"),
        SmokeTarget("opensearch_health", f"{opensearch}/_cluster/health"),
    ]


def smoke_check(
    targets: list[SmokeTarget] | None = None,
    *,
    timeout_seconds: float = 5.0,
) -> list[SmokeResult]:
    return [_check_target(target, timeout_seconds) for target in targets or default_targets()]


def _check_target(target: SmokeTarget, timeout_seconds: float) -> SmokeResult:
    request = Request(target.url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status_code = response.status
            response.read(1)
    except HTTPError as exc:
        return SmokeResult(target.name, target.url, exc.code, False, str(exc))
    except (URLError, OSError) as exc:
        return SmokeResult(target.name, target.url, None, False, str(exc))
    return SmokeResult(target.name, target.url, status_code, 200 <= status_code < 300)
