"""Loopback-only local API for bundled demos and isolated CSV uploads."""
from __future__ import annotations

import csv
import io
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .artifacts import build_artifact_zip
from .contracts import RunConfig
from .core import reduce_csv

_MAX_CSV_BYTES = 5 * 1024 * 1024
_MAX_SCRIPT_BYTES = 100 * 1024
_MAX_ROWS = 10_000
_MAX_COLUMNS = 100
_MAX_JOBS = 10
_MAX_EVENTS = 100
_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_HOSTS = {"localhost", "127.0.0.1", "testserver"}


@dataclass
class Job:
    id: str
    demo: bool
    status: str = "queued"
    events: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] | None = None
    error: str | None = None
    preview: dict[str, Any] | None = None
    archive: bytes | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    work_dir: Path | None = None


def _examples_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here.parent / "examples", here / "examples"):
        if candidate.is_dir():
            return candidate
    return here / "examples"


def _metadata(example_root: Path, example_id: str) -> dict[str, Any] | None:
    if not re.fullmatch(r"[a-z0-9_-]{1,48}", example_id):
        return None
    folder = (example_root / example_id).resolve()
    try:
        folder.relative_to(example_root.resolve())
        data = json.loads((folder / "metadata.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return None
    if data.get("id") != example_id or not (folder / "data.csv").is_file() or not (folder / "repro.py").is_file():
        return None
    # Explicit response allowlist means metadata cannot inject arbitrary fields.
    return {key: data[key] for key in ("id", "name", "description", "rows", "sensitive_columns", "bug", "expected") if key in data}


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if not docker:
        return False
    try:
        daemon = subprocess.run([docker, "info"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                timeout=2, check=False)
        if daemon.returncode:
            return False
        image = subprocess.run([docker, "image", "inspect", "python:3.12-slim"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=2, check=False)
        return image.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _read_preview(path: Path, limit: int = 5) -> list[dict[str, str]]:
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            sample = handle.read(8192)
            handle.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter if sample else ","
            except csv.Error:
                delimiter = ","
            reader = csv.DictReader(handle, delimiter=delimiter)
            return [dict(row) for _, row in zip(range(limit), reader)]
    except (OSError, UnicodeError, csv.Error):
        return []


def _append_event(job: Job, event: dict[str, Any]) -> None:
    # Core events are engine-authored. Copy only the public, bounded fields.
    clean: dict[str, Any] = {
        "type": str(event.get("type", "progress"))[:30],
        "message": str(event.get("message", "Progress"))[:300],
    }
    for key in ("rows", "runs", "elapsed"):
        value = event.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clean[key] = value
    with job.lock:
        job.events.append(clean)
        del job.events[:-_MAX_EVENTS]


def _mark_privacy_review(output: Path, report: dict[str, Any]) -> None:
    """Turn an artifact-scan failure into a review result with no candidate."""
    for name in ("specimen.csv", "repro.py"):
        try:
            (output / name).unlink()
        except OSError:
            pass
    report["status"] = "needs_review"
    blockers = report.get("blockers")
    if not isinstance(blockers, list):
        blockers = []
    blockers.append("public artifact privacy checks did not pass")
    report["blockers"] = blockers
    report["artifact_names"] = ["report.json", "REPORT.md"]
    safe_report = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    try:
        (output / "report.json").write_text(safe_report, encoding="utf-8")
        (output / "REPORT.md").write_text(
            "# Specimen report\n\n"
            "Status: **needs_review**\n\n"
            "A public artifact privacy check did not pass. Candidate files were removed; review the source locally.\n",
            encoding="utf-8",
        )
    except OSError:
        # The job response still carries the safe generic blocker and never
        # serves this private output directory directly.
        pass


def _model_assist(config: RunConfig, job: Job) -> None:
    try:
        from .model import advise
        policy = advise(config)
        if isinstance(policy, dict):
            config.policy = policy
    except Exception:
        _append_event(job, {"type": "warning", "message": "Model adviser unavailable; deterministic policy will be used."})


def _run_job(job: Job, config: RunConfig, before_path: Path | None,
             model_assist: bool = False, source_path: Path | None = None,
             script_path: Path | None = None, sensitive_columns: list[str] | None = None) -> None:
    with job.lock:
        job.status = "running"
    try:
        if model_assist:
            _model_assist(config, job)
        if before_path is not None:
            job.preview = {"before": _read_preview(before_path)}
        report = reduce_csv(config, on_event=lambda event: _append_event(job, event))
        after_path = config.output_dir / "specimen.csv"
        if report.get("status") == "verified" and source_path and script_path:
            job.archive = build_artifact_zip(config.output_dir, source_path, script_path,
                                             sensitive_columns or [])
            if job.archive is None:
                _mark_privacy_review(config.output_dir, report)
                _append_event(job, {"type": "warning", "message": "Privacy checks blocked the evidence download."})
        # Upload after rows are eligible for display only after the full
        # public-artifact scan passes. In particular, an email copied into an
        # otherwise non-sensitive column must not escape through the preview.
        after = _read_preview(after_path) if after_path.is_file() else []
        if job.demo and before_path is not None:
            preview = {"before": _read_preview(before_path), "after": after}
        elif report.get("status") == "verified" and job.archive:
            preview = {"after": after}
        else:
            preview = {"after": []} if not job.demo else {"after": after}
        with job.lock:
            job.report = report
            job.preview = preview
        with job.lock:
            job.status = "complete"
    except Exception:
        # Exception text can contain paths, source values, or command details.
        with job.lock:
            job.status = "error"
            job.error = "The job could not be completed safely. Check the input and runner, then try again."
        _append_event(job, {"type": "error", "message": "The job could not be completed safely."})
    finally:
        # Keep only generated evidence and the bounded in-memory download.
        if not job.demo and job.work_dir is not None:
            for path in (job.work_dir / "source.csv", job.work_dir / "source.py"):
                try:
                    path.unlink()
                except OSError:
                    pass


def create_app(*, example_root: Path | None = None, storage_root: Path | None = None,
               docker_probe=None) -> FastAPI:
    examples = Path(example_root or _examples_root()).resolve()
    owns_storage = storage_root is None
    storage = Path(storage_root) if storage_root else Path(tempfile.gettempdir()) / f"specimen-server-{uuid.uuid4().hex}"
    probe = docker_probe or _docker_available
    probe_cache: tuple[float, bool] = (0.0, False)
    probe_lock = threading.Lock()
    jobs: dict[str, Job] = {}
    jobs_lock = threading.RLock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="specimen")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        yield
        executor.shutdown(wait=False, cancel_futures=True)
        if owns_storage:
            shutil.rmtree(storage, ignore_errors=True)

    app = FastAPI(title="Specimen", version=__version__, docs_url=None, redoc_url=None, lifespan=lifespan)
    app.state.jobs = jobs
    app.state.storage = storage

    @app.middleware("http")
    async def loopback_and_origin_guard(request: Request, call_next):
        host = request.headers.get("host", "").lower()
        parsed_host = None
        try:
            parsed_host = urlsplit("//" + host)
            hostname = parsed_host.hostname
            port = parsed_host.port
        except ValueError:
            hostname, port = None, None
        if (parsed_host is None or hostname not in _ALLOWED_HOSTS or parsed_host.username or parsed_host.password or
                (port is not None and not 1 <= port <= 65535)):
            return JSONResponse({"detail": "Only loopback requests are accepted."}, status_code=403)
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin:
                try:
                    parsed = urlsplit(origin)
                    # Touch the parsed authority fields here. They can raise on
                    # malformed values such as `http://[` even when urlsplit
                    # itself accepted the string.
                    origin_host = parsed.hostname
                    parsed.port
                    valid = (
                        parsed.scheme == request.url.scheme
                        and parsed.netloc.lower() == host
                        and origin_host is not None
                        and parsed.username is None
                        and parsed.password is None
                        and parsed.path in {"", "/"}
                        and not parsed.query
                        and not parsed.fragment
                    )
                except ValueError:
                    valid = False
                if not valid:
                    return JSONResponse({"detail": "Cross-origin mutations are not allowed."}, status_code=403)
            elif request.headers.get("sec-fetch-site", "").casefold() == "cross-site":
                return JSONResponse({"detail": "Cross-origin mutations are not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    def docker_ready() -> bool:
        nonlocal probe_cache
        with probe_lock:
            now = time.monotonic()
            if now - probe_cache[0] > 5:
                try:
                    available = bool(probe())
                except Exception:
                    available = False
                probe_cache = (now, available)
            return probe_cache[1]

    @app.get("/api/health")
    async def health():
        model_name = os.getenv("SPECIMEN_MODEL_NAME") if os.getenv("SPECIMEN_MODEL_BASE_URL") else None
        try:
            from .model import configuration
            model_status = configuration()
            if isinstance(model_status, dict):
                configured = bool(model_status.get("configured"))
                model_name = model_status.get("model") if configured else None
            else:
                configured = bool(model_name)
        except Exception:
            configured = bool(model_name)
        return {"status": "ok", "docker_available": docker_ready(),
                "model": {"configured": configured, "model": model_name if configured else None},
                "version": __version__}

    @app.get("/api/examples")
    async def list_examples():
        result = []
        if examples.is_dir():
            for entry in sorted(examples.iterdir()):
                meta = _metadata(examples, entry.name)
                if meta is not None:
                    result.append(meta)
        return result

    def add_job(job: Job, config: RunConfig, before_path: Path | None,
                model_assist: bool, source_path: Path | None = None,
                script_path: Path | None = None, sensitive: list[str] | None = None) -> dict[str, str]:
        with jobs_lock:
            if len(jobs) >= _MAX_JOBS:
                # Reclaim only finished jobs; never evict an active job.
                finished = [item for item in jobs.values() if item.status in {"complete", "error"}]
                while len(jobs) >= _MAX_JOBS and finished:
                    old = finished.pop(0)
                    jobs.pop(old.id, None)
                    if old.work_dir:
                        shutil.rmtree(old.work_dir, ignore_errors=True)
                if len(jobs) >= _MAX_JOBS:
                    raise HTTPException(status_code=429, detail="The local job queue is full. Wait for a run to finish.")
            jobs[job.id] = job
            executor.submit(_run_job, job, config, before_path, model_assist,
                            source_path, script_path, sensitive)
        return {"id": job.id, "status": job.status}

    @app.post("/api/jobs")
    async def create_demo(request: Request):
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > 8192:
                    raise HTTPException(status_code=413, detail="Demo requests must be under 8 KB.")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid request size.")
        try:
            body = await request.json()
        except (ValueError, json.JSONDecodeError):
            raise HTTPException(status_code=400, detail="Expected a JSON job request.")
        if not isinstance(body, dict) or set(body) - {"example_id", "model_assist"}:
            raise HTTPException(status_code=400, detail="Invalid demo request.")
        example_id = body.get("example_id")
        if not isinstance(example_id, str):
            raise HTTPException(status_code=400, detail="Select a bundled example.")
        meta = _metadata(examples, example_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Bundled example not found.")
        model_assist = body.get("model_assist", False)
        if not isinstance(model_assist, bool):
            raise HTTPException(status_code=400, detail="model_assist must be a boolean.")
        folder = examples / example_id
        work = storage / uuid.uuid4().hex
        work.mkdir(mode=0o700)
        job = Job(uuid.uuid4().hex, demo=True, work_dir=work)
        config = RunConfig(input_path=folder / "data.csv", script_path=folder / "repro.py",
                           output_dir=work / "output", sensitive_columns=meta.get("sensitive_columns", []),
                           runner="local", max_runs=200, max_seconds=120)
        return add_job(job, config, folder / "data.csv", model_assist,
                       folder / "data.csv", folder / "repro.py", meta.get("sensitive_columns", []))

    @app.post("/api/jobs/upload")
    async def create_upload(request: Request):
        if not docker_ready():
            raise HTTPException(status_code=503, detail="Docker with python:3.12-slim is required for uploaded scripts.")
        length = request.headers.get("content-length")
        if length:
            try:
                if int(length) > _MAX_CSV_BYTES + _MAX_SCRIPT_BYTES + 64 * 1024:
                    raise HTTPException(status_code=413, detail="Upload exceeds the combined size limit.")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid upload size.")
        max_body_bytes = _MAX_CSV_BYTES + _MAX_SCRIPT_BYTES + 64 * 1024
        original_receive = request._receive
        received_bytes = 0

        async def bounded_receive():
            nonlocal received_bytes
            message = await original_receive()
            if message.get("type") == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > max_body_bytes:
                    raise HTTPException(status_code=413, detail="Upload exceeds the combined size limit.")
            return message

        request._receive = bounded_receive
        try:
            form = await request.form(max_files=2, max_fields=4, max_part_size=64 * 1024)
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=400, detail="Could not read the upload form.")
        csv_file, script_file = form.get("file"), form.get("script")
        if not hasattr(csv_file, "read") or not hasattr(script_file, "read"):
            raise HTTPException(status_code=400, detail="Upload a CSV file and a Python script.")
        csv_bytes = await csv_file.read(_MAX_CSV_BYTES + 1)
        script_bytes = await script_file.read(_MAX_SCRIPT_BYTES + 1)
        if len(csv_bytes) > _MAX_CSV_BYTES:
            raise HTTPException(status_code=413, detail="CSV files are limited to 5 MB.")
        if len(script_bytes) > _MAX_SCRIPT_BYTES:
            raise HTTPException(status_code=413, detail="Python scripts are limited to 100 KB.")
        try:
            text = csv_bytes.decode("utf-8-sig")
            reader = csv.reader(io.StringIO(text, newline=""), strict=True)
            header = next(reader, None)
            if not header:
                raise HTTPException(status_code=400, detail="CSV must contain a header and at most 100 columns.")
            if len(header) > _MAX_COLUMNS:
                raise HTTPException(status_code=413, detail="CSV is limited to 10,000 rows and 100 columns.")
            row_count = 0
            for row in reader:
                row_count += 1
                if row_count > _MAX_ROWS:
                    raise HTTPException(status_code=413, detail="CSV is limited to 10,000 rows and 100 columns.")
                if len(row) > _MAX_COLUMNS:
                    raise HTTPException(status_code=413, detail="CSV is limited to 10,000 rows and 100 columns.")
        except (UnicodeError, csv.Error):
            raise HTTPException(status_code=400, detail="CSV must be valid UTF-8 with valid CSV rows.")
        sensitive_raw = form.get("sensitive_columns", "")
        sensitive = [item.strip() for item in str(sensitive_raw).split(",") if item.strip()]
        if len(sensitive) > _MAX_COLUMNS or any(len(item) > 128 for item in sensitive):
            raise HTTPException(status_code=400, detail="Invalid sensitive column list.")
        assist_raw = str(form.get("model_assist", "false")).casefold()
        if assist_raw not in {"true", "false"}:
            raise HTTPException(status_code=400, detail="model_assist must be true or false.")
        model_assist = assist_raw == "true"
        work = storage / uuid.uuid4().hex
        work.mkdir(mode=0o700)
        source_path, script_path = work / "source.csv", work / "source.py"
        source_path.write_bytes(csv_bytes)
        script_path.write_bytes(script_bytes)
        job = Job(uuid.uuid4().hex, demo=False, work_dir=work)
        config = RunConfig(input_path=source_path, script_path=script_path, output_dir=work / "output",
                           sensitive_columns=sensitive, runner="docker", max_runs=200, max_seconds=120)
        try:
            return add_job(job, config, None, model_assist, source_path, script_path, sensitive)
        except Exception:
            shutil.rmtree(work, ignore_errors=True)
            raise

    @app.get("/api/jobs/{job_id}")
    async def get_job(job_id: str):
        if not _ID_RE.fullmatch(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        with job.lock:
            return {"id": job.id, "status": job.status, "events": list(job.events),
                    "report": job.report, "error": job.error, "preview": job.preview,
                    "download_available": bool(job.status == "complete" and job.report and
                                                job.report.get("status") == "verified" and job.archive)}

    @app.get("/api/jobs/{job_id}/download")
    async def download(job_id: str):
        if not _ID_RE.fullmatch(job_id):
            raise HTTPException(status_code=404, detail="Job not found.")
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found.")
        with job.lock:
            if job.status != "complete" or not job.report or job.report.get("status") != "verified" or not job.archive:
                raise HTTPException(status_code=409, detail="A verified, privacy-checked result is required for download.")
            bundle = job.archive
        return Response(bundle, media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="specimen-{job.id[:8]}.zip"',
                                 "X-Content-Type-Options": "nosniff"})

    web = Path(__file__).resolve().parent / "web"
    if web.is_dir():
        @app.get("/", include_in_schema=False)
        async def index():
            return FileResponse(web / "index.html")

        app.mount("/", StaticFiles(directory=web), name="web")
    return app


app = create_app()
