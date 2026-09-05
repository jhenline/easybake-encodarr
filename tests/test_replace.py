from __future__ import annotations

from pathlib import Path

import pytest

from hevc_encoder.replace import ReplaceError, replace_in_place


def test_replace_mkv_in_place(tmp_path: Path) -> None:
    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    encoded = tmp_path / "movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED-DATA")
    dest = tmp_path / "movie.mkv"

    result = replace_in_place(original, encoded, dest)
    assert result == dest
    assert dest.read_bytes() == b"ENCODED-DATA"
    assert not (tmp_path / "movie.mkv.bak").exists()
    assert not encoded.exists()


def test_replace_copies_original_permissions(tmp_path: Path) -> None:
    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    original.chmod(0o644)
    encoded = tmp_path / "movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED-DATA")
    encoded.chmod(0o600)

    from hevc_encoder.replace import commit_output

    dest = commit_output(original, encoded, original, replace_original=True)
    mode = dest.stat().st_mode & 0o777
    assert mode == 0o644


def test_sidecar_copies_original_permissions(tmp_path: Path) -> None:
    from hevc_encoder.replace import commit_output

    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    original.chmod(0o664)
    encoded = tmp_path / ".movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED-DATA")
    encoded.chmod(0o600)
    dest = tmp_path / "movie.hevc.mkv"

    commit_output(original, encoded, dest, replace_original=False)
    assert dest.stat().st_mode & 0o777 == 0o664
    assert original.stat().st_mode & 0o777 == 0o664


def test_replace_mp4_with_mkv(tmp_path: Path) -> None:
    original = tmp_path / "movie.mp4"
    original.write_bytes(b"ORIGINAL")
    encoded = tmp_path / "movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED-DATA")
    dest = tmp_path / "movie.mkv"

    replace_in_place(original, encoded, dest)
    assert dest.read_bytes() == b"ENCODED-DATA"
    assert not original.exists()
    assert not Path(str(original) + ".bak").exists()


def test_replace_refuses_existing_dest(tmp_path: Path) -> None:
    original = tmp_path / "movie.mp4"
    original.write_bytes(b"ORIGINAL")
    dest = tmp_path / "movie.mkv"
    dest.write_bytes(b"EXISTING")
    encoded = tmp_path / "movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED")

    with pytest.raises(ReplaceError, match="already exists"):
        replace_in_place(original, encoded, dest)
    assert original.read_bytes() == b"ORIGINAL"
    assert dest.read_bytes() == b"EXISTING"


def test_replace_restores_original_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    encoded = tmp_path / "movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED")

    import hevc_encoder.replace as replace_mod

    def boom(*_args, **_kwargs):
        raise OSError("simulated failure")

    monkeypatch.setattr(replace_mod.os, "replace", boom)
    with pytest.raises(ReplaceError):
        replace_in_place(original, encoded, original)
    assert original.exists()
    assert original.read_bytes() == b"ORIGINAL"


def test_replace_restores_original_on_interrupt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    encoded = tmp_path / ".movie.encoding.mkv"
    encoded.write_bytes(b"PARTIAL")

    import hevc_encoder.replace as replace_mod

    def boom(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(replace_mod.os, "replace", boom)
    with pytest.raises(KeyboardInterrupt):
        replace_in_place(original, encoded, original)
    assert original.exists()
    assert original.read_bytes() == b"ORIGINAL"


def test_sidecar_keeps_original(tmp_path: Path) -> None:
    from hevc_encoder.replace import commit_output

    original = tmp_path / "movie.mkv"
    original.write_bytes(b"ORIGINAL")
    encoded = tmp_path / ".movie.encoding.mkv"
    encoded.write_bytes(b"ENCODED-DATA")
    dest = tmp_path / "movie.hevc.mkv"

    result = commit_output(original, encoded, dest, replace_original=False)
    assert result == dest
    assert original.read_bytes() == b"ORIGINAL"
    assert dest.read_bytes() == b"ENCODED-DATA"
    assert not encoded.exists()
