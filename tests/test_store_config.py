from __future__ import annotations

from pathlib import Path

from hevc_encoder.config import dump_config, load_config, overlay_config, parse_config_payload
from hevc_encoder.store import Store


def test_load_example_config() -> None:
    cfg = load_config(Path("config.example.yml"))
    assert cfg.evaluate.mode == "rules"
    assert cfg.evaluate.skip_hdr is True
    assert cfg.evaluate.bitrate_caps_kbps[1080] == 3500
    assert cfg.encode.video_quality == 65
    assert cfg.encode.test_mode is False


def test_store_processed_roundtrip(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    path = tmp_path / "a.mkv"
    path.write_bytes(b"x")
    stat = path.stat()
    assert not store.is_processed(path, stat.st_size, stat.st_mtime)
    store.mark_processed(path, stat.st_size, stat.st_mtime, "skipped", "already hevc")
    assert store.is_processed(path, stat.st_size, stat.st_mtime)
    path.write_bytes(b"changed")
    stat = path.stat()
    assert not store.is_processed(path, stat.st_size, stat.st_mtime)
    store.close()


def test_store_jobs_and_stats(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    path = tmp_path / "a.mkv"
    path.write_bytes(b"x")
    store.mark_processed(
        path,
        400,
        1.0,
        "encoded",
        "h264",
        encoder="hevc_videotoolbox",
        original_path=str(path),
        original_codec="h264",
        original_bitrate_kbps=8000,
        original_width=1920,
        original_height=1080,
        original_size=1000,
        output_codec="hevc",
        output_bitrate_kbps=2500,
        output_size=400,
        bytes_saved=600,
    )
    stats = store.stats()
    assert stats["encoded"] == 1
    assert stats["bytes_saved"] == 600
    assert stats["original_bytes"] == 1000
    assert stats["output_bytes"] == 400
    assert stats["percent_saved"] == 60.0
    row = store.get_processed(path)
    assert row is not None
    assert row["original_codec"] == "h264"
    assert row["output_codec"] == "hevc"
    files = store.processed_files()
    assert files[0]["bytes_saved"] == 600
    store.close()


def test_clear_failed(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    path = tmp_path / "bad.mkv"
    store.mark_processed(path, 1, 1.0, "failed", "boom")
    assert store.failed_paths() == [path]
    assert store.clear_failed() == 1
    assert store.failed_paths() == []
    store.close()


def test_migrates_legacy_schema(tmp_path: Path) -> None:
    import sqlite3

    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """
        CREATE TABLE processed (
            path TEXT PRIMARY KEY,
            size INTEGER NOT NULL,
            mtime REAL NOT NULL,
            status TEXT NOT NULL,
            reason TEXT,
            encoder TEXT,
            in_size INTEGER,
            out_size INTEGER,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    conn.close()

    store = Store(db)
    path = tmp_path / "movie.mkv"
    store.mark_processed(
        path,
        500,
        1.0,
        "encoded",
        "h264 1080p",
        original_codec="h264",
        original_bitrate_kbps=8000.0,
        original_size=1000,
        output_codec="hevc",
        output_size=500,
        bytes_saved=500,
    )
    row = store.get_processed(path)
    assert row is not None
    assert row["original_codec"] == "h264"
    assert row["original_bitrate_kbps"] == 8000.0
    assert row["bytes_saved"] == 500
    store.close()


def test_clear_history(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    path = tmp_path / "a.mkv"
    store.mark_processed(path, 1, 1.0, "encoded", "h264", original_size=10, output_size=4, bytes_saved=6)
    store.insert_job(path, "encoded")
    processed, jobs = store.clear_history()
    assert processed == 1
    assert jobs == 1
    assert store.processed_files() == []
    assert store.recent_jobs() == []
    assert store.stats()["files_tracked"] == 0
    store.close()


def test_forget_processed_path(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    keep = tmp_path / "keep.mkv"
    gone = tmp_path / "gone.mkv"
    store.mark_processed(keep, 1, 1.0, "skipped", "already hevc")
    store.mark_processed(gone, 1, 1.0, "encoded", "h264")
    store.insert_job(gone, "encoded")
    assert store.delete_processed(gone) is True
    assert store.delete_processed(gone) is False
    assert store.get_processed(gone) is None
    assert store.get_processed(keep) is not None
    assert store.recent_jobs() == []
    store.close()


def test_dump_and_reload_config(tmp_path: Path) -> None:
    src = load_config(Path("config.example.yml"))
    dest = tmp_path / "config.yml"
    dump_config(src, dest)
    loaded = load_config(dest)
    assert loaded.evaluate.max_size_percent == src.evaluate.max_size_percent
    assert loaded.encode.video_quality == 65
    assert loaded.encode.test_mode is False
    assert loaded.encode.vaapi_device == Path("/dev/dri/renderD128")
    assert loaded.libraries[0].path == src.libraries[0].path
    assert loaded.evaluate.bitrate_caps_kbps[1080] == 3500


def test_overlay_config_mutates_lists_in_place() -> None:
    target = parse_config_payload(
        {
            "libraries": [{"path": "/old", "recursive": True}],
            "ignore_globs": ["*.tmp"],
            "encode": {"video_quality": 40},
        }
    )
    libs = target.libraries
    globs = target.ignore_globs
    source = parse_config_payload(
        {
            "libraries": [{"path": "/new", "recursive": False}],
            "ignore_globs": ["*.bak"],
            "encode": {"video_quality": 70},
        }
    )
    overlay_config(target, source)
    assert libs is target.libraries
    assert globs is target.ignore_globs
    assert target.libraries[0].path == Path("/new")
    assert target.ignore_globs == ["*.bak"]
    assert target.encode.video_quality == 70
