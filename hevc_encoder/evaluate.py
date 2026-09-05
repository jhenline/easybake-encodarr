from __future__ import annotations

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.models import Decision, Evaluator, VideoInfo
from hevc_encoder.probe import resolution_bucket


class RulesEvaluator:
    """v1 evaluator: codec, bitrate caps, bits-per-pixel, HDR skip.

    A future ``VmafEvaluator`` can implement the same ``Evaluator`` protocol
    and return the same ``Decision`` type (with VMAF details in ``extra``).
    """

    name = "rules"

    def __init__(self, cfg: EvaluateConfig) -> None:
        self.cfg = cfg

    def evaluate(self, video: VideoInfo) -> Decision:
        if not video.has_video:
            return Decision(action="skip", reason="no video stream")

        if self.cfg.skip_hdr and video.hdr:
            detail = video.hdr_reason or "hdr"
            return Decision(action="skip", reason=f"hdr skipped ({detail})")

        if video.codec in {"av1", "vp9"}:
            return Decision(action="skip", reason=f"already {video.codec}")

        is_hevc = video.codec in {"hevc", "h265"}
        cap = self._bitrate_cap(video.height)
        over_cap = cap is not None and video.bitrate_kbps > cap

        if is_hevc:
            if self.cfg.reconvert_hevc and over_cap:
                return Decision(
                    action="encode",
                    reason=(
                        f"hevc over bitrate cap "
                        f"({video.bitrate_kbps:.0f} kbps > {cap} kbps @ {video.height}p)"
                    ),
                )
            if self.cfg.skip_if_hevc:
                if cap is not None and not over_cap:
                    return Decision(
                        action="skip",
                        reason=(
                            f"already hevc under bitrate cap "
                            f"({video.bitrate_kbps:.0f} kbps ≤ {cap} kbps)"
                        ),
                    )
                return Decision(action="skip", reason="already hevc")

        if video.bpp > 0 and video.bpp < self.cfg.bpp_skip_below:
            return Decision(
                action="skip",
                reason=f"already efficient (bpp {video.bpp:.3f} < {self.cfg.bpp_skip_below})",
            )

        return Decision(
            action="encode",
            reason=f"{video.codec or 'unknown'} {video.width}x{video.height}",
        )

    def _bitrate_cap(self, height: int) -> int | None:
        caps = self.cfg.bitrate_caps_kbps
        if not caps:
            return None
        bucket = resolution_bucket(height)
        if bucket in caps:
            return caps[bucket]
        return caps[min(caps, key=lambda k: abs(k - bucket))]


def get_evaluator(cfg: EvaluateConfig) -> Evaluator:
    """Factory kept so a VMAF evaluator can be selected via ``evaluate.mode`` later."""
    if cfg.mode == "vmaf":
        raise NotImplementedError(
            "VMAF evaluator is not implemented yet; set evaluate.mode: rules"
        )
    return RulesEvaluator(cfg)
