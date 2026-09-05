from __future__ import annotations

import fnmatch
import os
import time
from collections.abc import Iterator
from pathlib import Path

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".mov",
    ".avi",
    ".wmv",
    ".ts",
    ".m2ts",
    ".webm",
}

SAMPLE_DIR_NAMES = {"sample", "samples"}


def is_ignored(path: Path, ignore_globs: list[str]) -> bool:
    name = path.name
    lowered = name.lower()
    # macOS AppleDouble resource forks (._Movie.mkv) are not videos.
    if name.startswith("._"):
        return True
    if ".encoding." in lowered:
        return True
    if lowered.endswith(".hevc.mkv"):
        return True
    if lowered.endswith(".bak"):
        return True
    for glob in ignore_globs:
        if fnmatch.fnmatch(name, glob) or fnmatch.fnmatch(lowered, glob.lower()):
            return True
    parts = {p.lower() for p in path.parts}
    if parts & SAMPLE_DIR_NAMES:
        return True
    if lowered == "sample" or lowered.startswith("sample."):
        return True
    return False


def is_video_path(path: Path, ignore_globs: list[str]) -> bool:
    if not path.is_file():
        return False
    if path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    return not is_ignored(path, ignore_globs)


def iter_videos(root: Path, recursive: bool, ignore_globs: list[str]) -> Iterator[Path]:
    if not root.exists():
        return
    if root.is_file():
        if is_video_path(root, ignore_globs):
            yield root
        return
    pattern = "**/*" if recursive else "*"
    for path in root.glob(pattern):
        if is_video_path(path, ignore_globs):
            yield path


def is_leftover(path: Path, *, include_sidecars: bool = False) -> bool:
    if not path.is_file():
        return False
    name = path.name.lower()
    if ".encoding." in name:
        return True
    if name.endswith(".bak"):
        return True
    if include_sidecars and name.endswith(".hevc.mkv"):
        return True
    return False


def iter_leftovers(
    root: Path, recursive: bool, *, include_sidecars: bool = False
) -> Iterator[Path]:
    if not root.exists():
        return
    if root.is_file():
        if is_leftover(root, include_sidecars=include_sidecars):
            yield root
        return
    if not recursive:
        for path in root.iterdir():
            if is_leftover(path, include_sidecars=include_sidecars):
                yield path
        return
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            path = Path(dirpath) / name
            if is_leftover(path, include_sidecars=include_sidecars):
                yield path


def wait_until_stable(path: Path, stable_seconds: int, poll: float = 1.0) -> bool:
    """Return True if size is unchanged for ``stable_seconds``. False if the file vanishes."""
    if stable_seconds <= 0:
        return path.exists()
    last_size: int | None = None
    last_change = time.monotonic()
    while True:
        if not path.exists():
            return False
        size = path.stat().st_size
        now = time.monotonic()
        if last_size is None or size != last_size:
            last_size = size
            last_change = now
        elif now - last_change >= stable_seconds:
            return True
        time.sleep(poll)
