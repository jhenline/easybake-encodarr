from __future__ import annotations

import logging
import queue
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from hevc_encoder.config import AppConfig
from hevc_encoder.pipeline import process_file, should_stop_in_test_mode
from hevc_encoder.scan import VIDEO_EXTENSIONS, is_ignored
from hevc_encoder.store import Store

log = logging.getLogger("hevc_encoder")


class _Handler(FileSystemEventHandler):
    def __init__(self, work: queue.Queue[Path], ignore_globs: list[str]) -> None:
        super().__init__()
        self.work = work
        self.ignore_globs = ignore_globs

    def on_created(self, event: FileSystemEvent) -> None:
        self._offer(event)

    def on_modified(self, event: FileSystemEvent) -> None:
        self._offer(event)

    def on_moved(self, event: FileSystemEvent) -> None:
        dest = getattr(event, "dest_path", None)
        if dest:
            self._enqueue(Path(dest))

    def _offer(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._enqueue(Path(event.src_path))

    def _enqueue(self, path: Path) -> None:
        if path.suffix.lower() not in VIDEO_EXTENSIONS:
            return
        if is_ignored(path, self.ignore_globs):
            return
        self.work.put(path)


def watch_libraries(cfg: AppConfig, store: Store, stop: threading.Event) -> None:
    work: queue.Queue[Path] = queue.Queue()
    queued: set[str] = set()
    queued_lock = threading.Lock()
    handler = _Handler(work, cfg.ignore_globs)
    observer = Observer()

    for lib in cfg.libraries:
        root = lib.path.expanduser()
        if not root.exists():
            log.warning("library does not exist, skipping watch: %s", root)
            continue
        observer.schedule(handler, str(root), recursive=lib.recursive)
        log.info("watching %s", root)

    test_done = False

    def worker() -> None:
        nonlocal test_done
        while not stop.is_set():
            try:
                path = work.get(timeout=0.5)
            except queue.Empty:
                continue
            if cfg.encode.test_mode and test_done:
                log.info("test_mode: ignoring %s (already processed one file)", path.name)
                work.task_done()
                continue
            key = str(path.resolve()) if path.exists() else str(path)
            with queued_lock:
                if key in queued:
                    work.task_done()
                    continue
                queued.add(key)
            try:
                status = process_file(path, cfg, store, wait_stable=True)
                if cfg.encode.test_mode and should_stop_in_test_mode(status):
                    test_done = True
                    log.info("test_mode: will not process further files")
            except Exception:
                log.exception("watch worker failed on %s", path)
            finally:
                with queued_lock:
                    queued.discard(key)
                work.task_done()

    observer.start()
    if cfg.encode.test_mode:
        log.info("test_mode is on — encoding at most one file")
    thread = threading.Thread(target=worker, name="hevc-watch-worker", daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            time.sleep(0.4)
    finally:
        observer.stop()
        observer.join(timeout=5)
        thread.join(timeout=2)
