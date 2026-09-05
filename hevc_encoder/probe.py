from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from hevc_encoder.models import VideoInfo

HDR_TRANSFERS = {"smpte2084", "arib-std-b67", "smpte428"}
COVER_CODECS = {"mjpeg", "png", "bmp", "gif"}


def parse_fps(rate: str | None) -> float:
    if not rate or rate in {"0/0", "N/A"}:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            d = float(den)
            return float(num) / d if d else 0.0
        return float(rate)
    except (TypeError, ValueError, ZeroDivisionError):
        return 0.0


def bit_depth(stream: dict[str, Any]) -> int:
    raw = stream.get("bits_per_raw_sample")
    if raw:
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    pix = stream.get("pix_fmt") or ""
    if "12" in pix:
        return 12
    if "10" in pix:
        return 10
    return 8


def pick_video_stream(streams: list[dict[str, Any]]) -> dict[str, Any] | None:
    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        if stream.get("codec_name") in COVER_CODECS:
            continue
        return stream
    return None


def detect_hdr(stream: dict[str, Any]) -> tuple[bool, str | None]:
    transfer = (stream.get("color_transfer") or "").lower()
    if transfer in HDR_TRANSFERS:
        return True, f"color_transfer={transfer}"

    tag = (stream.get("codec_tag_string") or "").lower()
    if "dovi" in tag or tag in {"dvhe", "dvh1"}:
        return True, f"codec_tag={tag}"

    for side in stream.get("side_data_list") or []:
        kind = (side.get("side_data_type") or "").lower()
        if any(
            token in kind
            for token in ("dolby", "hdr", "mastering display", "content light")
        ):
            return True, kind
    return False, None


def resolution_bucket(height: int) -> int:
    if height <= 720:
        return 720
    if height <= 1080:
        return 1080
    return 2160


def parse_probe(data: dict[str, Any], path: Path, size: int, mtime: float) -> VideoInfo:
    fmt = data.get("format") or {}
    stream = pick_video_stream(data.get("streams") or [])
    if stream is None:
        return VideoInfo(
            path=path,
            size=size,
            mtime=mtime,
            duration=_float(fmt.get("duration")),
            width=0,
            height=0,
            fps=0.0,
            codec="",
            pix_fmt="",
            bit_depth=8,
            bitrate_kbps=0.0,
            bpp=0.0,
            hdr=False,
            hdr_reason=None,
            video_stream_index=-1,
            has_video=False,
            probe=data,
        )

    duration = _float(stream.get("duration")) or _float(fmt.get("duration"))
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    fps = parse_fps(stream.get("avg_frame_rate") or stream.get("r_frame_rate"))
    codec = (stream.get("codec_name") or "").lower()
    pix_fmt = stream.get("pix_fmt") or ""
    depth = bit_depth(stream)

    stream_br = _float(stream.get("bit_rate"))
    format_br = _float(fmt.get("bit_rate"))
    if stream_br > 0:
        bitrate_kbps = stream_br / 1000.0
    elif format_br > 0:
        bitrate_kbps = format_br / 1000.0
    elif duration > 0:
        bitrate_kbps = (size * 8.0) / duration / 1000.0
    else:
        bitrate_kbps = 0.0

    pixels_per_sec = width * height * fps
    bpp = (bitrate_kbps * 1000.0) / pixels_per_sec if pixels_per_sec > 0 else 0.0
    hdr, hdr_reason = detect_hdr(stream)

    return VideoInfo(
        path=path,
        size=size,
        mtime=mtime,
        duration=duration,
        width=width,
        height=height,
        fps=fps,
        codec=codec,
        pix_fmt=pix_fmt,
        bit_depth=depth,
        bitrate_kbps=bitrate_kbps,
        bpp=bpp,
        hdr=hdr,
        hdr_reason=hdr_reason,
        video_stream_index=int(stream.get("index") or 0),
        has_video=True,
        probe=data,
    )


def probe_file(path: Path, ffprobe: str = "ffprobe") -> VideoInfo:
    stat = path.stat()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise ProbeError(result.stderr.strip() or f"ffprobe failed for {path}")
    data = json.loads(result.stdout or "{}")
    return parse_probe(data, path, stat.st_size, stat.st_mtime)


class ProbeError(RuntimeError):
    pass


def _float(value: Any) -> float:
    if value is None or value == "N/A":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
