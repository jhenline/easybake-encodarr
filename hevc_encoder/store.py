from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MEDIA_FIELDS = (
    "encoder",
    "original_path",
    "original_codec",
    "original_bitrate_kbps",
    "original_width",
    "original_height",
    "original_duration",
    "original_size",
    "output_codec",
    "output_bitrate_kbps",
    "output_size",
    "bytes_saved",
    "in_size",
    "out_size",
)

_EXTRA_COLUMNS = {
    "original_path": "TEXT",
    "original_codec": "TEXT",
    "original_bitrate_kbps": "REAL",
    "original_width": "INTEGER",
    "original_height": "INTEGER",
    "original_duration": "REAL",
    "original_size": "INTEGER",
    "output_codec": "TEXT",
    "output_bitrate_kbps": "REAL",
    "output_size": "INTEGER",
    "bytes_saved": "INTEGER",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS processed (
                    path TEXT PRIMARY KEY,
                    size INTEGER NOT NULL,
                    mtime REAL NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    encoder TEXT,
                    in_size INTEGER,
                    out_size INTEGER,
                    original_path TEXT,
                    original_codec TEXT,
                    original_bitrate_kbps REAL,
                    original_width INTEGER,
                    original_height INTEGER,
                    original_duration REAL,
                    original_size INTEGER,
                    output_codec TEXT,
                    output_bitrate_kbps REAL,
                    output_size INTEGER,
                    bytes_saved INTEGER,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT,
                    encoder TEXT,
                    in_size INTEGER,
                    out_size INTEGER,
                    duration_in REAL,
                    duration_out REAL,
                    original_path TEXT,
                    original_codec TEXT,
                    original_bitrate_kbps REAL,
                    original_width INTEGER,
                    original_height INTEGER,
                    output_codec TEXT,
                    output_bitrate_kbps REAL,
                    bytes_saved INTEGER,
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT
                );
                """
            )
            self._migrate("processed")
            self._migrate("jobs")

    def _migrate(self, table: str) -> None:
        existing = {
            row[1] for row in self._conn.execute(f"PRAGMA table_info({table})")
        }
        for name, typ in _EXTRA_COLUMNS.items():
            if name not in existing:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")

    def close(self) -> None:
        self._conn.close()

    def is_processed(self, path: Path, size: int, mtime: float) -> bool:
        row = self.get_processed(path)
        if row is None:
            return False
        return row["size"] == size and abs(row["mtime"] - mtime) < 0.001

    def get_processed(self, path: Path) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM processed WHERE path = ?", (str(path),)
            )
            row = cur.fetchone()
        return dict(row) if row else None

    def mark_processed(
        self,
        path: Path,
        size: int,
        mtime: float,
        status: str,
        reason: str,
        **media: Any,
    ) -> None:
        now = _utc_now()
        extra = {k: media.get(k) for k in MEDIA_FIELDS}
        if extra.get("in_size") is None:
            extra["in_size"] = extra.get("original_size")
        if extra.get("out_size") is None:
            extra["out_size"] = extra.get("output_size")
        cols = [
            "path",
            "size",
            "mtime",
            "status",
            "reason",
            "updated_at",
            *MEDIA_FIELDS,
        ]
        values = [str(path), size, mtime, status, reason, now, *[extra[k] for k in MEDIA_FIELDS]]
        placeholders = ", ".join("?" for _ in cols)
        assignments = ", ".join(
            f"{c}=excluded.{c}" for c in cols if c != "path"
        )
        with self._lock, self._conn:
            self._conn.execute(
                f"""
                INSERT INTO processed ({", ".join(cols)})
                VALUES ({placeholders})
                ON CONFLICT(path) DO UPDATE SET {assignments}
                """,
                values,
            )

    def delete_processed(self, path: Path) -> bool:
        """Remove a path from history (and its jobs). Does not delete video files."""
        key = str(path)
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM processed WHERE path = ?", (key,))
            self._conn.execute("DELETE FROM jobs WHERE path = ?", (key,))
            return cur.rowcount > 0

    def failed_paths(self) -> list[Path]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT path FROM processed WHERE status = 'failed'"
            )
            rows = cur.fetchall()
        return [Path(r["path"]) for r in rows]

    def clear_failed(self) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM processed WHERE status = 'failed'")
            return cur.rowcount

    def clear_history(self) -> tuple[int, int]:
        """Delete all processed-file and job records. Does not touch video files."""
        with self._lock, self._conn:
            processed = self._conn.execute("SELECT COUNT(*) FROM processed").fetchone()[0]
            jobs = self._conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
            self._conn.execute("DELETE FROM processed")
            self._conn.execute("DELETE FROM jobs")
        return int(processed), int(jobs)

    def processed_files(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM processed ORDER BY updated_at DESC, path ASC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def insert_job(self, path: Path, status: str) -> int:
        now = _utc_now()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO jobs (path, status, started_at) VALUES (?, ?, ?)",
                (str(path), status, now),
            )
            return int(cur.lastrowid)

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        allowed = {
            "status",
            "reason",
            "encoder",
            "in_size",
            "out_size",
            "duration_in",
            "duration_out",
            "original_path",
            "original_codec",
            "original_bitrate_kbps",
            "original_width",
            "original_height",
            "output_codec",
            "output_bitrate_kbps",
            "bytes_saved",
            "error",
            "finished_at",
        }
        updates = {k: v for k, v in fields.items() if k in allowed}
        if "status" in updates and updates["status"] in {
            "encoded",
            "skipped",
            "failed",
            "rejected",
            "interrupted",
        }:
            updates.setdefault("finished_at", _utc_now())
        assignments = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [job_id]
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE jobs SET {assignments} WHERE id = ?",
                values,
            )

    def recent_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,)
            )
            rows = cur.fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        with self._lock:
            cur = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS files_tracked,
                    SUM(CASE WHEN status = 'encoded' THEN 1 ELSE 0 END) AS encoded,
                    SUM(CASE WHEN status = 'skipped' THEN 1 ELSE 0 END) AS skipped,
                    SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
                    SUM(CASE WHEN status = 'rejected' THEN 1 ELSE 0 END) AS rejected,
                    SUM(CASE WHEN status = 'encoded' THEN COALESCE(original_size, in_size, 0) ELSE 0 END)
                        AS original_bytes,
                    SUM(CASE WHEN status = 'encoded' THEN COALESCE(output_size, out_size, 0) ELSE 0 END)
                        AS output_bytes,
                    SUM(CASE WHEN status = 'encoded' THEN COALESCE(bytes_saved, 0) ELSE 0 END)
                        AS bytes_saved
                FROM processed
                """
            )
            row = cur.fetchone()
        original = row["original_bytes"] or 0
        output = row["output_bytes"] or 0
        saved = row["bytes_saved"] or 0
        if saved == 0 and original and output:
            saved = original - output
        percent = (saved / original * 100.0) if original else 0.0
        return {
            "files_tracked": row["files_tracked"] or 0,
            "encoded": row["encoded"] or 0,
            "skipped": row["skipped"] or 0,
            "failed": row["failed"] or 0,
            "rejected": row["rejected"] or 0,
            "original_bytes": original,
            "output_bytes": output,
            "bytes_saved": saved,
            "percent_saved": round(percent, 1),
        }
