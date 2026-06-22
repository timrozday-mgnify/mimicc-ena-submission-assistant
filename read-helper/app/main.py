"""MIMICC reads upload helper — a tiny local app that runs webin-cli.

The hosted MIMICC ENA Submission Assistant keeps all session/sample/study state
on the server, but **reads upload goes direct from the user's machine to ENA**.
This helper is what does that upload: the browser (logged into the hosted app)
fetches the webin-cli manifest from the server and hands it to this helper on
``localhost``; the helper runs ``enasequence/webin-cli`` against the user's local
read files and streams the log back. The helper never talks to the hosted server
directly — it is a dumb, local executor.

Detection: the browser polls ``GET /api/health``. Control: ``POST /api/submit``
then SSE ``GET /api/stream/{job_id}`` (the stream launches webin-cli). This is
the same two-phase pattern as webin-cli-browser-assistant; the key difference is
that CORS allows the configured hosted origin so the remote page can reach the
loopback helper (the port is still bound to 127.0.0.1, so it is not remotely
reachable).
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import read_assign
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from webin_cli_lib import iter_webin_cli_logs

VERSION = "1.0.0"

app = FastAPI(title="mimicc-read-helper")

# CORS: allow the configured hosted app origin(s) plus any localhost origin so a
# page served from the hosted domain can drive this loopback helper.
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_origin_regex=r"https?://localhost(:\d+)?",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=2)
_jobs: dict[str, dict[str, Any]] = {}
_credentials: tuple[str, str] | None = None

# Read-only view of the host filesystem so the helper can read/scan the local
# read directory and write the manifest beside the reads (mounted read-write).
_HOSTROOT = pathlib.Path(os.environ.get("HOSTROOT", "/hostroot"))
_HOST_HOME = os.environ.get("HOST_HOME") or str(pathlib.Path.home())
_STATIC = pathlib.Path(__file__).resolve().parent / "static"


def _host_to_local(host_path: str) -> pathlib.Path:
    p = pathlib.PurePosixPath(host_path)
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail=f"Path must be absolute: {host_path}")
    return _HOSTROOT / str(p).lstrip("/")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CredentialsRequest(BaseModel):
    username: str
    password: str


class ScanRequest(BaseModel):
    host_dir: str


class SubmitRequest(BaseModel):
    input_host_dir: str
    manifest_filename: str
    manifest_text: str
    submit: bool = True
    test: bool = True


# ---------------------------------------------------------------------------
# Static UI + health + credentials
# ---------------------------------------------------------------------------

if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")


@app.get("/")
def index() -> Any:
    idx = _STATIC / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    return {"status": "ok", "service": "mimicc-read-helper", "version": VERSION}


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "service": "mimicc-read-helper",
        "version": VERSION,
        "host_home": _HOST_HOME,
        "credentials_configured": _credentials is not None,
    }


@app.post("/api/credentials")
def set_credentials(req: CredentialsRequest) -> dict[str, str]:
    global _credentials
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=422, detail="Username and password are required")
    _credentials = (username, req.password)
    return {"status": "ok", "username": username}


@app.delete("/api/credentials")
def clear_credentials() -> dict[str, str]:
    global _credentials
    _credentials = None
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Scan local read files
# ---------------------------------------------------------------------------


@app.post("/api/scan")
def scan(req: ScanRequest) -> dict[str, Any]:
    local = _host_to_local(req.host_dir)
    if not local.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {req.host_dir}")
    groups = read_assign.scan_reads(local)
    return {"host_dir": req.host_dir.rstrip("/"), "groups": groups, "count": len(groups)}


# ---------------------------------------------------------------------------
# Upload: two-phase submit -> SSE stream (the stream launches webin-cli)
# ---------------------------------------------------------------------------


@app.post("/api/submit")
def submit(req: SubmitRequest) -> dict[str, str]:
    if _credentials is None:
        raise HTTPException(status_code=401, detail="Webin credentials not set in the helper")
    local_dir = _host_to_local(req.input_host_dir)
    if not local_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {req.input_host_dir}")
    # Write the server-built manifest next to the reads (read-write hostroot).
    manifest_name = pathlib.PurePosixPath(req.manifest_filename).name
    try:
        (local_dir / manifest_name).write_text(req.manifest_text)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Could not write manifest: {exc}") from exc

    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"config": req.model_dump(), "manifest_name": manifest_name, "status": "pending"}
    return {"job_id": job_id}


@app.get("/api/stream/{job_id}")
async def stream(job_id: str) -> EventSourceResponse:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]

    async def event_generator():
        if job["status"] != "pending":
            yield {"data": json.dumps({"error": "Job already started or completed"})}
            return
        job["status"] = "running"
        cfg = job["config"]
        creds = _credentials
        if creds is None:
            job["status"] = "failed"
            yield {"data": json.dumps({"error": "Webin credentials not set"})}
            return

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        input_host_dir = cfg["input_host_dir"].rstrip("/")
        output_host_dir = f"{input_host_dir}/.webin-output"
        manifest_host_path = f"{input_host_dir}/{job['manifest_name']}"
        try:
            os.makedirs(_host_to_local(output_host_dir), exist_ok=True)
        except OSError:
            pass

        def _produce() -> None:
            lines: list[str] = []
            exit_code: int | None = None
            gen = iter_webin_cli_logs(
                context="reads",
                manifest_path=manifest_host_path,
                input_dir=input_host_dir,
                output_dir=output_host_dir,
                username=creds[0],
                password=creds[1],
                submit=cfg["submit"],
                test=cfg["test"],
            )
            try:
                while True:
                    line = next(gen)
                    lines.append(line)
                    loop.call_soon_threadsafe(queue.put_nowait, ("line", line))
            except StopIteration as si:
                exit_code = si.value
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("line", f"ERROR: {exc}"))
                exit_code = 1
            accs = read_assign.parse_accessions(lines)
            loop.call_soon_threadsafe(
                queue.put_nowait,
                ("__DONE__", {"exit_code": exit_code, "log": "\n".join(lines), **accs}),
            )

        loop.run_in_executor(_executor, _produce)

        while True:
            kind, payload = await queue.get()
            ts = datetime.now(UTC).isoformat()
            if kind == "__DONE__":
                job["status"] = "done"
                job["result"] = payload
                yield {"data": json.dumps({"done": True, **payload, "ts": ts})}
                break
            yield {"data": json.dumps({"line": payload, "ts": ts})}

    return EventSourceResponse(event_generator())


@app.get("/api/status/{job_id}")
def status(job_id: str) -> dict[str, Any]:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]
    return {"status": job["status"], "result": job.get("result")}
