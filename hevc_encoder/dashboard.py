from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ValidationError

from hevc_encoder.config import (
    AppConfig,
    config_to_dict,
    dump_config,
    overlay_config,
    parse_config_payload,
)
from hevc_encoder.runtime import runtime
from hevc_encoder.store import Store

STATIC = Path(__file__).parent / "static" / "index.html"


class ForgetBody(BaseModel):
    path: str


def create_app(
    store: Store,
    cfg: AppConfig | None = None,
    config_path: Path | None = None,
) -> FastAPI:
    app = FastAPI(title="EasyBake Encodarr", docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return STATIC.read_text(encoding="utf-8")

    @app.get("/api/jobs")
    def jobs() -> list[dict]:
        return store.recent_jobs(150)

    @app.get("/api/processed")
    def processed() -> list[dict]:
        return store.processed_files(250)

    @app.get("/api/stats")
    def stats() -> dict:
        return store.stats()

    @app.get("/api/current")
    def current() -> dict | None:
        return runtime.snapshot()

    @app.post("/api/processed/forget")
    def forget(body: ForgetBody) -> dict[str, Any]:
        path = body.path.strip()
        if not path:
            raise HTTPException(status_code=400, detail="path is required")
        if not store.delete_processed(Path(path)):
            raise HTTPException(status_code=404, detail="not in processed library")
        return {"ok": True, "path": path}

    @app.post("/api/history/reset")
    def reset_history() -> dict[str, Any]:
        processed_n, jobs_n = store.clear_history()
        return {"ok": True, "processed": processed_n, "jobs": jobs_n}

    @app.get("/api/config")
    def get_config() -> dict[str, Any]:
        if cfg is None:
            raise HTTPException(status_code=404, detail="config is not loaded")
        return {
            "path": str(config_path) if config_path else None,
            "config": config_to_dict(cfg),
        }

    @app.put("/api/config")
    def put_config(payload: dict[str, Any]) -> dict[str, Any]:
        if cfg is None or config_path is None:
            raise HTTPException(status_code=404, detail="config is not loaded")
        try:
            new = parse_config_payload(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        dump_config(new, config_path)
        overlay_config(cfg, new)
        return {
            "ok": True,
            "path": str(config_path),
            "config": config_to_dict(cfg),
        }

    return app
