"""Slack request signature verification, the trust boundary for the internet-facing inbound
route. HMAC-SHA256 over `v0:{timestamp}:{raw_body}` with the signing secret, constant-time
compare, reject timestamps older than 5 minutes (replay guard). Uses the RAW body bytes, not
re-serialized JSON. Fails CLOSED: no secret -> reject.
"""

from __future__ import annotations

import hashlib
import hmac
import time

_MAX_SKEW_S = 60 * 5


def sign(secret: str, timestamp: str, raw_body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + raw_body
    return "v0=" + hmac.new(secret.encode(), base, hashlib.sha256).hexdigest()


def verify_slack(secret: str | None, timestamp: str | None, signature: str | None,
                 raw_body: bytes, *, now: float | None = None) -> bool:
    if not secret:              # fail closed: unset secret -> refuse
        return False
    if not timestamp or not signature:
        return False
    try:
        ts = int(timestamp)
    except ValueError:
        return False
    now = now if now is not None else time.time()
    if abs(now - ts) > _MAX_SKEW_S:   # replay guard
        return False
    expected = sign(secret, timestamp, raw_body)
    return hmac.compare_digest(expected, signature)   # constant-time
