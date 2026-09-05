from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hevc_encoder.config import AppConfig, EncodeConfig, LibraryConfig
from hevc_encoder.pipeline import scan_libraries, should_stop_in_test_mode
from hevc_encoder.store import Store


def test_should_stop_in_test_mode() -> None:
    assert not should_stop_in_test_mode("already")
    assert not should_stop_in_test_mode("skipped")
    assert not should_stop_in_test_mode("gone")
    assert should_stop_in_test_mode("encoded")
    assert should_stop_in_test_mode("failed")
    assert should_stop_in_test_mode("rejected")
    assert should_stop_in_test_mode("dry-run")


def _library(tmp_path: Path, names: tuple[str, ...] = ("a.mkv", "b.mkv", "c.mkv")) -> Path:
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    return tmp_path


def test_test_mode_skips_then_stops_after_one_encode(tmp_path: Path) -> None:
    root = _library(tmp_path)
    cfg = AppConfig(
        libraries=[LibraryConfig(path=root, recursive=False)],
        encode=EncodeConfig(test_mode=True),
        state_db=tmp_path / "encoder.db",
    )
    store = Store(cfg.state_db)
    calls: list[str] = []

    def fake_process(path: Path, *_args, **_kwargs) -> str:
        calls.append(path.name)
        if len(calls) == 1:
            return "skipped"
        if len(calls) == 2:
            return "encoded"
        raise AssertionError(f"processed extra file {path.name}")

    try:
        with patch("hevc_encoder.pipeline.process_file", side_effect=fake_process):
            counts = scan_libraries(cfg, store)
        assert counts == {"skipped": 1, "encoded": 1}
        assert len(calls) == 2
    finally:
        store.close()


def test_test_mode_off_processes_every_file(tmp_path: Path) -> None:
    root = _library(tmp_path)
    cfg = AppConfig(
        libraries=[LibraryConfig(path=root, recursive=False)],
        encode=EncodeConfig(test_mode=False),
        state_db=tmp_path / "encoder.db",
    )
    store = Store(cfg.state_db)
    calls: list[str] = []

    def fake_process(path: Path, *_args, **_kwargs) -> str:
        calls.append(path.name)
        return "encoded"

    try:
        with patch("hevc_encoder.pipeline.process_file", side_effect=fake_process):
            counts = scan_libraries(cfg, store)
        assert counts == {"encoded": 3}
        assert len(calls) == 3
    finally:
        store.close()
