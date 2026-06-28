"""Length-prefixed JSON framing over a stream socket.

Stdlib only: this module is bind-mounted into the sandbox container, which does
not have the sentinel package. Each message is a 4-byte big-endian length
followed by that many UTF-8 JSON bytes.
"""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


def send_msg(sock: socket.socket, obj: dict[str, Any]) -> None:
    payload = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(payload)) + payload)


def _recv_exactly(sock: socket.socket, n: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


def recv_msg(sock: socket.socket) -> dict[str, Any] | None:
    header = _recv_exactly(sock, 4)
    if header is None:
        return None
    (length,) = struct.unpack(">I", header)
    body = _recv_exactly(sock, length)
    if body is None:
        return None
    return json.loads(body.decode("utf-8"))
