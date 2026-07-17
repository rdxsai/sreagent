import hashlib
from pathlib import Path

import pytest

from sentinel_rcaeval.download import verify_checksum


def test_verify_checksum_ok(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    verify_checksum(f, hashlib.sha256(b"hello").hexdigest())  # no raise


def test_verify_checksum_mismatch(tmp_path: Path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"hello")
    with pytest.raises(ValueError):
        verify_checksum(f, "0" * 64)
