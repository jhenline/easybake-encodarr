from __future__ import annotations

import queue
from pathlib import Path

from hevc_encoder.config import DEFAULT_IGNORE_GLOBS
from hevc_encoder.watch import _Handler


def test_watch_handler_enqueues_videos(tmp_path: Path) -> None:
    work: queue.Queue[Path] = queue.Queue()
    handler = _Handler(work, DEFAULT_IGNORE_GLOBS)
    movie = tmp_path / "movie.mkv"
    movie.write_bytes(b"x")

    class Event:
        is_directory = False
        src_path = str(movie)

    handler.on_created(Event())
    assert work.get_nowait() == movie


def test_watch_handler_ignores_partial(tmp_path: Path) -> None:
    work: queue.Queue[Path] = queue.Queue()
    handler = _Handler(work, DEFAULT_IGNORE_GLOBS)
    partial = tmp_path / "movie.mkv.partial"
    partial.write_bytes(b"x")

    class Event:
        is_directory = False
        src_path = str(partial)

    handler.on_created(Event())
    assert work.empty()
