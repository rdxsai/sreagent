"""Sandbox kernel entrypoint. Stdlib only; runs inside the container.

Protocol over one unix socket:
  host -> {"type":"init","client_source": "..."}     ; we exec it once
  host <- {"type":"init_ok"}
  host -> {"type":"exec","code":"..."}                ; run in the persistent ns
    (during exec) host <- {"type":"rpc","tool":..,"args":..}
                  host -> {"type":"rpc_result","ok":..,"result"/"error":..}
  host <- {"type":"exec_result","stdout":"...","error": null | "traceback"}
  host -> {"type":"shutdown"}                          ; exit
"""

from __future__ import annotations

import contextlib
import io
import socket
import sys
import traceback

from protocol import recv_msg, send_msg  # bind-mounted alongside this file


class _ToolError(Exception):
    pass


def _make_rpc(sock: socket.socket):
    def _rpc(tool: str, args: dict):
        send_msg(sock, {"type": "rpc", "tool": tool, "args": args})
        reply = recv_msg(sock)
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


def main(sock_path: str) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    ns: dict = {}
    while True:
        msg = recv_msg(sock)
        if msg is None or msg.get("type") == "shutdown":
            break
        kind = msg["type"]
        if kind == "init":
            exec(compile(msg["client_source"], "<client>", "exec"), ns)
            ns["_rpc"] = _make_rpc(sock)
            send_msg(sock, {"type": "init_ok"})
        elif kind == "exec":
            stdout, error = _run(ns, msg["code"])
            send_msg(sock, {"type": "exec_result", "stdout": stdout, "error": error})
    with contextlib.suppress(OSError):
        sock.close()


if __name__ == "__main__":
    main(sys.argv[1])
