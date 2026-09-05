from __future__ import annotations

from pathlib import Path

from hevc_encoder.config import AppConfig, EncodeConfig
from hevc_encoder.encode import (
    build_ffmpeg_args,
    build_remux_args,
    encoding_temp_path,
    libx265_crf,
    output_dest_path,
    remux_temp_path,
    resolve_encoder,
    vaapi_upload_filter,
)
from tests.conftest import video_from


def test_libx265_crf_maps_quality_65_to_about_22() -> None:
    assert libx265_crf(65) == 22


def _maps(args: list[str]) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == "-map"]


def test_videotoolbox_args() -> None:
    video = video_from("h264_1080p.json")
    cfg = AppConfig(encode=EncodeConfig(video_quality=65))
    dest = Path("/tmp/out.encoding.mkv")
    args = build_ffmpeg_args(video, dest, "hevc_videotoolbox", cfg)
    joined = " ".join(args)
    assert _maps(args) == ["0:0", "0:a?", "0:s?"]
    assert "-c:v" in args
    assert "hevc_videotoolbox" in args
    assert "-q:v" in args
    assert "65" in args
    assert "-c" in args and "copy" in args
    assert "-progress" in args
    assert str(dest) == args[-1]
    assert "libx265" not in joined


def test_libx265_10bit_args() -> None:
    video = video_from("hevc_bloated_4k.json")
    cfg = AppConfig(encode=EncodeConfig(video_quality=65))
    args = build_ffmpeg_args(video, Path("/tmp/a.mkv"), "libx265", cfg)
    assert "libx265" in args
    assert "-crf" in args
    assert "22" in args
    assert "yuv420p10le" in args
    assert args[args.index("-x265-params") + 1] == "repeat-headers=1:aud=1:open-gop=0"


def test_qsv_args() -> None:
    video = video_from("h264_1080p.json")
    cfg = AppConfig(encode=EncodeConfig(video_quality=65))
    args = build_ffmpeg_args(video, Path("/tmp/a.mkv"), "hevc_qsv", cfg)
    assert "hevc_qsv" in args
    assert args[args.index("-global_quality") + 1] == "22"
    assert "nv12" in args
    assert "libx265" not in args


def test_vaapi_upload_filter_pads_planar_then_nv12() -> None:
    assert vaapi_upload_filter(8) == (
        "format=yuv420p,pad=ceil(iw/32)*32:ceil(ih/32)*32,format=nv12,"
        "hwupload=extra_hw_frames=64"
    )
    assert "yuv420p10le" in vaapi_upload_filter(10)
    assert "p010le" in vaapi_upload_filter(10)
    video = video_from("h264_1080p.json")
    cfg = AppConfig(encode=EncodeConfig(video_quality=65))
    args = build_ffmpeg_args(video, Path("/tmp/a.mkv"), "hevc_vaapi", cfg)
    assert args[args.index("-init_hw_device") + 1] == "vaapi=va:/dev/dri/renderD128"
    assert args[args.index("-filter_hw_device") + 1] == "va"
    assert args[args.index("-vf") + 1] == vaapi_upload_filter(8)
    assert args[args.index("-vf") + 1] == (
        "format=yuv420p,pad=ceil(iw/32)*32:ceil(ih/32)*32,format=nv12,"
        "hwupload=extra_hw_frames=64"
    )
    assert "hevc_vaapi" in args
    assert args[args.index("-qp") + 1] == "22"
    assert args[args.index("-bf") + 1] == "0"
    assert args[args.index("-profile:v") + 1] == "main"
    assert "hevc_qsv" not in args
    assert "libx265" not in args


def test_vaapi_10bit_uses_p010le() -> None:
    video = video_from("hevc_bloated_4k.json")
    args = build_ffmpeg_args(video, Path("/tmp/a.mkv"), "hevc_vaapi", AppConfig())
    assert args[args.index("-vf") + 1] == vaapi_upload_filter(10)
    assert "yuv420p10le" in args[args.index("-vf") + 1]
    assert args[args.index("-profile:v") + 1] == "main10"


def test_resolve_encoder_auto_and_libx265_use_libx265() -> None:
    assert resolve_encoder(AppConfig()) == "libx265"
    assert resolve_encoder(AppConfig(encode=EncodeConfig(encoder="libx265"))) == "libx265"


def test_resolve_encoder_videotoolbox_is_explicit() -> None:
    cfg = AppConfig(encode=EncodeConfig(encoder="videotoolbox"))
    assert resolve_encoder(cfg) == "hevc_videotoolbox"


def test_resolve_encoder_qsv_is_explicit() -> None:
    cfg = AppConfig(encode=EncodeConfig(encoder="qsv"))
    assert resolve_encoder(cfg) == "hevc_qsv"


def test_resolve_encoder_vaapi_is_explicit() -> None:
    cfg = AppConfig(encode=EncodeConfig(encoder="vaapi"))
    assert resolve_encoder(cfg) == "hevc_vaapi"


def test_resolve_encoder_auto_prefers_vaapi_on_linux(monkeypatch) -> None:
    monkeypatch.setattr("hevc_encoder.encode.sys.platform", "linux")
    monkeypatch.setattr("hevc_encoder.encode.vaapi_available", lambda ffmpeg="ffmpeg": True)
    monkeypatch.setattr("hevc_encoder.encode.qsv_available", lambda ffmpeg="ffmpeg": True)
    assert resolve_encoder(AppConfig()) == "hevc_vaapi"


def test_resolve_encoder_auto_uses_qsv_on_linux_without_vaapi(monkeypatch) -> None:
    monkeypatch.setattr("hevc_encoder.encode.sys.platform", "linux")
    monkeypatch.setattr("hevc_encoder.encode.vaapi_available", lambda ffmpeg="ffmpeg": False)
    monkeypatch.setattr("hevc_encoder.encode.qsv_available", lambda ffmpeg="ffmpeg": True)
    assert resolve_encoder(AppConfig()) == "hevc_qsv"


def test_resolve_encoder_auto_stays_libx265_on_mac_even_if_hw(monkeypatch) -> None:
    monkeypatch.setattr("hevc_encoder.encode.sys.platform", "darwin")
    monkeypatch.setattr("hevc_encoder.encode.vaapi_available", lambda ffmpeg="ffmpeg": True)
    monkeypatch.setattr("hevc_encoder.encode.qsv_available", lambda ffmpeg="ffmpeg": True)
    assert resolve_encoder(AppConfig()) == "libx265"


def test_cover_art_is_not_mapped_as_video() -> None:
    video = video_from("mpeg2_with_cover.json")
    cfg = AppConfig()
    args = build_ffmpeg_args(video, Path("/tmp/a.mkv"), "hevc_videotoolbox", cfg)
    assert _maps(args) == ["0:1", "0:a?", "0:s?"]
    assert "0:0" not in _maps(args)


def test_output_paths() -> None:
    src = Path("/media/Movie.mkv")
    assert output_dest_path(src, True) == Path("/media/Movie.mkv")
    assert output_dest_path(src, False) == Path("/media/Movie.hevc.mkv")
    assert output_dest_path(Path("/media/Movie.mp4"), True) == Path("/media/Movie.mkv")


def test_remux_rebuilds_matroska_cues() -> None:
    src = Path("/media/.Movie.encoding.mkv")
    dest = remux_temp_path(src)
    assert dest == Path("/media/.Movie.encoding.remux.mkv")
    args = build_remux_args(src, dest, "ffmpeg")
    assert args[:4] == ["ffmpeg", "-hide_banner", "-nostdin", "-y"]
    assert "-map" in args and "0" in args
    assert "-c" in args and "copy" in args
    assert "-max_interleave_delta" in args
    assert args[args.index("-max_interleave_delta") + 1] == "0"
    assert "-progress" in args
    assert args[-1] == str(dest)


def test_encoding_temp_is_hidden() -> None:
    src = Path("/media/Movie.mkv")
    assert encoding_temp_path(src, None) == Path("/media/.Movie.encoding.mkv")
