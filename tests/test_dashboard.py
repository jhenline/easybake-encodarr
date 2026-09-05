from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from hevc_encoder.config import dump_config, load_config
from hevc_encoder.dashboard import create_app
from hevc_encoder.runtime import runtime
from hevc_encoder.store import Store


def test_dashboard_pages(tmp_path: Path) -> None:
    store = Store(tmp_path / "encoder.db")
    job_id = store.insert_job(tmp_path / "movie.mkv", "encoding")
    store.update_job(job_id, status="skipped", reason="already hevc")
    store.mark_processed(
        tmp_path / "movie.mkv",
        100,
        1.0,
        "skipped",
        "already hevc",
        original_codec="hevc",
        original_bitrate_kbps=2100,
        original_size=100,
        output_codec="hevc",
        output_size=100,
        bytes_saved=0,
    )
    runtime.start("/tmp/movie.mkv", "hevc_videotoolbox")
    runtime.progress({"percent": 12.5, "time": "00:01:00"})
    try:
        client = TestClient(create_app(store))
        home = client.get("/")
        assert home.status_code == 200
        assert "EasyBake Encodarr" in home.text
        assert "Processed files" in home.text
        assert "Space saved" in home.text
        assert "Settings" in home.text
        assert "Test mode" in home.text
        assert "Reset history" in home.text
        stats = client.get("/api/stats").json()
        assert stats["skipped"] == 1
        files = client.get("/api/processed").json()
        assert files[0]["original_codec"] == "hevc"
        jobs = client.get("/api/jobs").json()
        assert jobs[0]["reason"] == "already hevc"
        current = client.get("/api/current").json()
        assert current["path"] == "/tmp/movie.mkv"
        assert current["percent"] == 12.5
        missing = client.post("/api/processed/forget", json={"path": "/nope.mkv"})
        assert missing.status_code == 404
        forgot = client.post(
            "/api/processed/forget", json={"path": str(tmp_path / "movie.mkv")}
        )
        assert forgot.status_code == 200
        assert client.get("/api/processed").json() == []
        store.mark_processed(tmp_path / "other.mkv", 1, 1.0, "failed", "boom")
        reset = client.post("/api/history/reset")
        assert reset.status_code == 200
        assert reset.json()["processed"] == 1
        assert client.get("/api/stats").json()["files_tracked"] == 0
    finally:
        runtime.clear()
        store.close()


def test_dashboard_config_roundtrip(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.yml"
    dump_config(load_config(Path("config.example.yml")), cfg_path)
    cfg = load_config(cfg_path)
    store = Store(tmp_path / "encoder.db")
    try:
        client = TestClient(create_app(store, cfg=cfg, config_path=cfg_path))
        got = client.get("/api/config")
        assert got.status_code == 200
        assert got.json()["path"] == str(cfg_path)
        payload = got.json()["config"]
        payload["libraries"] = [{"path": str(tmp_path / "film"), "recursive": True}]
        payload["encode"]["video_quality"] = 55
        payload["encode"]["replace_original"] = False
        payload["encode"]["test_mode"] = True
        saved = client.put("/api/config", json=payload)
        assert saved.status_code == 200, saved.text
        assert saved.json()["config"]["encode"]["video_quality"] == 55
        assert cfg.encode.video_quality == 55
        reloaded = load_config(cfg_path)
        assert reloaded.encode.video_quality == 55
        assert reloaded.encode.replace_original is False
        assert reloaded.encode.test_mode is True
        assert reloaded.libraries[0].path == tmp_path / "film"
        bad = client.put("/api/config", json={"encode": {"encoder": "nope"}})
        assert bad.status_code == 422
    finally:
        store.close()
