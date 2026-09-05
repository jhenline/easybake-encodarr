from __future__ import annotations

import json
from pathlib import Path

from hevc_encoder.probe import bit_depth, parse_fps, parse_probe, resolution_bucket

from tests.conftest import FIXTURES, video_from


def test_parse_fps_fraction() -> None:
    assert abs(parse_fps("24000/1001") - 23.976) < 0.01
    assert parse_fps("24/1") == 24.0
    assert parse_fps("0/0") == 0.0
    assert parse_fps(None) == 0.0


def test_bit_depth_from_pix_fmt() -> None:
    assert bit_depth({"pix_fmt": "yuv420p10le"}) == 10
    assert bit_depth({"pix_fmt": "yuv420p", "bits_per_raw_sample": "8"}) == 8
    assert bit_depth({"bits_per_raw_sample": "10"}) == 10


def test_resolution_bucket() -> None:
    assert resolution_bucket(480) == 720
    assert resolution_bucket(720) == 720
    assert resolution_bucket(1080) == 1080
    assert resolution_bucket(2160) == 2160


def test_h264_probe_fields() -> None:
    video = video_from("h264_1080p.json")
    assert video.codec == "h264"
    assert video.width == 1920
    assert video.height == 1080
    assert video.has_video
    assert not video.hdr
    assert video.bitrate_kbps == 8000
    assert video.bpp > 0.1


def test_hdr_detection() -> None:
    video = video_from("hdr_hevc.json")
    assert video.hdr
    assert video.hdr_reason is not None
    assert video.bit_depth == 10


def test_cover_art_skipped_for_video_stream() -> None:
    video = video_from("mpeg2_with_cover.json")
    assert video.codec == "mpeg2video"
    assert video.video_stream_index == 1
    assert video.height == 480


def test_bitrate_from_file_size_when_missing() -> None:
    data = json.loads((FIXTURES / "h264_1080p.json").read_text())
    del data["streams"][0]["bit_rate"]
    del data["format"]["bit_rate"]
    video = parse_probe(data, Path("/tmp/a.mkv"), size=3_600_000_000, mtime=1.0)
    # 3600000000 * 8 / 3600 / 1000 = 8000 kbps
    assert abs(video.bitrate_kbps - 8000) < 1
