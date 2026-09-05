from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import typer
import uvicorn
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from hevc_encoder.config import load_config
from hevc_encoder.dashboard import create_app
from hevc_encoder.pipeline import process_file, scan_libraries, should_stop_in_test_mode
from hevc_encoder.scan import iter_leftovers
from hevc_encoder.store import Store
from hevc_encoder.watch import watch_libraries

app = typer.Typer(
    add_completion=False,
    help="EasyBake Encodarr: scan folders and re-encode videos to HEVC.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)],
        force=True,
    )


def resolve_config_path(config: Path) -> Path:
    path = config
    if not path.exists() and path.name == "config.yml":
        alt = path.with_name("config.yaml")
        if alt.exists():
            path = alt
    if not path.exists():
        raise typer.BadParameter(
            f"config not found: {config}. Copy config.example.yml to "
            "config.yml or config.yaml and set your library paths."
        )
    return path


def _load(config: Path):
    path = resolve_config_path(config)
    try:
        return load_config(path)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _fmt_bytes(n: int | None) -> str:
    if n is None:
        return "—"
    step = 1024.0
    value = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < step or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= step
    return f"{n} B"


def _fmt_kbps(n: float | None) -> str:
    if n is None or n == 0:
        return "—"
    if n >= 1000:
        return f"{n / 1000:.1f} Mbps"
    return f"{n:.0f} kbps"


def _dashboard_pid_file(cfg) -> Path:
    return cfg.state_db.expanduser().parent / "dashboard.pid"


def _pid_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_dashboard_pid(cfg) -> int | None:
    path = _dashboard_pid_file(cfg)
    if not path.exists():
        return None
    try:
        pid = int(path.read_text().strip())
    except ValueError:
        return None
    if _pid_is_running(pid):
        return pid
    path.unlink(missing_ok=True)
    return None


def _stop_dashboard(cfg) -> bool:
    pid = _read_dashboard_pid(cfg)
    path = _dashboard_pid_file(cfg)
    if pid is None:
        path.unlink(missing_ok=True)
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        path.unlink(missing_ok=True)
        return False
    for _ in range(20):
        if not _pid_is_running(pid):
            break
        time.sleep(0.1)
    if _pid_is_running(pid):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    path.unlink(missing_ok=True)
    return True


@app.command()
def scan(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Evaluate only; do not encode."),
    wait_stable: bool = typer.Option(
        False, "--wait-stable", help="Wait until each file stops growing."
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Scan configured libraries once."""
    _setup_logging(verbose)
    cfg = _load(config)
    store = Store(cfg.state_db)
    try:
        counts = scan_libraries(cfg, store, dry_run=dry_run, wait_stable=wait_stable)
        if not counts:
            console.print("[yellow]No video files found.[/yellow]")
            return
        parts = [f"{n} {name}" for name, n in sorted(counts.items())]
        console.print("Done: " + ", ".join(parts))
    finally:
        store.close()


@app.command()
def watch(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    dashboard: bool = typer.Option(True, "--dashboard/--no-dashboard"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Watch libraries and encode new or changed files."""
    _setup_logging(verbose)
    cfg = _load(config)
    store = Store(cfg.state_db)
    stop = threading.Event()

    def run_watch() -> None:
        watch_libraries(cfg, store, stop)

    thread = None
    try:
        if dashboard:
            thread = threading.Thread(target=run_watch, name="hevc-watch", daemon=True)
            thread.start()
            console.print(
                f"Dashboard: http://{cfg.dashboard.host}:{cfg.dashboard.port}"
            )
            uvicorn.run(
                create_app(
                    store,
                    cfg=cfg,
                    config_path=resolve_config_path(config).resolve(),
                ),
                host=cfg.dashboard.host,
                port=cfg.dashboard.port,
                log_level="warning",
            )
        else:
            run_watch()
    except KeyboardInterrupt:
        stop.set()
    finally:
        stop.set()
        store.close()
        if thread:
            thread.join(timeout=2)


@app.command()
def status(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    limit: int = typer.Option(20, "--limit", "-n"),
) -> None:
    """Show processed files, codecs, sizes, and space saved."""
    _setup_logging()
    cfg = _load(config)
    store = Store(cfg.state_db)
    try:
        stats = store.stats()
        console.print(
            f"tracked={stats['files_tracked']}  encoded={stats['encoded']}  "
            f"skipped={stats['skipped']}  rejected={stats['rejected']}  "
            f"failed={stats['failed']}"
        )
        console.print(
            f"original={_fmt_bytes(stats['original_bytes'])}  "
            f"new={_fmt_bytes(stats['output_bytes'])}  "
            f"saved={_fmt_bytes(stats['bytes_saved'])} "
            f"({stats['percent_saved']}%)"
        )
        rows = store.processed_files(limit)
        table = Table(show_header=True, header_style="bold")
        table.add_column("Status")
        table.add_column("File", overflow="fold")
        table.add_column("Codec")
        table.add_column("Bitrate")
        table.add_column("Original")
        table.add_column("New")
        table.add_column("Saved")
        for row in rows:
            orig = row["original_codec"] or "—"
            out = row["output_codec"] or orig
            codec = f"{orig} → {out}" if row["status"] == "encoded" and out != orig else orig
            br_in = _fmt_kbps(row["original_bitrate_kbps"])
            br_out = _fmt_kbps(row["output_bitrate_kbps"])
            bitrate = (
                f"{br_in} → {br_out}"
                if row["status"] == "encoded" and br_out != "—" and br_out != br_in
                else br_in
            )
            saved = row["bytes_saved"] if row["status"] == "encoded" else None
            table.add_row(
                str(row["status"]),
                str(row["path"]),
                codec,
                bitrate,
                _fmt_bytes(row["original_size"] or row["in_size"]),
                _fmt_bytes(row["output_size"] or row["out_size"])
                if row["status"] == "encoded"
                else "—",
                _fmt_bytes(saved) if saved is not None else "—",
            )
        if rows:
            console.print(table)
        else:
            console.print("[yellow]No processed files recorded yet.[/yellow]")
    finally:
        store.close()


@app.command()
def retry(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Re-queue files that previously failed."""
    _setup_logging(verbose)
    cfg = _load(config)
    store = Store(cfg.state_db)
    try:
        paths = store.failed_paths()
        cleared = store.clear_failed()
        console.print(f"Cleared {cleared} failed record(s).")
        counts: dict[str, int] = {}
        for path in paths:
            if not path.exists():
                continue
            status = process_file(path, cfg, store)
            counts[status] = counts.get(status, 0) + 1
            if cfg.encode.test_mode and should_stop_in_test_mode(status):
                break
        if counts:
            parts = [f"{n} {name}" for name, n in sorted(counts.items())]
            console.print("Retry: " + ", ".join(parts))
        else:
            console.print("Nothing to retry.")
    finally:
        store.close()


@app.command()
def reset(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt."),
) -> None:
    """Clear encode history so the next scan re-evaluates every file.

    Does not delete video files. Use `clean` for leftover temps/sidecars.
    """
    _setup_logging()
    cfg = _load(config)
    store = Store(cfg.state_db)
    try:
        stats = store.stats()
        console.print(
            f"This will forget {stats['files_tracked']} tracked file(s) "
            f"in {cfg.state_db}"
        )
        if not yes and not typer.confirm("Clear history?"):
            raise typer.Abort()
        processed, jobs = store.clear_history()
        console.print(f"Cleared {processed} processed file(s) and {jobs} job(s).")
    finally:
        store.close()


@app.command()
def clean(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    sidecars: bool = typer.Option(
        False, "--sidecars", help="Also delete *.hevc.mkv test outputs."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="List files without deleting."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt."),
) -> None:
    """Remove leftover encode temps (.encoding.mkv, .bak) from library folders."""
    _setup_logging()
    cfg = _load(config)
    found: list[Path] = []
    for lib in cfg.libraries:
        found.extend(
            iter_leftovers(lib.path, lib.recursive, include_sidecars=sidecars)
        )
    found = sorted(set(found))
    if not found:
        console.print("No leftover encode files found.")
        return
    for path in found:
        console.print(str(path))
    if dry_run:
        console.print(f"{len(found)} file(s) would be deleted.")
        return
    if not yes and not typer.confirm(f"Delete {len(found)} file(s)?"):
        raise typer.Abort()
    deleted = 0
    for path in found:
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            console.print(f"[red]could not delete {path}: {exc}[/red]")
    console.print(f"Deleted {deleted} file(s).")


@app.command()
def serve(
    config: Path = typer.Option(Path("config.yml"), "--config", "-c"),
    host: Optional[str] = typer.Option(None),
    port: Optional[int] = typer.Option(None),
    background: bool = typer.Option(
        False,
        "--background",
        "-d",
        help="Start in the background and return this terminal.",
    ),
    stop: bool = typer.Option(False, "--stop", help="Stop a background dashboard."),
) -> None:
    """Serve the local dashboard (does not encode).

    Foreground (default) occupies the terminal until Ctrl+C. Use --background
    to keep the shell free.
    """
    _setup_logging()
    cfg = _load(config)
    bind_host = host or cfg.dashboard.host
    bind_port = port or cfg.dashboard.port
    url = f"http://{bind_host}:{bind_port}"

    if stop:
        if _stop_dashboard(cfg):
            console.print("Dashboard stopped.")
        else:
            console.print("No background dashboard is running.")
        return

    running = _read_dashboard_pid(cfg)
    if background and running is not None:
        console.print(f"Dashboard already running (pid {running}): {url}")
        return

    if background:
        config_file = config if config.exists() else config.with_name("config.yaml")
        log_path = _dashboard_pid_file(cfg).with_name("dashboard.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable,
            "-m",
            "hevc_encoder",
            "serve",
            "--config",
            str(config_file.resolve()),
            "--host",
            bind_host,
            "--port",
            str(bind_port),
        ]
        with log_path.open("a", encoding="utf-8") as log:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        time.sleep(0.4)
        if proc.poll() is not None:
            console.print(
                f"[red]Dashboard failed to start. See {log_path}[/red]"
            )
            raise typer.Exit(code=1)
        _dashboard_pid_file(cfg).write_text(str(proc.pid), encoding="utf-8")
        console.print(f"Dashboard: {url}")
        console.print(f"Running in background (pid {proc.pid}). Stop with: easybake serve --stop")
        return

    store = Store(cfg.state_db)
    console.print(f"Dashboard: {url}")
    console.print("Foreground server — Ctrl+C to stop, or use `serve --background`.")
    try:
        uvicorn.run(
            create_app(
                store,
                cfg=cfg,
                config_path=resolve_config_path(config).resolve(),
            ),
            host=bind_host,
            port=bind_port,
            log_level="warning",
        )
    finally:
        store.close()
