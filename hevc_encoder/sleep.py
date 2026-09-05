from __future__ import annotations

import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager


@contextmanager
def prevent_sleep() -> Iterator[None]:
    """Keep the Mac awake while an encode is running."""
    if sys.platform != "darwin":
        yield
        return
    proc = subprocess.Popen(
        ["caffeinate", "-i"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
