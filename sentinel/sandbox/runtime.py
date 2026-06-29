"""Sandbox kernel entrypoint. Stdlib only; runs inside the container.

It drives one protocol channel (a unix socket for LocalExecutor, or the process's
stdin/stdout pipes for DockerExecutor). Same message protocol either way:
  host -> {"type":"init","client_source": "..."}     ; we exec it once
  host <- {"type":"init_ok"}
  host -> {"type":"exec","code":"..."}                ; run in the persistent ns
    (during exec) host <- {"type":"rpc","tool":..,"args":..}
                  host -> {"type":"rpc_result","ok":..,"result"/"error":..}
  host <- {"type":"exec_result","stdout":"...","error": null | "traceback"}
  host -> {"type":"shutdown"}                          ; exit

stdio mode (`--stdio`): the protocol owns the process's real stdout (fd 1). We
dup it aside for the protocol writer and point fd 1 at stderr, so any stray
fd-1 write goes to stderr (which the host discards), never into the framing.
User print() is already captured separately by redirect_stdout during exec.
"""

from __future__ import annotations

import contextlib
import io
import os
import socket
import sys
import traceback

from protocol import PipeChannel, recv_msg, send_msg  # bind-mounted alongside this file


class _ToolError(Exception):
    pass


def _make_rpc(channel):
    def _rpc(tool: str, args: dict):
        send_msg(channel, {"type": "rpc", "tool": tool, "args": args})
        reply = recv_msg(channel)
        if reply is None:
            raise _ToolError("sandbox lost connection to host")
        if not reply.get("ok"):
            err = reply.get("error", {})
            raise _ToolError(f"{err.get('code', 'error')}: {err.get('message', '')}")
        return reply.get("result")

    return _rpc


def _run(ns: dict, code: str) -> tuple[str, str | None]:
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<run_code>", "exec"), ns)
        return buf.getvalue(), None
    except Exception:  # surfaced to the model as navigation, never crashes the kernel
        return buf.getvalue(), traceback.format_exc()


def _serve(channel) -> None:
    ns: dict = {}
    while True:
        msg = recv_msg(channel)
        if msg is None or msg.get("type") == "shutdown":
            break
        kind = msg["type"]
        if kind == "init":
            exec(compile(msg["client_source"], "<client>", "exec"), ns)
            ns["_rpc"] = _make_rpc(channel)
            send_msg(channel, {"type": "init_ok"})
        elif kind == "exec":
            stdout, error = _run(ns, msg["code"])
            send_msg(channel, {"type": "exec_result", "stdout": stdout, "error": error})


def main(sock_path: str) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    try:
        _serve(sock)
    finally:
        with contextlib.suppress(OSError):
            sock.close()


def main_stdio() -> None:
    proto_fd = os.dup(1)  # the real stdout pipe back to the host
    os.dup2(2, 1)  # stray fd-1 writes now go to stderr, not the protocol
    reader = os.fdopen(0, "rb", buffering=0)
    writer = os.fdopen(proto_fd, "wb", buffering=0)
    _serve(PipeChannel(reader, writer))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--stdio":
        main_stdio()
    else:
        main(sys.argv[1])
