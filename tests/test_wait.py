from __future__ import annotations

from pathlib import Path

from hevc_encoder.scan import wait_until_stable


def test_wait_until_stable_zero(tmp_path: Path) -> None:
    path = tmp_path / "a.mkv"
    path.write_bytes(b"x")
    assert wait_until_stable(path, 0)


def test_wait_until_stable_missing(tmp_path: Path) -> None:
    assert not wait_until_stable(tmp_path / "gone.mkv", 0)
