"""Host-side code executors: drive a sandbox kernel over one protocol channel.

`_KernelExecutor` owns the transport-agnostic part: the init handshake, the exec
ping-pong (answering each proxy call via the RpcHandler), and the shutdown frame.
Subclasses supply the channel and the lifecycle:

- `LocalExecutor` runs the kernel as a plain subprocess connected by a unix socket
  (tests, Docker-less dev). The socket's own per-op timeout bounds a hung exec.
- `DockerExecutor` runs the kernel in an isolated container and talks over the
  container's stdin/stdout pipes (`docker run -i`), because a host AF_UNIX socket
  bind-mounted into a Linux container cannot be connected to on macOS Docker
  Desktop. Pipes have no socket timeout, so a watchdog kills the container.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sentinel.sandbox.protocol import PipeChannel, recv_msg, send_msg
from sentinel.sandbox.rpc import RpcHandler

_SANDBOX_DIR = Path(__file__).resolve().parent
_DOCKER_IMAGE = os.environ.get("SENTINEL_SANDBOX_IMAGE", "python:3.11-slim")


@dataclass
class ExecResult:
    stdout: str
    error: str | None
    duration_ms: int


class CodeExecutor(Protocol):
    def start(self) -> None: ...
    def run(self, code: str) -> ExecResult: ...
    def close(self) -> None: ...


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


class _KernelExecutor:
    """Transport-agnostic kernel driver. Subclasses set `self._channel` in start()
    and implement `_terminate()`."""

    def __init__(self, handler: RpcHandler, client_source: str, *, timeout_s: float = 20.0) -> None:
        self.handler = handler
        self.client_source = client_source
        self.timeout_s = timeout_s
        self._channel = None

    def _terminate(self) -> None: ...

    def _handshake(self) -> None:
        send_msg(self._channel, {"type": "init", "client_source": self.client_source})
        ack = recv_msg(self._channel)
        if not ack or ack.get("type") != "init_ok":
            raise RuntimeError("sandbox failed to initialize")

    def _pump(self, code: str, started: float) -> ExecResult:
        """Send one exec and service proxy calls until the kernel returns a result."""
        send_msg(self._channel, {"type": "exec", "code": code})
        while True:
            msg = recv_msg(self._channel)
            if msg is None:
                return ExecResult("", "sandbox exited unexpectedly", _ms(started))
            if msg["type"] == "rpc":
                resp = self.handler.handle(msg["tool"], msg["args"])
                send_msg(self._channel, {"type": "rpc_result", **resp})
            elif msg["type"] == "exec_result":
                return ExecResult(msg["stdout"], msg["error"], _ms(started))

    def _send_shutdown(self) -> None:
        try:
            if self._channel is not None:
                send_msg(self._channel, {"type": "shutdown"})
        except OSError:
            pass


class _SocketExecutor(_KernelExecutor):
    """Kernel reached over a host unix socket (works when host and kernel share a
    kernel namespace, i.e. a local subprocess)."""

    def __init__(self, handler: RpcHandler, client_source: str, *, timeout_s: float = 20.0) -> None:
        super().__init__(handler, client_source, timeout_s=timeout_s)
        self._dir = Path(tempfile.mkdtemp(prefix="sentinel-sandbox-"))
        self._sock_path = self._dir / "sock"
        self._srv: socket.socket | None = None
        self._conn: socket.socket | None = None

    # subclasses implement these
    def _launch(self, sock_path: str) -> None: ...

    def start(self) -> None:
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self._sock_path))
        self._srv.listen(1)
        self._srv.settimeout(self.timeout_s)
        self._launch(str(self._sock_path))
        self._conn, _ = self._srv.accept()
        self._conn.settimeout(self.timeout_s)
        self._channel = self._conn
        self._handshake()

    def run(self, code: str) -> ExecResult:
        assert self._channel is not None, "start() not called"
        started = time.monotonic()
        try:
            return self._pump(code, started)
        except socket.timeout:
            self._terminate()
            return ExecResult("", f"sandbox timed out after {self.timeout_s:.0f}s", _ms(started))

    def close(self) -> None:
        self._send_shutdown()
        self._terminate()
        for sock in (self._conn, self._srv):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        try:
            os.remove(self._sock_path)
        except OSError:
            pass
        try:
            os.rmdir(self._dir)
        except OSError:
            pass


class LocalExecutor(_SocketExecutor):
    def _launch(self, sock_path: str) -> None:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        self._proc = subprocess.Popen(
            [sys.executable, str(_SANDBOX_DIR / "runtime.py"), sock_path],
            cwd=str(_SANDBOX_DIR),  # so `import protocol` resolves
            env=env,
        )

    def _terminate(self) -> None:
        proc = getattr(self, "_proc", None)
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()


class DockerExecutor(_KernelExecutor):
    """Kernel reached over the container's stdin/stdout pipes (`docker run -i`),
    isolated with `--network none`, a non-root user, dropped capabilities, a
    read-only rootfs, and memory/cpu/pids caps. The sandbox dir (runtime.py +
    protocol.py) is bind-mounted read-only at /app; nothing else crosses in."""

    def __init__(self, handler: RpcHandler, client_source: str, *, timeout_s: float = 60.0) -> None:
        super().__init__(handler, client_source, timeout_s=timeout_s)
        self._proc: subprocess.Popen | None = None
        self._name = f"sentinel-sandbox-{uuid.uuid4().hex[:12]}"
        self._timed_out = False

    def start(self) -> None:
        cmd = [
            "docker", "run", "-i", "--rm", "--name", self._name,
            "--network", "none",
            "--user", "65534:65534",
            "--cap-drop", "ALL",
            "--read-only",
            "--memory", "256m", "--cpus", "1", "--pids-limit", "128",
            "--tmpfs", "/tmp",
            "-e", "PYTHONDONTWRITEBYTECODE=1",
            "-v", f"{_SANDBOX_DIR}:/app:ro",
            _DOCKER_IMAGE,
            "python", "/app/runtime.py", "--stdio",
        ]
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0
        )
        self._channel = PipeChannel(self._proc.stdout, self._proc.stdin)
        self._timed_out = False
        watchdog = threading.Timer(self.timeout_s, self._on_timeout)
        watchdog.start()
        try:
            self._handshake()
        except Exception:
            watchdog.cancel()
            if self._timed_out:
                raise RuntimeError(
                    f"sandbox failed to start within {self.timeout_s:.0f}s; "
                    "ensure the image is pulled and the docker daemon is healthy"
                ) from None
            raise
        watchdog.cancel()

    def run(self, code: str) -> ExecResult:
        assert self._channel is not None, "start() not called"
        started = time.monotonic()
        self._timed_out = False
        watchdog = threading.Timer(self.timeout_s, self._on_timeout)
        watchdog.start()
        try:
            result = self._pump(code, started)
        finally:
            watchdog.cancel()
        if self._timed_out:
            return ExecResult("", f"sandbox timed out after {self.timeout_s:.0f}s", _ms(started))
        return result

    def _on_timeout(self) -> None:
        self._timed_out = True
        self._terminate()  # killing the container EOFs the pipe, unblocking _pump

    def _terminate(self) -> None:
        subprocess.run(["docker", "kill", self._name], capture_output=True)
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()

    def close(self) -> None:
        self._send_shutdown()
        self._terminate()
        proc = self._proc
        if proc is not None:
            for stream in (proc.stdin, proc.stdout):
                if stream is not None:
                    try:
                        stream.close()
                    except OSError:
                        pass


def make_executor(
    handler: RpcHandler, client_source: str, *, backend: str = "local", timeout_s: float = 20.0
) -> CodeExecutor:
    if backend == "local":
        return LocalExecutor(handler, client_source, timeout_s=timeout_s)
    if backend == "docker":
        return DockerExecutor(handler, client_source, timeout_s=timeout_s)
    raise ValueError(f"unknown executor backend: {backend}")
