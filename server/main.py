"""FastAPI server for the MIMICC ENA Submission Assistant.

Ties together three existing tools behind one local web app:

  * studies/samples   -> ena-api-client + the ena-submission-dataharmonizer
    submit scripts (via ``ena_service``)
  * sample metadata    -> embedded DataHarmonizer (static bundle under /dh)
  * reads              -> enasequence/webin-cli in a Docker sibling container
    (via ``webin_runner`` + ``webin_cli_lib``), with read-to-sample assignment
    handled by ``read_assign``

Credentials live in server memory only and are never written to disk or logged.
Long-running reads submission uses the two-phase submit->SSE-stream pattern from
webin-cli-browser-assistant.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import dh_builder_runner
import ena_service
import read_assign
import webin_runner
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="mimicc-ena-submission-assistant")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://localhost(:\d+)?",
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=4)

_STATIC = pathlib.Path(__file__).resolve().parent / "static"
_DH_DIR = _STATIC / "dh"
# Host path backing _DH_DIR (see docker-compose.yml) — used for the
# mimicc-dh-builder sibling -v mount so an on-demand rebuild writes straight
# into the directory this server already serves /dh from.
_HOST_DH_OUTPUT_DIR = os.environ.get("HOST_DH_OUTPUT_DIR", str(_DH_DIR))

# Reads workspace (read-write mount) — manifests are written here next to FASTQs.
_READS_CONTAINER_DIR = pathlib.Path(os.environ.get("READS_CONTAINER_DIR", "/reads"))
_HOST_READS_DIR = os.environ.get("HOST_READS_DIR", str(_READS_CONTAINER_DIR))
_HOST_OUTPUT_DIR = os.environ.get("DEFAULT_OUTPUT_DIR", f"{_HOST_READS_DIR}/.webin-output")

# Read-write view of the whole host (see docker-compose.yml) — backs the
# directory browser, letting the user point reads scanning at any host
# directory instead of just the fixed reads workspace above.
_HOSTROOT = pathlib.Path(os.environ.get("HOSTROOT", "/hostroot"))
_HOST_HOME = os.environ.get("HOST_HOME") or str(pathlib.Path.home())
# None => use the default reads workspace (_READS_CONTAINER_DIR/_HOST_READS_DIR).
# Otherwise an absolute host path the user picked via the directory browser.
_active_reads_host_dir: str | None = None


def _current_reads_host_dir() -> str:
    return _active_reads_host_dir or _HOST_READS_DIR


def _current_reads_container_dir() -> pathlib.Path:
    if _active_reads_host_dir is None:
        return _READS_CONTAINER_DIR
    return _HOSTROOT / _active_reads_host_dir.lstrip("/")


def _current_output_host_dir() -> str:
    if _active_reads_host_dir is None:
        return _HOST_OUTPUT_DIR
    return f"{_active_reads_host_dir.rstrip('/')}/.webin-output"


# DH schema workspace (read-write mount) — editable LinkML schema used to
# rebuild the embedded DataHarmonizer bundle on demand.
_DH_SCHEMA_CONTAINER_DIR = pathlib.Path(os.environ.get("DH_SCHEMA_CONTAINER_DIR", "/dh-schema"))
_HOST_DH_SCHEMA_DIR = os.environ.get("HOST_DH_SCHEMA_DIR", str(_DH_SCHEMA_CONTAINER_DIR))

# DataHarmonizer export draft (read-write mount) — the sample data exported
# from the embedded DH grid via the Samples tab's "Export to Prepare" button
# or its periodic autosave, picked up by the Prepare step.
_DH_DRAFT_CONTAINER_DIR = pathlib.Path(os.environ.get("DH_DRAFT_CONTAINER_DIR", "/dh-draft"))
_DH_DRAFT_FILE = _DH_DRAFT_CONTAINER_DIR / "export.json"


def _iso_mtime(path: pathlib.Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


# In-memory stores only.
_jobs: dict[str, dict[str, Any]] = {}
_credentials: tuple[str, str] | None = None


def _creds() -> ena_service.Credentials:
    if _credentials is None:
        raise HTTPException(status_code=401, detail="Credentials not set. Enter your Webin username and password.")
    return ena_service.Credentials(username=_credentials[0], password=_credentials[1])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CredentialsRequest(BaseModel):
    username: str
    password: str
    test: bool = True


class StudySubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    hold_until: str | None = None
    public: bool = False


class DhExportRequest(BaseModel):
    export: dict[str, Any]


class PrepareRequest(BaseModel):
    export: dict[str, Any]
    where: str | None = ena_service.DEFAULT_SAMPLE_FILTER


class SampleSubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    checklist: str | None = "ERC000025"
    hold_until: str | None = None
    public: bool = False


class ScanRequest(BaseModel):
    subdir: str | None = None


class SetReadsDirRequest(BaseModel):
    path: str | None = None  # absolute host path; omit/null to reset to the default workspace


class SuggestRequest(BaseModel):
    groups: list[dict[str, Any]]
    test: bool = True
    max_results: int = 5000


class ReadsSubmitRequest(BaseModel):
    runs: list[dict[str, Any]]
    test: bool = True
    submit: bool = True


class DhBuildRequest(BaseModel):
    schema_yaml: str | None = None  # if provided, overwrites the schema before rebuilding


class ActionRequest(BaseModel):
    action: str
    accession: str
    test: bool = True
    alias: str | None = None
    hold_until: str | None = None


# ---------------------------------------------------------------------------
# Static / health / credentials
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(_STATIC)), name="static")
# Mounted unconditionally (even if empty) so a later on-demand rebuild
# (dh_builder_runner) starts populating it without needing a server restart.
_DH_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/dh", StaticFiles(directory=str(_DH_DIR), html=True), name="dh")
# DataHarmonizer's own JS fetches template schemas via a root-absolute path
# (lib/utils/templates.js: fetchSchema("/templates/<folder>/schema.json")),
# which doesn't respect the /dh mount prefix. Mount the same directory at
# root too so that fetch resolves correctly when DH is iframed under /dh/.
(_DH_DIR / "templates").mkdir(parents=True, exist_ok=True)
app.mount("/templates", StaticFiles(directory=str(_DH_DIR / "templates")), name="dh-templates")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(_STATIC / "index.html"))


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "credentials_configured": _credentials is not None,
        "dh_available": any(_DH_DIR.iterdir()),
        "reads_dir": str(_current_reads_container_dir()),
        "host_reads_dir": _current_reads_host_dir(),
        "default_host_reads_dir": _HOST_READS_DIR,
        "library_presets": read_assign.LIBRARY_PRESETS,
        "default_sample_filter": ena_service.DEFAULT_SAMPLE_FILTER,
    }


@app.post("/api/credentials")
async def set_credentials(req: CredentialsRequest) -> dict[str, str]:
    global _credentials
    username = req.username.strip()
    if not username or not req.password:
        raise HTTPException(status_code=422, detail="Username and password are required")
    creds = ena_service.Credentials(username=username, password=req.password)
    env_name = "test" if req.test else "production"
    try:
        ena_service.validate_credentials(creds, test=req.test)
    except PermissionError as exc:
        raise HTTPException(
            status_code=401, detail=f"Invalid Webin credentials for the ENA {env_name} service"
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Could not validate Webin credentials against the ENA {env_name} service: {exc}",
        ) from exc
    _credentials = (username, req.password)
    return {"status": "ok", "username": username, "environment": env_name}


@app.delete("/api/credentials")
async def clear_credentials() -> dict[str, str]:
    global _credentials
    _credentials = None
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


@app.post("/api/study/submit")
def study_submit(req: StudySubmitRequest) -> dict[str, Any]:
    creds = _creds()
    try:
        return ena_service.submit_studies(
            creds,
            req.records,
            test=req.test,
            modify=req.modify,
            hold_until=req.hold_until,
            public=req.public,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/study/list")
def study_list(test: bool = True, status: str = "all", max_results: int = 5000) -> list[dict[str, Any]]:
    return ena_service.list_records(_creds(), "studies", test=test, status=status, max_results=max_results)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


@app.post("/api/sample/dh-export")
def save_dh_export(req: DhExportRequest) -> dict[str, Any]:
    """Persist the DataHarmonizer export draft (manual export or autosave)."""
    _DH_DRAFT_CONTAINER_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _DH_DRAFT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(req.export))
    tmp.replace(_DH_DRAFT_FILE)  # atomic on POSIX
    return {"status": "ok", "saved_at": _iso_mtime(_DH_DRAFT_FILE)}


@app.get("/api/sample/dh-export")
def get_dh_export() -> dict[str, Any]:
    """Return the most recently saved DataHarmonizer export draft, if any."""
    if not _DH_DRAFT_FILE.is_file():
        return {"export": None, "saved_at": None}
    return {"export": json.loads(_DH_DRAFT_FILE.read_text()), "saved_at": _iso_mtime(_DH_DRAFT_FILE)}


@app.post("/api/sample/prepare")
def sample_prepare(req: PrepareRequest) -> dict[str, Any]:
    try:
        prepared = ena_service.prepare_samples(req.export, where=req.where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    records = ena_service.records_from_container(prepared)
    return {"prepared": prepared, "records": records, "count": len(records)}


@app.post("/api/sample/submit")
def sample_submit(req: SampleSubmitRequest) -> dict[str, Any]:
    creds = _creds()
    try:
        return ena_service.submit_samples(
            creds,
            req.records,
            test=req.test,
            modify=req.modify,
            checklist=req.checklist,
            hold_until=req.hold_until,
            public=req.public,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sample/list")
def sample_list(test: bool = True, status: str = "all", max_results: int = 5000) -> list[dict[str, Any]]:
    return ena_service.list_records(_creds(), "samples", test=test, status=status, max_results=max_results)


# ---------------------------------------------------------------------------
# Records browser + lifecycle actions
# ---------------------------------------------------------------------------


@app.get("/api/records/{entity}")
def records_list(entity: str, test: bool = True, status: str = "all", max_results: int = 5000) -> list[dict[str, Any]]:
    try:
        return ena_service.list_records(_creds(), entity, test=test, status=status, max_results=max_results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/records/action")
def records_action(req: ActionRequest) -> dict[str, Any]:
    creds = _creds()
    try:
        return ena_service.run_action(
            creds,
            req.action,
            req.accession,
            test=req.test,
            alias=req.alias,
            hold_until=req.hold_until,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Reads: scan / suggest / submit (SSE)
# ---------------------------------------------------------------------------


def _reads_workdir(subdir: str | None) -> pathlib.Path:
    base = _current_reads_container_dir()
    if subdir:
        candidate = (base / subdir).resolve()
        if base.resolve() not in candidate.parents and candidate != base.resolve():
            raise HTTPException(status_code=400, detail="subdir must be inside the active reads directory")
        return candidate
    return base


def _host_path_to_local(host_path: str) -> pathlib.Path:
    """Resolve an absolute host path to its view under the /hostroot mount."""
    p = pathlib.PurePosixPath(host_path)
    if not p.is_absolute():
        raise HTTPException(status_code=400, detail=f"Path must be absolute: {host_path}")
    return _HOSTROOT / str(p).lstrip("/")


@app.get("/api/reads/browse")
def reads_browse(path: str | None = None) -> dict[str, Any]:
    host_path = path or _HOST_HOME
    local = _host_path_to_local(host_path)
    if not local.is_dir():
        raise HTTPException(status_code=404, detail=f"Not a directory: {host_path}")
    dirs: list[str] = []
    try:
        entries = sorted(local.iterdir(), key=lambda p: p.name.lower())
    except (PermissionError, OSError):
        entries = []
    for entry in entries:
        if entry.name.startswith("."):
            continue
        try:
            if entry.is_dir():
                dirs.append(entry.name)
        except OSError:
            continue  # unreadable or special file (socket, device, ...)
    norm = host_path.rstrip("/") or "/"
    parent = None if norm == "/" else (str(pathlib.PurePosixPath(norm).parent))
    return {"path": norm, "parent": parent, "dirs": dirs}


@app.post("/api/reads/set-dir")
def reads_set_dir(req: SetReadsDirRequest) -> dict[str, Any]:
    global _active_reads_host_dir
    if not req.path:
        _active_reads_host_dir = None
    else:
        if not _host_path_to_local(req.path).is_dir():
            raise HTTPException(status_code=400, detail=f"Not a directory: {req.path}")
        _active_reads_host_dir = req.path.rstrip("/") or "/"
    return {
        "reads_dir": str(_current_reads_container_dir()),
        "host_reads_dir": _current_reads_host_dir(),
        "default_host_reads_dir": _HOST_READS_DIR,
    }


@app.post("/api/reads/scan")
def reads_scan(req: ScanRequest) -> dict[str, Any]:
    workdir = _reads_workdir(req.subdir)
    groups = read_assign.scan_reads(workdir)
    return {
        "reads_dir": str(workdir),
        "host_reads_dir": _current_reads_host_dir(),
        "groups": groups,
        "count": len(groups),
    }


@app.post("/api/reads/suggest")
def reads_suggest(req: SuggestRequest) -> dict[str, Any]:
    samples = ena_service.list_records(_creds(), "samples", test=req.test, max_results=req.max_results)
    return {"groups": read_assign.suggest(req.groups, samples), "samples": samples}


@app.post("/api/reads/submit")
def reads_submit(req: ReadsSubmitRequest) -> dict[str, str]:
    _creds()  # fail fast
    if not req.runs:
        raise HTTPException(status_code=422, detail="No runs provided")
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"config": req.model_dump(), "status": "pending", "results": []}
    return {"job_id": job_id}


@app.get("/api/reads/stream/{job_id}")
async def reads_stream(job_id: str) -> EventSourceResponse:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]

    async def event_generator():
        if job["status"] != "pending":
            yield {"data": json.dumps({"error": "Job already started or completed"})}
            return
        job["status"] = "running"
        cfg = job["config"]

        try:
            creds = _creds()
        except HTTPException as exc:
            job["status"] = "failed"
            yield {"data": json.dumps({"error": exc.detail})}
            return

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        reads_container_dir = _current_reads_container_dir()
        reads_host_dir = _current_reads_host_dir()
        output_host_dir = _current_output_host_dir()

        # Ensure the webin-cli output dir exists (the active reads dir is
        # read-write, whether it's the default workspace or a browsed-to host
        # directory via the now read-write /hostroot mount).
        try:
            (reads_container_dir / ".webin-output").mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

        def _produce() -> None:
            results: list[dict[str, Any]] = []
            for idx, run in enumerate(cfg["runs"], start=1):
                name = run.get("NAME", f"run{idx}")
                loop.call_soon_threadsafe(queue.put_nowait, ("line", f"=== [{idx}/{len(cfg['runs'])}] {name} ==="))
                try:
                    alias, manifest_path = read_assign.build_manifest(run, reads_container_dir)
                except ValueError as exc:
                    loop.call_soon_threadsafe(queue.put_nowait, ("line", f"SKIP: {exc}"))
                    results.append({"name": name, "success": False, "messages": str(exc)})
                    continue

                manifest_host_path = f"{reads_host_dir.rstrip('/')}/{manifest_path.name}"
                lines: list[str] = []
                gen = webin_runner.iter_webin_cli_logs(
                    context="reads",
                    manifest_host_path=manifest_host_path,
                    input_host_dir=reads_host_dir,
                    output_host_dir=output_host_dir,
                    username=creds.username,
                    password=creds.password,
                    submit=cfg["submit"],
                    test=cfg["test"],
                )
                exit_code: int | None = None
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
                result = {
                    "name": name,
                    "alias": alias,
                    "sample": run.get("SAMPLE", ""),
                    "study": run.get("STUDY", ""),
                    "exit_code": exit_code,
                    "success": exit_code == 0,
                    **accs,
                }
                results.append(result)
                loop.call_soon_threadsafe(queue.put_nowait, ("result", result))

            loop.call_soon_threadsafe(queue.put_nowait, ("__DONE__", results))

        loop.run_in_executor(_executor, _produce)

        while True:
            kind, payload = await queue.get()
            ts = datetime.now(UTC).isoformat()
            if kind == "__DONE__":
                job["status"] = "done"
                job["results"] = payload
                yield {"data": json.dumps({"done": True, "results": payload, "ts": ts})}
                break
            if kind == "result":
                yield {"data": json.dumps({"result": payload, "ts": ts})}
            else:
                yield {"data": json.dumps({"line": payload, "ts": ts})}

    return EventSourceResponse(event_generator())


@app.get("/api/reads/status/{job_id}")
async def reads_status(job_id: str) -> dict[str, Any]:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]
    return {"status": job["status"], "results": job.get("results", [])}


# ---------------------------------------------------------------------------
# DataHarmonizer bundle: on-demand rebuild (SSE)
# ---------------------------------------------------------------------------


@app.post("/api/dh/build")
def dh_build(req: DhBuildRequest) -> dict[str, str]:
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"config": req.model_dump(), "status": "pending", "results": []}
    return {"job_id": job_id}


@app.get("/api/dh/build/stream/{job_id}")
async def dh_build_stream(job_id: str) -> EventSourceResponse:
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    job = _jobs[job_id]

    async def event_generator():
        if job["status"] != "pending":
            yield {"data": json.dumps({"error": "Job already started or completed"})}
            return
        job["status"] = "running"
        cfg = job["config"]

        if cfg.get("schema_yaml"):
            _DH_SCHEMA_CONTAINER_DIR.mkdir(parents=True, exist_ok=True)
            (_DH_SCHEMA_CONTAINER_DIR / "mimicc.yaml").write_text(cfg["schema_yaml"])

        loop = asyncio.get_event_loop()
        queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()

        def _produce() -> None:
            gen = dh_builder_runner.iter_dh_builder_logs(
                schema_host_dir=_HOST_DH_SCHEMA_DIR,
                output_host_dir=_HOST_DH_OUTPUT_DIR,
            )
            exit_code: int | None = None
            try:
                while True:
                    line = next(gen)
                    loop.call_soon_threadsafe(queue.put_nowait, ("line", line))
            except StopIteration as si:
                exit_code = si.value
            except Exception as exc:  # noqa: BLE001
                loop.call_soon_threadsafe(queue.put_nowait, ("line", f"ERROR: {exc}"))
                exit_code = 1
            result = {"success": exit_code == 0, "exit_code": exit_code}
            loop.call_soon_threadsafe(queue.put_nowait, ("__DONE__", result))

        loop.run_in_executor(_executor, _produce)

        while True:
            kind, payload = await queue.get()
            ts = datetime.now(UTC).isoformat()
            if kind == "__DONE__":
                job["status"] = "done"
                job["results"] = payload
                yield {"data": json.dumps({"done": True, "result": payload, "ts": ts})}
                break
            yield {"data": json.dumps({"line": payload, "ts": ts})}

    return EventSourceResponse(event_generator())


@app.post("/api/shutdown")
async def shutdown() -> dict[str, str]:
    async def _stop() -> None:
        await asyncio.sleep(0.5)
        subprocess.Popen(["docker", "stop", "mimicc-ena-submission-assistant"])

    asyncio.create_task(_stop())
    return {"status": "stopping"}
