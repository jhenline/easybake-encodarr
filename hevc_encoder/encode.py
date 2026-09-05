from __future__ import annotations

import logging
import os
import re
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from hevc_encoder.config import AppConfig
from hevc_encoder.models import EncoderName, VideoInfo
from hevc_encoder.sleep import prevent_sleep

log = logging.getLogger("hevc_encoder")

ProgressCallback = Callable[[dict[str, str | float | None]], None]

TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
ERROR_RE = re.compile(
    r"error|unable to parse|invalid argument|failed|conversion failed",
    re.IGNORECASE,
)


def libx265_crf(video_quality: int) -> int:
    """Map VideoToolbox -q:v (higher=better) to libx265 CRF (lower=better).

    Quality 65 → CRF ~22 as specified in the v1 plan.
    """
    return max(0, min(51, round(51 - video_quality * 0.45)))


def encoding_temp_path(source: Path, temp_dir: Path | None) -> Path:
    """Hidden temp file so Plex/Finder don't treat a partial encode as a movie."""
    name = f".{source.stem}.encoding.mkv"
    return (temp_dir or source.parent) / name


def output_dest_path(source: Path, replace_original: bool = True) -> Path:
    if replace_original:
        return source.with_suffix(".mkv")
    return source.with_name(f"{source.stem}.hevc.mkv")


def _ffmpeg_encoder_list(ffmpeg: str) -> str:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout or ""


def videotoolbox_available(ffmpeg: str = "ffmpeg") -> bool:
    return "hevc_videotoolbox" in _ffmpeg_encoder_list(ffmpeg)


def qsv_available(ffmpeg: str = "ffmpeg") -> bool:
    return "hevc_qsv" in _ffmpeg_encoder_list(ffmpeg)


def vaapi_available(ffmpeg: str = "ffmpeg") -> bool:
    return "hevc_vaapi" in _ffmpeg_encoder_list(ffmpeg)


def resolve_encoder(cfg: AppConfig, ffmpeg: str | None = None) -> EncoderName:
    """Pick an encoder.

    ``auto`` is libx265 on macOS. On Linux prefer ``hevc_vaapi`` (same Intel iGPU
    as Quick Sync via VA-API). Ubuntu 4.4 ``hevc_qsv`` often cannot open MFX.
    """
    binary = ffmpeg or cfg.ffmpeg
    choice = cfg.encode.encoder
    if choice == "videotoolbox":
        return "hevc_videotoolbox"
    if choice == "qsv":
        return "hevc_qsv"
    if choice == "vaapi":
        return "hevc_vaapi"
    if choice == "libx265":
        return "libx265"
    if sys.platform.startswith("linux"):
        if vaapi_available(binary):
            return "hevc_vaapi"
        if qsv_available(binary):
            return "hevc_qsv"
    return "libx265"


def build_ffmpeg_args(
    video: VideoInfo,
    dest: Path,
    encoder: EncoderName,
    cfg: AppConfig,
) -> list[str]:
    """Encode the main video; copy audio/subs. Skip attached covers.

    Mapping every stream (``-map 0``) plus a global ``-profile:v`` breaks files
    with a JPEG cover, because FFmpeg applies the HEVC profile to that picture.
    """
    quality = cfg.encode.video_quality
    vid = f"0:{video.video_stream_index}"
    args: list[str] = [
        cfg.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
    ]
    if encoder == "hevc_vaapi":
        device = str(cfg.encode.vaapi_device)
        args += [
            "-init_hw_device",
            f"vaapi=va:{device}",
            "-filter_hw_device",
            "va",
        ]
    args += [
        "-i",
        str(video.path),
        "-map",
        vid,
        "-map",
        "0:a?",
        "-map",
        "0:s?",
        "-c",
        "copy",
    ]
    if encoder == "hevc_vaapi":
        pix = "p010le" if video.bit_depth >= 10 else "nv12"
        args += ["-vf", f"format={pix},hwupload"]
    args += ["-c:v"]

    if encoder == "hevc_videotoolbox":
        args += ["hevc_videotoolbox", "-q:v", str(quality)]
        if video.bit_depth >= 10:
            args += ["-profile:v", "main10", "-pix_fmt", "p010le"]
        else:
            args += ["-profile:v", "main", "-pix_fmt", "yuv420p"]
    elif encoder == "hevc_qsv":
        # Software decode + QSV encode. Often fails on Ubuntu 4.4 libmfx; VA-API is preferred.
        gq = libx265_crf(quality)
        args += [
            "hevc_qsv",
            "-preset",
            "medium",
            "-global_quality",
            str(gq),
            "-g",
            "48",
            "-forced_idr",
            "1",
        ]
        if video.bit_depth >= 10:
            args += ["-profile:v", "main10", "-pix_fmt", "p010le"]
        else:
            args += ["-profile:v", "main", "-pix_fmt", "nv12"]
    elif encoder == "hevc_vaapi":
        qp = libx265_crf(quality)
        args += [
            "hevc_vaapi",
            "-qp",
            str(qp),
            "-g",
            "48",
        ]
        if video.bit_depth >= 10:
            args += ["-profile:v", "main10"]
        else:
            args += ["-profile:v", "main"]
    else:
        crf = libx265_crf(quality)
        args += [
            "libx265",
            "-preset",
            "medium",
            "-crf",
            str(crf),
            "-x265-params",
            "repeat-headers=1:aud=1:open-gop=0",
        ]
        if video.bit_depth >= 10:
            args += ["-pix_fmt", "yuv420p10le"]
        else:
            args += ["-pix_fmt", "yuv420p"]

    args += ["-max_muxing_queue_size", "4096", str(dest)]
    return args


def remux_temp_path(encoding_temp: Path) -> Path:
    """Sibling of the encode temp; hidden so Finder/Plex ignore it."""
    return encoding_temp.with_name(encoding_temp.name.replace(".encoding.mkv", ".encoding.remux.mkv"))


def build_remux_args(src: Path, dest: Path, ffmpeg: str) -> list[str]:
    """Rewrite cues/clusters so timeline seeks keep audio.

    FFmpeg's matroska muxer defaults to a 10s interleave window. With DTS-HD
    plus many sparse PGS subtitle tracks, that produces a file that plays
    from the start but drops audio after a seek in VLC/IINA. Forcing
    ``max_interleave_delta=0`` rebuilds clusters so every stream is indexed.
    """
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-progress",
        "pipe:1",
        "-nostats",
        "-i",
        str(src),
        "-map",
        "0",
        "-c",
        "copy",
        "-max_interleave_delta",
        "0",
        str(dest),
    ]


def remux_for_seek(
    src: Path,
    dest: Path,
    ffmpeg: str,
    duration: float = 0.0,
    on_progress: ProgressCallback | None = None,
) -> None:
    dest.unlink(missing_ok=True)
    try:
        run_ffmpeg(build_remux_args(src, dest, ffmpeg), duration, on_progress)
    except EncodeError as exc:
        dest.unlink(missing_ok=True)
        raise EncodeError(f"seek remux failed: {exc}") from exc
    if not dest.exists() or dest.stat().st_size == 0:
        dest.unlink(missing_ok=True)
        raise EncodeError(f"seek remux produced no output: {dest}")


def _parse_progress_line(
    fields: dict[str, str], duration: float
) -> dict[str, str | float | None] | None:
    raw_time = fields.get("out_time") or fields.get("out_time_ms")
    elapsed = None
    display = None
    if fields.get("out_time") and fields["out_time"] not in {"N/A", "00:00:00"}:
        display = fields["out_time"].split(".")[0]
        match = TIME_RE.search("time=" + fields["out_time"])
        if match:
            hours, minutes, seconds = match.groups()
            elapsed = int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    elif fields.get("out_time_ms") not in {None, "N/A"}:
        try:
            elapsed = int(fields["out_time_ms"]) / 1_000_000.0
            display = _format_hms(elapsed)
        except ValueError:
            elapsed = None

    if elapsed is None:
        return None
    percent = None
    if duration > 0:
        percent = max(0.0, min(100.0, elapsed / duration * 100.0))
    speed = fields.get("speed")
    if speed in {None, "N/A"}:
        speed = None
    return {
        "time": display,
        "percent": percent,
        "speed": speed,
        "fps": fields.get("fps") if fields.get("fps") not in {None, "N/A"} else None,
    }


def _format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _ffmpeg_error(chunks: list[str], code: int) -> str:
    lines = [ln.strip() for ln in "".join(chunks).splitlines() if ln.strip()]
    hits = [ln for ln in lines if ERROR_RE.search(ln)]
    useful = hits[-8:] if hits else lines[-8:]
    return f"ffmpeg exited {code}: " + " | ".join(useful)


def _stop_ffmpeg(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            proc.kill()
        proc.wait(timeout=3)


def run_ffmpeg(
    args: list[str],
    duration: float,
    on_progress: ProgressCallback | None = None,
) -> None:
    proc = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        start_new_session=True,
    )
    assert proc.stdout is not None
    assert proc.stderr is not None
    stderr_chunks: list[str] = []
    finished = False

    def _read_stderr() -> None:
        for line in proc.stderr:
            stderr_chunks.append(line)

    reader = threading.Thread(target=_read_stderr, name="ffmpeg-stderr", daemon=True)
    reader.start()
    fields: dict[str, str] = {}
    try:
        for line in proc.stdout:
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, value = line.split("=", 1)
            fields[key] = value
            if key == "progress":
                if value == "end":
                    finished = True
                if on_progress:
                    parsed = _parse_progress_line(fields, duration)
                    if parsed:
                        on_progress(parsed)
    except KeyboardInterrupt:
        _stop_ffmpeg(proc)
        raise
    finally:
        if proc.poll() is None:
            _stop_ffmpeg(proc)
        proc.wait()
        reader.join(timeout=5)
    if proc.returncode != 0:
        raise EncodeError(_ffmpeg_error(stderr_chunks, proc.returncode))
    if not finished:
        raise EncodeError(
            "ffmpeg exited without a completion signal; output is incomplete"
        )


def encode_video(
    video: VideoInfo,
    encoder: EncoderName,
    cfg: AppConfig,
    on_progress: ProgressCallback | None = None,
) -> Path:
    dest = encoding_temp_path(video.path, cfg.encode.temp_dir)
    remux = remux_temp_path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    remux.unlink(missing_ok=True)
    args = build_ffmpeg_args(video, dest, encoder, cfg)
    try:
        with prevent_sleep():
            run_ffmpeg(args, video.duration, on_progress)
            if not dest.exists() or dest.stat().st_size == 0:
                raise EncodeError(f"encoder produced no output: {dest}")
            log.info("rebuilding playback index for %s", video.path.name)
            remux_for_seek(
                dest,
                remux,
                cfg.ffmpeg,
                duration=video.duration,
                on_progress=on_progress,
            )
            dest.unlink()
            remux.replace(dest)
        return dest
    except BaseException:
        dest.unlink(missing_ok=True)
        remux.unlink(missing_ok=True)
        raise


class EncodeError(RuntimeError):
    pass
