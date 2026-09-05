from __future__ import annotations

import pytest

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.evaluate import RulesEvaluator, get_evaluator
from tests.conftest import video_from


def test_h264_is_encoded(evaluator: RulesEvaluator) -> None:
    video = video_from("h264_1080p.json")
    decision = evaluator.evaluate(video)
    assert decision.action == "encode"
    assert "h264" in decision.reason


def test_efficient_hevc_is_skipped(evaluator: RulesEvaluator) -> None:
    video = video_from("hevc_efficient_1080p.json")
    decision = evaluator.evaluate(video)
    assert decision.action == "skip"
    assert "already hevc" in decision.reason
    assert "under bitrate cap" in decision.reason


def test_bloated_hevc_skipped_without_reconvert(evaluator: RulesEvaluator) -> None:
    video = video_from("hevc_bloated_4k.json")
    decision = evaluator.evaluate(video)
    assert decision.action == "skip"
    assert decision.reason == "already hevc"


def test_bloated_hevc_encoded_when_reconvert() -> None:
    ev = RulesEvaluator(EvaluateConfig(reconvert_hevc=True))
    video = video_from("hevc_bloated_4k.json")
    decision = ev.evaluate(video)
    assert decision.action == "encode"
    assert "over bitrate cap" in decision.reason


def test_hdr_is_skipped(evaluator: RulesEvaluator) -> None:
    video = video_from("hdr_hevc.json")
    assert video.hdr
    decision = evaluator.evaluate(video)
    assert decision.action == "skip"
    assert decision.reason.startswith("hdr skipped")


def test_hdr_can_be_allowed() -> None:
    ev = RulesEvaluator(EvaluateConfig(skip_hdr=False, skip_if_hevc=True))
    video = video_from("hdr_hevc.json")
    decision = ev.evaluate(video)
    assert decision.action == "skip"
    assert "already hevc" in decision.reason


def test_mpeg2_cover_art_ignored(evaluator: RulesEvaluator) -> None:
    video = video_from("mpeg2_with_cover.json")
    assert video.codec == "mpeg2video"
    assert video.video_stream_index == 1
    decision = evaluator.evaluate(video)
    assert decision.action == "encode"


def test_av1_is_skipped(evaluator: RulesEvaluator) -> None:
    video = video_from("av1_1080p.json")
    decision = evaluator.evaluate(video)
    assert decision.action == "skip"
    assert "av1" in decision.reason


def test_low_bpp_skipped() -> None:
    ev = RulesEvaluator(EvaluateConfig(bpp_skip_below=0.5))
    video = video_from("h264_1080p.json")
    # 8000 kbps / (1920*1080*23.976) ≈ 0.16, so 0.5 threshold skips it
    assert video.bpp < 0.5
    decision = ev.evaluate(video)
    assert decision.action == "skip"
    assert "bpp" in decision.reason


def test_no_video_stream() -> None:
    from pathlib import Path

    from hevc_encoder.models import VideoInfo

    video = VideoInfo(
        path=Path("empty.mkv"),
        size=10,
        mtime=1,
        duration=0,
        width=0,
        height=0,
        fps=0,
        codec="",
        pix_fmt="",
        bit_depth=8,
        bitrate_kbps=0,
        bpp=0,
        hdr=False,
        hdr_reason=None,
        video_stream_index=-1,
        has_video=False,
    )
    decision = RulesEvaluator(EvaluateConfig()).evaluate(video)
    assert decision.action == "skip"
    assert decision.reason == "no video stream"


def test_get_evaluator_rules() -> None:
    ev = get_evaluator(EvaluateConfig(mode="rules"))
    assert ev.name == "rules"


def test_get_evaluator_vmaf_reserved() -> None:
    with pytest.raises(NotImplementedError, match="VMAF"):
        get_evaluator(EvaluateConfig(mode="vmaf"))
