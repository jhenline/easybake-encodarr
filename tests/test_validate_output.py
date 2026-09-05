from __future__ import annotations

from pathlib import Path

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.models import VideoInfo
from hevc_encoder.validate import validate_output


def _video(tmp_path: Path, size: int = 1_000_000, duration: float = 100.0) -> VideoInfo:
    src = tmp_path / "src.mkv"
    src.write_bytes(b"x" * 10)
    return VideoInfo(
        path=src,
        size=size,
        mtime=1.0,
        duration=duration,
        width=1920,
        height=1080,
        fps=24,
        codec="h264",
        pix_fmt="yuv420p",
        bit_depth=8,
        bitrate_kbps=8000,
        bpp=0.16,
        hdr=False,
        hdr_reason=None,
        video_stream_index=0,
        has_video=True,
    )


def test_validate_missing_file(tmp_path: Path) -> None:
    result = validate_output(
        _video(tmp_path), tmp_path / "missing.mkv", EvaluateConfig()
    )
    assert not result.ok
    assert "missing" in result.reason


def test_validate_empty_file(tmp_path: Path) -> None:
    encoded = tmp_path / "out.mkv"
    encoded.write_bytes(b"")
    result = validate_output(_video(tmp_path), encoded, EvaluateConfig())
    assert not result.ok
    assert "empty" in result.reason


def test_validate_size_gate(tmp_path: Path, monkeypatch) -> None:
    encoded = tmp_path / "out.mkv"
    encoded.write_bytes(b"y" * 950)
    source = _video(tmp_path, size=1000)

    fake = VideoInfo(
        path=encoded,
        size=950,
        mtime=1.0,
        duration=100.0,
        width=1920,
        height=1080,
        fps=24,
        codec="hevc",
        pix_fmt="yuv420p",
        bit_depth=8,
        bitrate_kbps=1000,
        bpp=0.02,
        hdr=False,
        hdr_reason=None,
        video_stream_index=0,
        has_video=True,
    )

    monkeypatch.setattr("hevc_encoder.validate.probe_file", lambda *a, **k: fake)
    # 950 > 90% of 1000
    result = validate_output(source, encoded, EvaluateConfig(max_size_percent=90))
    assert not result.ok
    assert "too large" in result.reason

    result_ok = validate_output(source, encoded, EvaluateConfig(max_size_percent=100))
    assert result_ok.ok
