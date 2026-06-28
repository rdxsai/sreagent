import socket

from sentinel.sandbox.protocol import recv_msg, send_msg


def test_round_trip():
    a, b = socket.socketpair()
    send_msg(a, {"type": "exec", "code": "print(1)"})
    assert recv_msg(b) == {"type": "exec", "code": "print(1)"}


def test_clean_eof_returns_none():
    a, b = socket.socketpair()
    a.close()
    assert recv_msg(b) is None
