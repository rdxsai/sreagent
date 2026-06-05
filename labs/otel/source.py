"""Verified OpenTelemetry Demo source configuration."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


OTEL_DEMO_REPOSITORY = "https://github.com/open-telemetry/opentelemetry-demo"
OTEL_DEMO_GIT_SHA = "b6f5c1ac7328fa57f2681e9c74574f1698847abb"
OTEL_DEMO_DEPLOYMENT_MODE = "docker-compose"

COMPOSE_FILES: tuple[str, ...] = (
    "compose.yaml",
    "compose.full.yaml",
    "compose.observability.yaml",
    "compose.extras.yaml",
)

REQUIRED_SOURCE_FILES: tuple[str, ...] = (
    "Makefile",
    ".env",
    "compose.yaml",
    "compose.full.yaml",
    "compose.observability.yaml",
    "compose.extras.yaml",
    "src/flagd/demo.flagd.json",
    "src/flagd-ui/lib/flagd_ui_web/router.ex",
    "src/flagd-ui/lib/flagd_ui_web/controllers/feature_controller.ex",
    "src/frontend-proxy/envoy.tmpl.yaml",
    "src/otel-collector/otelcol-config-observability.yml",
)

FRONTEND_PROXY_BASE_URL = "http://localhost:8080"
FLAGD_UI_BASE_URL = f"{FRONTEND_PROXY_BASE_URL}/feature"
PROMETHEUS_BASE_URL = "http://localhost:9090"
JAEGER_UI_URL = f"{FRONTEND_PROXY_BASE_URL}/jaeger/ui"


@dataclass(frozen=True)
class SourceVerification:
    root: Path
    git_sha: str | None
    missing_files: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return self.git_sha == OTEL_DEMO_GIT_SHA and not self.missing_files


def verify_source_checkout(root: Path) -> SourceVerification:
    """Verify that a local OpenTelemetry Demo checkout matches the pinned source."""

    resolved = root.resolve()
    missing = tuple(path for path in REQUIRED_SOURCE_FILES if not (resolved / path).exists())
    return SourceVerification(
        root=resolved,
        git_sha=_git_sha(resolved),
        missing_files=missing,
    )


def docker_compose_command(*args: str) -> list[str]:
    """Build the verified compose command for the pinned source layout."""

    command = ["docker", "compose", "--env-file", ".env", "--env-file", ".env.override"]
    for compose_file in COMPOSE_FILES:
        command.extend(["-f", compose_file])
    command.extend(args)
    return command


def start_command() -> list[str]:
    return docker_compose_command("up", "--force-recreate", "--remove-orphans", "--detach")


def stop_command() -> list[str]:
    return docker_compose_command("down", "--remove-orphans", "--volumes")


def _git_sha(root: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip()
