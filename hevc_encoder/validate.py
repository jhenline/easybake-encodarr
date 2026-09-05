from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.models import VideoInfo
from hevc_encoder.probe import ProbeError, probe_file


@dataclass
class ValidationResult:
    ok: bool
    reason: str
    out_size: int = 0
    duration_out: float = 0.0
    codec: str = ""
    bitrate_kbps: float = 0.0


def validate_output(
    source: VideoInfo,
    encoded: Path,
    cfg: EvaluateConfig,
    ffprobe: str = "ffprobe",
) -> ValidationResult:
    if not encoded.exists():
        return ValidationResult(ok=False, reason="encoded file missing")

    out_size = encoded.stat().st_size
    if out_size <= 0:
        return ValidationResult(ok=False, reason="encoded file is empty")

    try:
        probed = probe_file(encoded, ffprobe=ffprobe)
    except ProbeError as exc:
        return ValidationResult(ok=False, reason=f"encoded file not probeable: {exc}")

    meta = {
        "out_size": out_size,
        "duration_out": probed.duration,
        "codec": probed.codec,
        "bitrate_kbps": probed.bitrate_kbps,
    }

    if not probed.has_video:
        return ValidationResult(
            ok=False, reason="encoded file has no video stream", **meta
        )

    if not duration_ok(source.duration, probed.duration, cfg):
        return ValidationResult(
            ok=False,
            reason=(
                f"duration mismatch "
                f"(source {source.duration:.2f}s vs output {probed.duration:.2f}s)"
            ),
            **meta,
        )

    max_size = source.size * (cfg.max_size_percent / 100.0)
    if out_size > max_size:
        return ValidationResult(
            ok=False,
            reason=(
                f"output too large "
                f"({out_size} > {cfg.max_size_percent:g}% of {source.size})"
            ),
            **meta,
        )

    return ValidationResult(ok=True, reason="validation passed", **meta)


def duration_ok(source: float, output: float, cfg: EvaluateConfig) -> bool:
    if source <= 0 or output <= 0:
        return False
    delta = abs(source - output)
    if delta <= cfg.duration_tolerance_seconds:
        return True
    pct = (delta / source) * 100.0
    return pct <= cfg.duration_tolerance_percent
