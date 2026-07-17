"""Download and checksum an RCAEval archive.

The RE2 archive URL and sha256 are supplied by the caller (recorded in the run
notes after inspecting the Zenodo record; see the plan's Task 11 manual step) so
this module hardcodes no source and no license assumption.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx


def verify_checksum(path: Path, sha256: str) -> None:
    digest = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    if digest != sha256:
        raise ValueError(f"checksum mismatch for {path}: got {digest}, expected {sha256}")


def download_archive(url: str, dest: Path, sha256: str | None = None) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with httpx.stream("GET", url, follow_redirects=True, timeout=None) as resp:
        resp.raise_for_status()
        with dest.open("wb") as fh:
            for chunk in resp.iter_bytes():
                fh.write(chunk)
    if sha256:
        verify_checksum(dest, sha256)
    return dest
