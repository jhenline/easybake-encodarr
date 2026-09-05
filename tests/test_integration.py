from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from hevc_encoder.config import AppConfig, EncodeConfig, EvaluateConfig, LibraryConfig
from hevc_encoder.pipeline import process_file, scan_libraries
from hevc_encoder.probe import probe_file
from hevc_encoder.store import Store

ffmpeg = shutil.which("ffmpeg")
pytestmark = pytest.mark.skipif(ffmpeg is None, reason="ffmpeg not installed")


def _make_h264(path: Path, bitrate: str = "8M") -> None:
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=2:size=1280x720:rate=30",
            "-c:v",
            "libx264",
            "-b:v",
            bitrate,
            "-pix_fmt",
            "yuv420p",
            "-y",
            str(path),
        ],
        check=True,
    )


def test_scan_dry_run_and_encode_replace(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    _make_h264(src)
    original_size = src.stat().st_size
    cfg = AppConfig(
        libraries=[LibraryConfig(path=tmp_path, recursive=False)],
        evaluate=EvaluateConfig(max_size_percent=99, bpp_skip_below=0.01),
        encode=EncodeConfig(encoder="auto", video_quality=65),
        state_db=tmp_path / "encoder.db",
    )
    store = Store(cfg.state_db)
    try:
        counts = scan_libraries(cfg, store, dry_run=True)
        assert counts.get("dry-run") == 1
        assert src.exists()

        status = process_file(src, cfg, store)
        assert status == "encoded"
        dest = tmp_path / "clip.mkv"
        assert dest.exists()
        assert not src.exists()
        assert dest.stat().st_size < original_size
        probed = probe_file(dest)
        assert probed.codec in {"hevc", "h265"}
        assert probed.has_video

        record = store.get_processed(dest)
        assert record is not None
        assert record["status"] == "encoded"
        assert record["original_codec"] == "h264"
        assert record["output_codec"] in {"hevc", "h265"}
        assert record["original_size"] == original_size
        assert record["output_size"] == dest.stat().st_size
        assert record["bytes_saved"] == original_size - dest.stat().st_size
        assert record["original_bitrate_kbps"]
        stats = store.stats()
        assert stats["encoded"] == 1
        assert stats["bytes_saved"] == record["bytes_saved"]

        again = process_file(dest, cfg, store)
        assert again == "already"
    finally:
        store.close()


def test_sidecar_keeps_original_on_disk(tmp_path: Path) -> None:
    src = tmp_path / "clip.mp4"
    _make_h264(src)
    original_bytes = src.read_bytes()
    cfg = AppConfig(
        libraries=[LibraryConfig(path=tmp_path, recursive=False)],
        evaluate=EvaluateConfig(max_size_percent=99, bpp_skip_below=0.01),
        encode=EncodeConfig(encoder="auto", video_quality=65, replace_original=False),
        state_db=tmp_path / "encoder.db",
    )
    store = Store(cfg.state_db)
    try:
        status = process_file(src, cfg, store)
        assert status == "encoded"
        sidecar = tmp_path / "clip.hevc.mkv"
        assert src.exists()
        assert src.read_bytes() == original_bytes
        assert sidecar.exists()
        assert not list(tmp_path.glob(".*.encoding.mkv"))
        probed = probe_file(sidecar)
        assert probed.codec in {"hevc", "h265"}
        record = store.get_processed(src)
        assert record is not None
        assert record["output_size"] == sidecar.stat().st_size
    finally:
        store.close()


def test_example_yaml_roundtrip() -> None:
    raw = Path("config.example.yml").read_text()
    yaml.safe_load(raw)
