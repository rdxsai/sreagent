from __future__ import annotations

from labs.otel.source import COMPOSE_FILES, docker_compose_command


def test_docker_compose_command_uses_verified_files() -> None:
    command = docker_compose_command("ps")

    assert command[:6] == ["docker", "compose", "--env-file", ".env", "--env-file", ".env.override"]
    for compose_file in COMPOSE_FILES:
        assert ["-f", compose_file] == command[
            command.index(compose_file) - 1 : command.index(compose_file) + 1
        ]
    assert command[-1] == "ps"
