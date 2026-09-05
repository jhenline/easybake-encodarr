from __future__ import annotations

import os

from hevc_encoder.cli import _pid_is_running


def test_pid_is_running_self() -> None:
    assert _pid_is_running(os.getpid())
    assert not _pid_is_running(999_999_999)
