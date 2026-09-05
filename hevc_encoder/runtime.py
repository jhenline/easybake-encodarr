from __future__ import annotations

import threading
from typing import Any


class RuntimeState:
    """In-memory encode progress for the dashboard."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.current: dict[str, Any] | None = None

    def start(self, path: str, encoder: str | None = None) -> None:
        with self._lock:
            self.current = {
                "path": path,
                "encoder": encoder,
                "percent": None,
                "time": None,
                "speed": None,
                "fps": None,
            }

    def progress(self, data: dict[str, Any]) -> None:
        with self._lock:
            if self.current is None:
                return
            self.current.update(data)

    def clear(self) -> None:
        with self._lock:
            self.current = None

    def snapshot(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self.current) if self.current else None


runtime = RuntimeState()
