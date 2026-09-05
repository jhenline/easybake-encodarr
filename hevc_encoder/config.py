from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, field_validator

DEFAULT_IGNORE_GLOBS = [
    "._*",
    "*.partial",
    "*.!qB",
    "*.tmp",
    "*.bak",
    "*.encoding.mkv",
    "*.encoding.*",
    "*.encoding.remux.mkv",
    "*.hevc.mkv",
]


class LibraryConfig(BaseModel):
    path: Path
    recursive: bool = True


class EvaluateConfig(BaseModel):
    mode: Literal["rules", "vmaf"] = "rules"
    skip_if_hevc: bool = True
    reconvert_hevc: bool = False
    skip_hdr: bool = True
    max_size_percent: float = 90
    bitrate_caps_kbps: dict[int, int] = Field(
        default_factory=lambda: {720: 1000, 1080: 3500, 2160: 16000}
    )
    bpp_skip_below: float = 0.10
    duration_tolerance_percent: float = 2.0
    duration_tolerance_seconds: float = 1.0

    @field_validator("bitrate_caps_kbps", mode="before")
    @classmethod
    def _int_keys(cls, value: Any) -> dict[int, int]:
        if not isinstance(value, dict):
            return value
        return {int(k): int(v) for k, v in value.items()}


class EncodeConfig(BaseModel):
    encoder: Literal["auto", "videotoolbox", "libx265", "qsv"] = "auto"
    video_quality: int = 65
    container: Literal["mkv"] = "mkv"
    audio: Literal["copy"] = "copy"
    subtitles: Literal["copy"] = "copy"
    jobs: int = 1
    temp_dir: Path | None = None
    replace_original: bool = True
    test_mode: bool = False


class WatchConfig(BaseModel):
    stable_seconds: int = 30


class DashboardConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8745


class AppConfig(BaseModel):
    libraries: list[LibraryConfig] = Field(default_factory=list)
    evaluate: EvaluateConfig = Field(default_factory=EvaluateConfig)
    encode: EncodeConfig = Field(default_factory=EncodeConfig)
    watch: WatchConfig = Field(default_factory=WatchConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    state_db: Path = Field(
        default_factory=lambda: Path.home() / ".hevc-encoder" / "encoder.db"
    )
    ignore_globs: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE_GLOBS))
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"

    @field_validator("state_db", mode="before")
    @classmethod
    def _expand_state_db(cls, value: Any) -> Any:
        if value is None:
            return Path.home() / ".hevc-encoder" / "encoder.db"
        path = Path(value).expanduser()
        return path


CONFIG_HEADER = (
    "# HEVC encoder configuration\n"
    "# Saved from the dashboard. Inline comments are not preserved.\n\n"
)


def normalize_config(cfg: AppConfig) -> AppConfig:
    cfg.state_db = cfg.state_db.expanduser()
    if cfg.encode.temp_dir is not None:
        cfg.encode.temp_dir = cfg.encode.temp_dir.expanduser()
    for lib in cfg.libraries:
        lib.path = lib.path.expanduser()
    return cfg


def load_config(path: Path) -> AppConfig:
    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"invalid YAML in {path}: {exc}") from exc
    return normalize_config(AppConfig.model_validate(data))


def parse_config_payload(data: dict[str, Any]) -> AppConfig:
    return normalize_config(AppConfig.model_validate(data))


class _IndentedDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> None:
        super().increase_indent(flow, False)


def _yaml_scalar(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def config_to_dict(cfg: AppConfig) -> dict[str, Any]:
    """Plain data for JSON and YAML. Paths are strings; bitrate keys are ints."""
    encode: dict[str, Any] = {
        "encoder": cfg.encode.encoder,
        "video_quality": cfg.encode.video_quality,
        "container": cfg.encode.container,
        "audio": cfg.encode.audio,
        "subtitles": cfg.encode.subtitles,
        "jobs": cfg.encode.jobs,
        "replace_original": cfg.encode.replace_original,
        "test_mode": cfg.encode.test_mode,
    }
    if cfg.encode.temp_dir is not None:
        encode["temp_dir"] = str(cfg.encode.temp_dir)
    return {
        "libraries": [
            {"path": str(lib.path), "recursive": bool(lib.recursive)}
            for lib in cfg.libraries
        ],
        "evaluate": {
            "mode": cfg.evaluate.mode,
            "skip_if_hevc": cfg.evaluate.skip_if_hevc,
            "reconvert_hevc": cfg.evaluate.reconvert_hevc,
            "skip_hdr": cfg.evaluate.skip_hdr,
            "max_size_percent": _yaml_scalar(cfg.evaluate.max_size_percent),
            "bitrate_caps_kbps": {
                int(k): int(v) for k, v in cfg.evaluate.bitrate_caps_kbps.items()
            },
            "bpp_skip_below": cfg.evaluate.bpp_skip_below,
            "duration_tolerance_percent": _yaml_scalar(
                cfg.evaluate.duration_tolerance_percent
            ),
            "duration_tolerance_seconds": _yaml_scalar(
                cfg.evaluate.duration_tolerance_seconds
            ),
        },
        "encode": encode,
        "watch": {"stable_seconds": cfg.watch.stable_seconds},
        "dashboard": {"host": cfg.dashboard.host, "port": cfg.dashboard.port},
        "state_db": str(cfg.state_db),
        "ffmpeg": cfg.ffmpeg,
        "ffprobe": cfg.ffprobe,
        "ignore_globs": list(cfg.ignore_globs),
    }


def dump_config(cfg: AppConfig, path: Path) -> None:
    body = yaml.dump(
        config_to_dict(cfg),
        Dumper=_IndentedDumper,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(CONFIG_HEADER + body, encoding="utf-8")
    tmp.replace(path)


def overlay_config(target: AppConfig, source: AppConfig) -> None:
    """Copy values into an existing config object (watch/scan keep the same ref)."""
    target.evaluate = source.evaluate
    target.encode = source.encode
    target.watch = source.watch
    target.dashboard = source.dashboard
    target.state_db = source.state_db
    target.ffmpeg = source.ffmpeg
    target.ffprobe = source.ffprobe
    target.libraries[:] = list(source.libraries)
    target.ignore_globs[:] = list(source.ignore_globs)
