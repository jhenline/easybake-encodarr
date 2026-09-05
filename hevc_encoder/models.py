from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

Action = Literal["encode", "skip"]
EncoderName = Literal["hevc_videotoolbox", "libx265", "hevc_qsv", "hevc_vaapi"]


@dataclass
class VideoInfo:
    path: Path
    size: int
    mtime: float
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    pix_fmt: str
    bit_depth: int
    bitrate_kbps: float
    bpp: float
    hdr: bool
    hdr_reason: str | None
    video_stream_index: int
    has_video: bool
    probe: dict[str, Any] = field(default_factory=dict)


@dataclass
class Decision:
    """Encoder-agnostic decision.

    ``extra`` is reserved for a future VMAF evaluator (crf, samples, vmaf_target).
    The scan/encode/replace pipeline only reads ``action``, ``reason``, and ``encoder``.
    """

    action: Action
    reason: str
    encoder: EncoderName | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class Evaluator(Protocol):
    """Swap-in evaluator. v1 is rules; later a VMAF implementation can replace it."""

    name: str

    def evaluate(self, video: VideoInfo) -> Decision: ...
