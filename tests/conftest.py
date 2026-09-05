from __future__ import annotations

import json
from pathlib import Path

import pytest

from hevc_encoder.config import EvaluateConfig
from hevc_encoder.evaluate import RulesEvaluator, get_evaluator
from hevc_encoder.probe import parse_probe

FIXTURES = Path(__file__).parent / "fixtures"


def video_from(name: str):
    data = json.loads((FIXTURES / name).read_text())
    return parse_probe(data, Path(f"/tmp/{name}.mkv"), size=1_000_000, mtime=1.0)


@pytest.fixture
def evaluator() -> RulesEvaluator:
    return RulesEvaluator(EvaluateConfig())
