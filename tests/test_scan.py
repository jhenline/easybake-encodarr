from __future__ import annotations

from pathlib import Path

from hevc_encoder.config import DEFAULT_IGNORE_GLOBS
from hevc_encoder.scan import is_ignored, is_video_path, iter_leftovers, iter_videos


def test_ignore_partial_and_encoding(tmp_path: Path) -> None:
    globs = DEFAULT_IGNORE_GLOBS
    assert is_ignored(tmp_path / "movie.mkv.partial", globs)
    assert is_ignored(tmp_path / "movie.encoding.mkv", globs)
    assert is_ignored(tmp_path / ".movie.encoding.mkv", globs)
    assert is_ignored(tmp_path / "movie.hevc.mkv", globs)
    assert is_ignored(tmp_path / "movie.mkv.bak", globs)
    assert is_ignored(tmp_path / "file.!qB", globs)
    assert is_ignored(tmp_path / "._Cop Out (2010).mkv", globs)
    assert not is_ignored(tmp_path / "movie.mkv", globs)


def test_ignore_sample_directory(tmp_path: Path) -> None:
    sample = tmp_path / "Sample" / "clip.mkv"
    assert is_ignored(sample, DEFAULT_IGNORE_GLOBS)


def test_iter_videos_skips_junk(tmp_path: Path) -> None:
    (tmp_path / "keep.mkv").write_bytes(b"x")
    (tmp_path / "skip.partial").write_bytes(b"x")
    (tmp_path / "film.encoding.mkv").write_bytes(b"x")
    (tmp_path / "._Cop Out (2010).mkv").write_bytes(b"x")
    (tmp_path / "notes.txt").write_bytes(b"x")
    names = {p.name for p in iter_videos(tmp_path, recursive=True, ignore_globs=DEFAULT_IGNORE_GLOBS)}
    assert names == {"keep.mkv"}


def test_is_video_path_requires_extension(tmp_path: Path) -> None:
    p = tmp_path / "movie.mkv"
    p.write_bytes(b"x")
    assert is_video_path(p, DEFAULT_IGNORE_GLOBS)
    assert not is_video_path(tmp_path / "movie.txt", DEFAULT_IGNORE_GLOBS)


def test_iter_leftovers_finds_temps_and_sidecars(tmp_path: Path) -> None:
    (tmp_path / "keep.mkv").write_bytes(b"x")
    (tmp_path / "movie.encoding.mkv").write_bytes(b"x")
    (tmp_path / ".hidden.encoding.mkv").write_bytes(b"x")
    (tmp_path / "movie.mkv.bak").write_bytes(b"x")
    (tmp_path / "movie.hevc.mkv").write_bytes(b"x")
    names = {p.name for p in iter_leftovers(tmp_path, recursive=True)}
    assert names == {"movie.encoding.mkv", ".hidden.encoding.mkv", "movie.mkv.bak"}
    with_sidecars = {p.name for p in iter_leftovers(tmp_path, recursive=True, include_sidecars=True)}
    assert "movie.hevc.mkv" in with_sidecars
    assert "keep.mkv" not in with_sidecars
