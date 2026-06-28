"""Host-side code executors: drive a sandbox kernel over a unix socket.

_SocketExecutor owns the socket, the init handshake, and the exec ping-pong
(answering each proxy call via the RpcHandler). Subclasses only say how to launch
and stop the kernel process. LocalExecutor launches a plain subprocess (tests,
Docker-less dev); DockerExecutor (Task 9) launches an isolated container.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sentinel.sandbox.protocol import recv_msg, send_msg
from sentinel.sandbox.rpc import RpcHandler

_SANDBOX_DIR = Path(__file__).resolve().parent


@dataclass
class ExecResult:
    stdout: str
    error: str | None
    duration_ms: int


class CodeExecutor(Protocol):
    def start(self) -> None: ...
    def run(self, code: str) -> ExecResult: ...
    def close(self) -> None: ...


class _SocketExecutor:
    def __init__(self, handler: RpcHandler, client_source: str, *, timeout_s: float = 20.0) -> None:
        self.handler = handler
        self.client_source = client_source
        self.timeout_s = timeout_s
        self._dir = Path(tempfile.mkdtemp(prefix="sentinel-sandbox-"))
        self._sock_path = self._dir / "sock"
        self._srv: socket.socket | None = None
        self._conn: socket.socket | None = None

    # subclasses implement these
    def _launch(self, sock_path: str) -> None: ...
    def _terminate(self) -> None: ...

    def start(self) -> None:
        self._srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._srv.bind(str(self._sock_path))
        self._srv.listen(1)
        self._srv.settimeout(self.timeout_s)
        self._launch(str(self._sock_path))
        self._conn, _ = self._srv.accept()
        self._conn.settimeout(self.timeout_s)
        send_msg(self._conn, {"type": "init", "client_source": self.client_source})
        ack = recv_msg(self._conn)
        if not ack or ack.get("type") != "init_ok":
            raise RuntimeError("sandbox failed to initialize")

    def run(self, code: str) -> ExecResult:
        assert self._conn is not None, "start() not called"
        started = time.monotonic()
        try:
            send_msg(self._conn, {"type": "exec", "code": code})
            while True:
                msg = recv_msg(self._conn)
                if msg is None:
                    return ExecResult("", "sandbox exited unexpectedly", _ms(started))
                if msg["type"] == "rpc":
                    resp = self.handler.handle(msg["tool"], msg["args"])
                    send_msg(self._conn, {"type": "rpc_result", **resp})
                elif msg["type"] == "exec_result":
                    return ExecResult(msg["stdout"], msg["error"], _ms(started))
        except socket.timeout:
            self._terminate()
            return ExecResult("", f"sandbox timed out after {self.timeout_s:.0f}s", _ms(started))

    def close(self) -> None:
        try:
            if self._conn is not None:
                send_msg(self._conn, {"type": "shutdown"})
        except OSError:
            pass
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


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


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


def make_executor(
    handler: RpcHandler, client_source: str, *, backend: str = "local", timeout_s: float = 20.0
) -> CodeExecutor:
    if backend == "local":
        return LocalExecutor(handler, client_source, timeout_s=timeout_s)
    raise ValueError(f"unknown executor backend: {backend}")
