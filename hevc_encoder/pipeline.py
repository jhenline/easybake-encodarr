from __future__ import annotations

import logging
import time
from pathlib import Path

from hevc_encoder.config import AppConfig
from hevc_encoder.encode import (
    EncodeError,
    encode_video,
    encoding_temp_path,
    output_dest_path,
    resolve_encoder,
)
from hevc_encoder.evaluate import get_evaluator
from hevc_encoder.models import EncoderName, VideoInfo
from hevc_encoder.probe import ProbeError, probe_file
from hevc_encoder.replace import ReplaceError, commit_output, same_volume
from hevc_encoder.runtime import runtime
from hevc_encoder.scan import iter_videos, wait_until_stable
from hevc_encoder.store import Store
from hevc_encoder.validate import validate_output

log = logging.getLogger("hevc_encoder")


def process_file(
    path: Path,
    cfg: AppConfig,
    store: Store,
    *,
    dry_run: bool = False,
    wait_stable: bool = False,
) -> str:
    path = path.resolve()
    if wait_stable:
        if not wait_until_stable(path, cfg.watch.stable_seconds):
            log.warning("file disappeared before it stabilized: %s", path)
            return "gone"

    if not path.exists():
        return "gone"

    stat = path.stat()
    if store.is_processed(path, stat.st_size, stat.st_mtime):
        log.debug("already processed: %s", path)
        return "already"

    job_id = store.insert_job(path, "probing")
    try:
        video = probe_file(path, ffprobe=cfg.ffprobe)
    except ProbeError as exc:
        reason = f"probe failed: {exc}"
        log.error("%s — %s", path, reason)
        store.update_job(job_id, status="failed", error=reason)
        store.mark_processed(
            path, stat.st_size, stat.st_mtime, "failed", reason, original_path=str(path)
        )
        return "failed"

    store.update_job(job_id, **_job_original(video))

    try:
        evaluator = get_evaluator(cfg.evaluate)
    except NotImplementedError as exc:
        store.update_job(job_id, status="failed", error=str(exc))
        return "failed"

    decision = evaluator.evaluate(video)
    log.info("%s — %s (%s)", path.name, decision.action, decision.reason)

    if decision.action == "skip":
        store.update_job(
            job_id,
            status="skipped",
            reason=decision.reason,
            output_codec=video.codec,
            output_bitrate_kbps=video.bitrate_kbps,
            out_size=video.size,
            bytes_saved=0,
        )
        store.mark_processed(
            path,
            video.size,
            video.mtime,
            "skipped",
            decision.reason,
            **_library_original(video),
            output_codec=video.codec,
            output_bitrate_kbps=video.bitrate_kbps,
            output_size=video.size,
            bytes_saved=0,
        )
        return "skipped"

    if dry_run:
        store.update_job(
            job_id,
            status="skipped",
            reason=f"dry-run: would encode ({decision.reason})",
            output_codec=video.codec,
            output_bitrate_kbps=video.bitrate_kbps,
            out_size=video.size,
            bytes_saved=0,
        )
        return "dry-run"

    if cfg.encode.temp_dir is not None:
        cfg.encode.temp_dir.mkdir(parents=True, exist_ok=True)
        if not same_volume(path.parent, cfg.encode.temp_dir):
            reason = (
                f"temp_dir {cfg.encode.temp_dir} is not on the same volume as {path}"
            )
            log.error(reason)
            store.update_job(job_id, status="failed", error=reason)
            store.mark_processed(
                path,
                video.size,
                video.mtime,
                "failed",
                reason,
                **_library_original(video),
                bytes_saved=0,
            )
            return "failed"

    encoder: EncoderName = decision.encoder or resolve_encoder(cfg)
    return _encode_and_replace(job_id, video, encoder, decision.reason, cfg, store)


def _encode_and_replace(
    job_id: int,
    video: VideoInfo,
    encoder: EncoderName,
    reason: str,
    cfg: AppConfig,
    store: Store,
) -> str:
    path = video.path
    runtime.start(str(path), encoder)
    store.update_job(job_id, status="encoding", encoder=encoder, reason=reason)
    log.info("encoding %s with %s (this can take a while)", path.name, encoder)

    last_log = {"t": 0.0, "pct": -10.0}

    def on_progress(data: dict) -> None:
        runtime.progress(data)
        pct = data.get("percent")
        if not isinstance(pct, (int, float)):
            return
        now = time.monotonic()
        jumped_back = last_log["pct"] > 50 and isinstance(pct, (int, float)) and pct < 10
        if now - last_log["t"] < 15 and pct - last_log["pct"] < 5 and not jumped_back:
            return
        last_log["t"] = now
        last_log["pct"] = float(pct)
        log.info(
            "  %s  %.1f%%  time=%s  speed=%s",
            path.name,
            pct,
            data.get("time") or "?",
            data.get("speed") or "?",
        )

    encoded: Path | None = None
    try:
        try:
            encoded = encode_video(video, encoder, cfg, on_progress=on_progress)
        except EncodeError as first:
            if encoder not in {"hevc_videotoolbox", "hevc_qsv", "hevc_vaapi"}:
                raise
            label = {
                "hevc_videotoolbox": "videotoolbox",
                "hevc_qsv": "qsv",
                "hevc_vaapi": "vaapi",
            }[encoder]
            log.warning("%s failed, falling back to libx265: %s", label, first)
            encoder = "libx265"
            runtime.start(str(path), encoder)
            log.info("encoding %s with libx265 (CPU — much slower)", path.name)
            store.update_job(
                job_id,
                encoder=encoder,
                reason=f"{reason} ({label} failed, libx265)",
            )
            encoded = encode_video(video, encoder, cfg, on_progress=on_progress)

        store.update_job(job_id, status="validating")
        result = validate_output(video, encoded, cfg.evaluate, ffprobe=cfg.ffprobe)
        if not result.ok:
            log.warning("validation rejected %s — %s", path, result.reason)
            _delete_incomplete(path, cfg, encoded)
            encoded = None
            store.update_job(
                job_id,
                status="rejected",
                reason=result.reason,
                out_size=result.out_size,
                duration_out=result.duration_out,
                encoder=encoder,
                output_codec=result.codec or None,
                output_bitrate_kbps=result.bitrate_kbps or None,
                bytes_saved=0,
            )
            store.mark_processed(
                path,
                video.size,
                video.mtime,
                "rejected",
                result.reason,
                encoder=encoder,
                **_library_original(video),
                output_codec=result.codec or None,
                output_bitrate_kbps=result.bitrate_kbps or None,
                output_size=result.out_size,
                bytes_saved=0,
            )
            return "rejected"

        dest = output_dest_path(path, cfg.encode.replace_original)
        commit_output(
            path,
            encoded,
            dest,
            replace_original=cfg.encode.replace_original,
        )
        encoded = None
        dest_stat = dest.stat()
        saved = video.size - dest_stat.st_size
        store.update_job(
            job_id,
            status="encoded",
            reason=reason,
            encoder=encoder,
            out_size=dest_stat.st_size,
            duration_out=result.duration_out,
            output_codec=result.codec or "hevc",
            output_bitrate_kbps=result.bitrate_kbps or None,
            bytes_saved=saved,
        )
        if cfg.encode.replace_original:
            if dest.resolve() != path.resolve():
                store.delete_processed(path)
            tracked = dest
            tracked_stat = dest_stat
        else:
            tracked = path
            tracked_stat = path.stat()
        store.mark_processed(
            tracked,
            tracked_stat.st_size,
            tracked_stat.st_mtime,
            "encoded",
            reason,
            encoder=encoder,
            **_library_original(video),
            output_codec=result.codec or "hevc",
            output_bitrate_kbps=result.bitrate_kbps or None,
            output_size=dest_stat.st_size,
            bytes_saved=saved,
        )
        if cfg.encode.replace_original:
            log.info(
                "replaced %s → %s (saved %s)",
                path.name,
                dest.name,
                _human_bytes(saved),
            )
        else:
            log.info(
                "wrote %s beside %s (original kept, saved %s)",
                dest.name,
                path.name,
                _human_bytes(saved),
            )
        return "encoded"
    except KeyboardInterrupt:
        log.warning(
            "interrupted — original left in place, removing incomplete encode"
        )
        _delete_incomplete(path, cfg, encoded)
        store.update_job(
            job_id, status="interrupted", error="interrupted by user", encoder=encoder
        )
        raise
    except (EncodeError, ReplaceError) as exc:
        log.error("%s — %s", path, exc)
        _delete_incomplete(path, cfg, encoded)
        store.update_job(job_id, status="failed", error=str(exc), encoder=encoder)
        store.mark_processed(
            path,
            video.size,
            video.mtime,
            "failed",
            str(exc),
            encoder=encoder,
            **_library_original(video),
            bytes_saved=0,
        )
        return "failed"
    except Exception as exc:
        log.exception("unexpected error processing %s", path)
        _delete_incomplete(path, cfg, encoded)
        store.update_job(job_id, status="failed", error=str(exc), encoder=encoder)
        store.mark_processed(
            path,
            video.size,
            video.mtime,
            "failed",
            str(exc),
            encoder=encoder,
            **_library_original(video),
            bytes_saved=0,
        )
        return "failed"
    finally:
        runtime.clear()


def should_stop_in_test_mode(status: str) -> bool:
    """Skip already-done / skip-decision files; stop after the first real attempt."""
    return status not in {"already", "skipped", "gone"}


def scan_libraries(
    cfg: AppConfig,
    store: Store,
    *,
    dry_run: bool = False,
    wait_stable: bool = False,
) -> dict[str, int]:
    counts: dict[str, int] = {}
    if cfg.encode.test_mode:
        log.info("test_mode is on — encoding at most one file")
    for lib in cfg.libraries:
        log.info("scanning %s", lib.path)
        for path in iter_videos(lib.path, lib.recursive, cfg.ignore_globs):
            status = process_file(
                path, cfg, store, dry_run=dry_run, wait_stable=wait_stable
            )
            counts[status] = counts.get(status, 0) + 1
            if cfg.encode.test_mode and should_stop_in_test_mode(status):
                log.info("test_mode: stopping after %s (%s)", path.name, status)
                return counts
    return counts


def _delete_incomplete(
    path: Path, cfg: AppConfig, encoded: Path | None
) -> None:
    if encoded is not None:
        encoded.unlink(missing_ok=True)
    encoding_temp_path(path, cfg.encode.temp_dir).unlink(missing_ok=True)


def _library_original(video: VideoInfo) -> dict:
    return {
        "original_path": str(video.path),
        "original_codec": video.codec,
        "original_bitrate_kbps": video.bitrate_kbps,
        "original_width": video.width,
        "original_height": video.height,
        "original_duration": video.duration,
        "original_size": video.size,
    }


def _job_original(video: VideoInfo) -> dict:
    return {
        "in_size": video.size,
        "duration_in": video.duration,
        "original_path": str(video.path),
        "original_codec": video.codec,
        "original_bitrate_kbps": video.bitrate_kbps,
        "original_width": video.width,
        "original_height": video.height,
    }


def _human_bytes(n: int) -> str:
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < step or unit == "TB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= step
    return f"{n} B"
