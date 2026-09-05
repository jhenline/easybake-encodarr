from __future__ import annotations

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.validate import duration_ok


def test_duration_within_seconds() -> None:
    cfg = EvaluateConfig(duration_tolerance_seconds=1.0, duration_tolerance_percent=2.0)
    assert duration_ok(100.0, 100.5, cfg)
    assert duration_ok(100.0, 99.2, cfg)
    assert not duration_ok(100.0, 95.0, cfg)


def test_duration_within_percent_for_long_files() -> None:
    cfg = EvaluateConfig(duration_tolerance_seconds=1.0, duration_tolerance_percent=2.0)
    # 3600s * 2% = 72s allowed
    assert duration_ok(3600.0, 3550.0, cfg)
    assert not duration_ok(3600.0, 3400.0, cfg)


def test_zero_duration_rejected() -> None:
    cfg = EvaluateConfig()
    assert not duration_ok(0, 10, cfg)
    assert not duration_ok(10, 0, cfg)
