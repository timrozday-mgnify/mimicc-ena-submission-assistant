"""FastAPI server for the MIMICC ENA Submission Assistant.

A multi-user web app (single-user when ``DEPLOYMENT_MODE=local``):

  * accounts/sessions  -> ``auth`` + ``session_store`` over the Django ORM
    (Postgres); accounts are separate from ENA Webin credentials.
  * studies/samples    -> ena-api-client + the ena-submission-dataharmonizer
    submit scripts (via ``ena_service``), submitted server-side per user.
  * sample metadata    -> embedded DataHarmonizer (static bundle under /dh)
  * reads              -> uploaded DIRECT from the user's machine to ENA by the
    local ``read-helper`` (browser-bridged). The server only builds the manifest
    and upload plan (``read_assign`` + the resume ledger) and records the result.

Webin credentials live in server memory only (per user) and are never persisted.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import shutil
import tempfile
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import auth
import dh_builder_runner
import ena_service
import read_assign
import schema_service
import session_store
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

app = FastAPI(title="mimicc-ena-submission-assistant")

# CORS: the SPA is served same-origin, so this only matters for explicitly
# configured cross-origin callers. Credentials are allowed (cookie auth).
_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _ALLOWED_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )
else:
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=r"http://localhost(:\d+)?",
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["*"],
    )


@app.on_event("startup")
def _bootstrap_admin_on_startup() -> None:
    # Ensure the admin account exists (env-driven) when the app boots.
    try:
        auth.bootstrap_admin()
    except Exception:  # noqa: BLE001 — never block startup on a transient DB hiccup
        pass


# CSRF: cookie auth needs protection against cross-site form posts. In hosted
# mode, require a custom header on state-changing /api requests (the SPA's fetch
# helper sets it; cross-site <form> posts cannot). Login is exempt (no cookie
# yet) so the very first request can succeed.
_CSRF_EXEMPT = {"/api/auth/login"}


@app.middleware("http")
async def _csrf_guard(request: Request, call_next):
    if (
        not auth.is_local()
        and request.method in ("POST", "PUT", "DELETE")
        and request.url.path.startswith("/api/")
        and request.url.path not in _CSRF_EXEMPT
        and request.headers.get("x-requested-with") is None
    ):
        return Response(
            status_code=403, content='{"detail":"Missing X-Requested-With header"}', media_type="application/json"
        )
    return await call_next(request)


_executor = ThreadPoolExecutor(max_workers=4)

_STATIC = pathlib.Path(__file__).resolve().parent / "static"
_DH_DIR = _STATIC / "dh"
# Host path backing _DH_DIR (see docker-compose.yml) — used for the
# mimicc-dh-builder sibling -v mount so an on-demand rebuild writes straight
# into the directory this server already serves /dh from.
_HOST_DH_OUTPUT_DIR = os.environ.get("HOST_DH_OUTPUT_DIR", str(_DH_DIR))

# The local read-upload helper (browser-bridged) the SPA talks to for reads
# submission. Reads upload goes direct from the user's machine to ENA via this
# helper; the server never touches local read files. /api/health advertises the
# helper's expected loopback port to the browser.
_HELPER_PORT = int(os.environ.get("HELPER_PORT", "9100"))

# DH schema workspace (read-write mount) — editable LinkML schema used to
# rebuild the embedded DataHarmonizer bundle on demand.
_DH_SCHEMA_CONTAINER_DIR = pathlib.Path(os.environ.get("DH_SCHEMA_CONTAINER_DIR", "/dh-schema"))
_HOST_DH_SCHEMA_DIR = os.environ.get("HOST_DH_SCHEMA_DIR", str(_DH_SCHEMA_CONTAINER_DIR))

# dataharmonizer-template-builder sidecar (schema editor), embedded as an
# iframe on the Schema tab — see docker-compose.yml's "dhtb" service.
_DHTB_URL = os.environ.get("DHTB_URL", "http://localhost:8765")


# In-memory stores only (Webin credentials are never persisted to disk). Webin
# credentials are held per-user, keyed by User.id, and lost on restart/logout.
_jobs: dict[str, dict[str, Any]] = {}
_user_credentials: dict[int, tuple[str, str]] = {}


def _creds(user) -> ena_service.Credentials:
    pair = _user_credentials.get(user.id)
    if pair is None:
        raise HTTPException(status_code=401, detail="Credentials not set. Enter your Webin username and password.")
    return ena_service.Credentials(username=pair[0], password=pair[1])


def _skip_result(run: dict[str, Any], name: str, alias: str, accs: dict[str, Any], reason: str) -> dict[str, Any]:
    """A result row for a run skipped during resume (already submitted/in ENA)."""
    return {
        "name": name,
        "alias": alias,
        "sample": run.get("SAMPLE", ""),
        "study": run.get("STUDY", ""),
        "exit_code": 0,
        "success": True,
        "skipped": True,
        "reason": reason,
        "experiment_accession": accs.get("experiment_accession", ""),
        "run_accession": accs.get("run_accession", ""),
    }


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


class SuggestRequest(BaseModel):
    groups: list[dict[str, Any]]
    test: bool = True
    max_results: int = 5000


class ReadsPlanRequest(BaseModel):
    runs: list[dict[str, Any]]
    test: bool = True
    session_id: str | None = None
    force_reupload: bool = False


class ReadsResultRequest(BaseModel):
    session_id: str | None = None
    name: str
    alias: str | None = None
    stable_alias: str | None = None
    exit_code: int | None = None
    log: str = ""
    sample: str = ""
    study: str = ""
    experiment_accession: str | None = None
    run_accession: str | None = None


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class PasswordRequest(BaseModel):
    password: str


class SessionCreateRequest(BaseModel):
    name: str
    test_env: bool = True


class SessionStateRequest(BaseModel):
    state: dict[str, Any]
    test_env: bool | None = None


class DhBuildRequest(BaseModel):
    schema_yaml: str | None = None  # if provided, overwrites the schema before rebuilding


class SchemaSaveRequest(BaseModel):
    name: str
    yaml: str


class SchemaSelectRequest(BaseModel):
    role: str  # "sample" | "experiment"
    schema_id: str | None = None
    yaml: str | None = None


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
def health(request: Request) -> dict[str, Any]:
    user = None
    if auth.is_local():
        user = auth.get_admin_user()
    else:
        user = auth.resolve_user(request.cookies.get(auth.COOKIE_NAME))
    creds_set = user is not None and user.id in _user_credentials
    return {
        "status": "ok",
        "deployment_mode": auth.deployment_mode(),
        "authenticated": user is not None,
        "username": getattr(user, "username", None),
        "is_admin": bool(getattr(user, "is_superuser", False)),
        "credentials_configured": creds_set,
        "helper_port": _HELPER_PORT,
        "dh_available": any(_DH_DIR.iterdir()),
        "default_sample_filter": ena_service.DEFAULT_SAMPLE_FILTER,
        "dhtb_url": _DHTB_URL,
    }


# ---------------------------------------------------------------------------
# Authentication (login/logout/me) + account management (admin)
# ---------------------------------------------------------------------------


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, response: Response) -> dict[str, Any]:
    user = auth.authenticate(req.username, req.password)
    if user is None:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    token = auth.create_login(user)
    auth.set_login_cookie(response, token)
    return {"status": "ok", "user": auth.user_to_dict(user)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    auth.destroy_login(request.cookies.get(auth.COOKIE_NAME))
    auth.clear_login_cookie(response)
    return {"status": "ok"}


@app.get("/api/auth/me")
def auth_me(user=Depends(auth.current_user)) -> dict[str, Any]:
    return {"user": auth.user_to_dict(user), "deployment_mode": auth.deployment_mode()}


@app.get("/api/admin/users")
def admin_users_list(_admin=Depends(auth.require_admin)) -> list[dict[str, Any]]:
    return auth.list_users()


@app.post("/api/admin/users")
def admin_users_create(req: UserCreateRequest, _admin=Depends(auth.require_admin)) -> dict[str, Any]:
    try:
        return auth.create_user(req.username, req.password, is_admin=req.is_admin)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.delete("/api/admin/users/{user_id}")
def admin_users_delete(user_id: int, _admin=Depends(auth.require_admin)) -> dict[str, str]:
    try:
        auth.delete_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "deleted"}


@app.post("/api/admin/users/{user_id}/password")
def admin_users_set_password(user_id: int, req: PasswordRequest, _admin=Depends(auth.require_admin)) -> dict[str, str]:
    try:
        auth.set_password(user_id, req.password)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# Webin credentials (per-user, in server memory only — never persisted)
# ---------------------------------------------------------------------------


@app.post("/api/credentials")
async def set_credentials(req: CredentialsRequest, user=Depends(auth.current_user)) -> dict[str, str]:
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
    _user_credentials[user.id] = (username, req.password)
    return {"status": "ok", "username": username, "environment": env_name}


@app.delete("/api/credentials")
async def clear_credentials(user=Depends(auth.current_user)) -> dict[str, str]:
    _user_credentials.pop(user.id, None)
    return {"status": "cleared"}


# ---------------------------------------------------------------------------
# Submission sessions (named, persisted in the database, owned per user)
# ---------------------------------------------------------------------------


def _require_session(session_id: str, user) -> dict[str, Any]:
    session = session_store.get_session(session_id, owner=user)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return session


@app.get("/api/sessions")
def sessions_list(user=Depends(auth.current_user)) -> list[dict[str, Any]]:
    return session_store.list_sessions(user)


@app.post("/api/sessions")
def sessions_create(req: SessionCreateRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    try:
        return session_store.create_session(req.name, user, test_env=req.test_env)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/sessions/{session_id}")
def sessions_get(session_id: str, user=Depends(auth.current_user)) -> dict[str, Any]:
    session = _require_session(session_id, user)
    export, dh_saved_at = session_store.load_dh_export(session_id, "sample")
    exp_export, exp_dh_saved_at = session_store.load_dh_export(session_id, "experiment")
    return {
        "session": session,
        "state": session_store.load_state(session_id),
        "dh_export": export,
        "dh_saved_at": dh_saved_at,
        "exp_dh_export": exp_export,
        "exp_dh_saved_at": exp_dh_saved_at,
        "reads_log": session_store.read_reads_log(session_id),
        "reads_runs": session_store.list_reads_runs(session_id),
    }


@app.delete("/api/sessions/{session_id}")
def sessions_delete(session_id: str, user=Depends(auth.current_user)) -> dict[str, str]:
    _require_session(session_id, user)
    session_store.delete_session(session_id)
    return {"status": "deleted"}


@app.put("/api/sessions/{session_id}/state")
def sessions_save_state(session_id: str, req: SessionStateRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    _require_session(session_id, user)
    if req.test_env is not None:
        session_store.set_test_env(session_id, req.test_env)
    saved_at = session_store.save_state(session_id, req.state)
    return {"status": "ok", "saved_at": saved_at}


@app.post("/api/sessions/{session_id}/dh-export/{kind}")
def sessions_save_dh_export(
    session_id: str, kind: str, req: DhExportRequest, user=Depends(auth.current_user)
) -> dict[str, Any]:
    _require_session(session_id, user)
    try:
        saved_at = session_store.save_dh_export(session_id, req.export, kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "saved_at": saved_at}


@app.get("/api/sessions/{session_id}/dh-export/{kind}")
def sessions_get_dh_export(session_id: str, kind: str, user=Depends(auth.current_user)) -> dict[str, Any]:
    _require_session(session_id, user)
    try:
        export, saved_at = session_store.load_dh_export(session_id, kind)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"export": export, "saved_at": saved_at}


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


@app.post("/api/study/submit")
def study_submit(req: StudySubmitRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    creds = _creds(user)
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
def study_list(
    test: bool = True, status: str = "all", max_results: int = 5000, user=Depends(auth.current_user)
) -> list[dict[str, Any]]:
    return ena_service.list_records(_creds(user), "studies", test=test, status=status, max_results=max_results)


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


@app.post("/api/sample/prepare")
def sample_prepare(req: PrepareRequest) -> dict[str, Any]:
    try:
        prepared = ena_service.prepare_samples(req.export, where=req.where)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    records = ena_service.records_from_container(prepared)
    return {"prepared": prepared, "records": records, "count": len(records)}


@app.post("/api/sample/submit")
def sample_submit(req: SampleSubmitRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    creds = _creds(user)
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
def sample_list(
    test: bool = True, status: str = "all", max_results: int = 5000, user=Depends(auth.current_user)
) -> list[dict[str, Any]]:
    return ena_service.list_records(_creds(user), "samples", test=test, status=status, max_results=max_results)


# ---------------------------------------------------------------------------
# Records browser + lifecycle actions
# ---------------------------------------------------------------------------


@app.get("/api/records/{entity}")
def records_list(
    entity: str, test: bool = True, status: str = "all", max_results: int = 5000, user=Depends(auth.current_user)
) -> list[dict[str, Any]]:
    try:
        return ena_service.list_records(_creds(user), entity, test=test, status=status, max_results=max_results)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/records/action")
def records_action(req: ActionRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    creds = _creds(user)
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
# Reads (browser-bridged): suggest / plan / result
#
# Reads upload goes DIRECT from the user's machine to ENA via the local helper.
# The server never touches local read files. It only:
#   * suggest  — matches scanned read groups to ENA samples (server-side creds),
#   * plan     — decides which runs to upload vs. skip (ledger + ENA lookup) and
#                hands the browser the webin-cli manifest text for each upload,
#   * result   — records the outcome the browser relays back from the helper.
# ---------------------------------------------------------------------------


@app.post("/api/reads/suggest")
def reads_suggest(req: SuggestRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    samples = ena_service.list_records(_creds(user), "samples", test=req.test, max_results=req.max_results)
    return {"groups": read_assign.suggest(req.groups, samples), "samples": samples}


@app.post("/api/reads/plan")
def reads_plan(req: ReadsPlanRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    """Decide, per run, whether to upload or skip, and build manifest text for
    the runs to upload. Skips (already-in-ENA / cached) are recorded in the
    ledger here; the browser only uploads the runs marked ``action == "submit"``.
    """
    creds = _creds(user)
    if not req.runs:
        raise HTTPException(status_code=422, detail="No runs provided")

    session = _require_session(req.session_id, user) if req.session_id else None
    force = req.force_reupload

    def stable_alias(run_name: str) -> str | None:
        if session is None:
            return None  # one-off: manifest uses a timestamped alias, no ledger
        return session_store.session_run_alias(session["name"], run_name)

    # Pre-compute stable aliases + a single ENA lookup for the batch.
    stable_by_name: dict[str, str | None] = {}
    candidate_aliases: set[str] = set()
    for idx, run in enumerate(req.runs, start=1):
        name = run.get("NAME", f"run{idx}")
        stable = stable_alias(name)
        stable_by_name[name] = stable
        if stable and not (force or run.get("reupload", False)):
            candidate_aliases.add(stable)

    existing: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    if session is not None and not force and candidate_aliases:
        try:
            existing = ena_service.lookup_existing_runs(creds, candidate_aliases, test=req.test)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not check ENA for existing runs ({exc}); proceeding to submit.")

    plan: list[dict[str, Any]] = []
    for idx, run in enumerate(req.runs, start=1):
        name = run.get("NAME", f"run{idx}")
        stable = stable_by_name[name]
        run_forced = force or run.get("reupload", False)

        # Resume short-circuits (only with a session + stable alias).
        if session is not None and stable and not run_forced:
            ledger = session_store.get_reads_run(req.session_id, name)
            if (
                ledger
                and ledger["status"] in (session_store.STATUS_DONE, session_store.STATUS_ALREADY_IN_ENA)
                and (ledger.get("run_accession") or ledger.get("experiment_accession"))
            ):
                plan.append({**_skip_result(run, name, stable, ledger, "cached"), "action": "skip"})
                continue
            if stable in existing:
                accs = existing[stable]
                session_store.upsert_reads_run(
                    req.session_id,
                    name,
                    stable,
                    session_store.STATUS_ALREADY_IN_ENA,
                    experiment_accession=accs.get("experiment_accession") or None,
                    run_accession=accs.get("run_accession") or None,
                )
                plan.append({**_skip_result(run, name, stable, accs, "already_in_ena"), "action": "skip"})
                continue

        # Manifest alias: stable by default; fresh timestamped one on re-upload
        # (ENA aliases are permanent).
        manifest_alias: str | None = stable
        if stable and run_forced:
            manifest_alias = f"{stable}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            alias, manifest_text = read_assign.build_manifest_text(run, alias=manifest_alias)
        except ValueError as exc:
            plan.append(
                {
                    "name": name,
                    "action": "skip",
                    "success": False,
                    "skipped": True,
                    "reason": "invalid",
                    "messages": str(exc),
                }
            )
            continue

        plan.append(
            {
                "name": name,
                "action": "submit",
                "alias": alias,
                "stable_alias": stable,
                "manifest_filename": f"{alias}.manifest",
                "manifest_text": manifest_text,
                "sample": run.get("SAMPLE", ""),
                "study": run.get("STUDY", ""),
            }
        )

    return {"plan": plan, "warnings": warnings}


@app.post("/api/reads/result")
def reads_result(req: ReadsResultRequest, user=Depends(auth.current_user)) -> dict[str, Any]:
    """Record the outcome of a helper-run upload and update the ledger/log."""
    session = _require_session(req.session_id, user) if req.session_id else None

    accs = read_assign.parse_accessions(req.log.splitlines()) if req.log else {}
    if req.experiment_accession:
        accs["experiment_accession"] = req.experiment_accession
    if req.run_accession:
        accs["run_accession"] = req.run_accession

    result = {
        "name": req.name,
        "alias": req.alias,
        "sample": req.sample,
        "study": req.study,
        "exit_code": req.exit_code,
        "success": req.exit_code == 0,
        "skipped": False,
        **accs,
    }

    if session is not None:
        if req.log:
            session_store.append_reads_log(req.session_id, req.log.rstrip("\n"))
        stable = req.stable_alias or session_store.session_run_alias(session["name"], req.name)
        session_store.upsert_reads_run(
            req.session_id,
            req.name,
            stable,
            session_store.STATUS_DONE if req.exit_code == 0 else session_store.STATUS_FAILED,
            experiment_accession=accs.get("experiment_accession"),
            run_accession=accs.get("run_accession"),
            submitted_alias=req.alias,
        )
    return {"result": result}


# ---------------------------------------------------------------------------
# Schema library: list/save/delete, import/merge from ENA XML/XSD/YAML
# sources, and select a schema for the sample/experiment DataHarmonizer grids.
# ---------------------------------------------------------------------------


@app.get("/api/schemas")
def schemas_list() -> list[dict[str, Any]]:
    return schema_service.list_schemas()


@app.get("/api/schemas/ena-sources")
def schemas_ena_sources() -> dict[str, list[dict[str, str]]]:
    return schema_service.list_ena_sources()


@app.get("/api/schemas/{schema_id}")
def schemas_get(schema_id: str) -> dict[str, str]:
    try:
        return {"yaml": schema_service.read_schema(schema_id)}
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/schemas/{schema_id}/export")
def schemas_export(schema_id: str) -> Response:
    try:
        yaml_text = schema_service.read_schema(schema_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(
        content=yaml_text,
        media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{schema_id}.yaml"'},
    )


@app.post("/api/schemas")
def schemas_save(req: SchemaSaveRequest) -> dict[str, str]:
    try:
        schema_id = schema_service.save_schema(req.name, req.yaml)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"id": schema_id}


@app.delete("/api/schemas/{schema_id}")
def schemas_delete(schema_id: str) -> dict[str, str]:
    try:
        schema_service.delete_schema(schema_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"status": "deleted"}


@app.post("/api/schemas/import")
async def schemas_import(
    source_ids: list[str] = Form(default=[]),
    schema_ids: list[str] = Form(default=[]),
    name: str | None = Form(default=None),
    title: str | None = Form(default=None),
    include: list[str] | None = Form(default=None),
    exclude: list[str] | None = Form(default=None),
    files: list[UploadFile] = File(default=[]),
) -> dict[str, str]:
    tmpdir: pathlib.Path | None = None
    upload_paths: list[pathlib.Path] = []
    try:
        uploaded = [f for f in files if f.filename]
        if uploaded:
            tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mimicc-schema-import-"))
            for f in uploaded:
                dest = tmpdir / f.filename
                dest.write_bytes(await f.read())
                upload_paths.append(dest)
        try:
            yaml_text = schema_service.import_build(
                source_ids=source_ids or None,
                schema_ids=schema_ids or None,
                upload_paths=upload_paths or None,
                name=name,
                title=title,
                include=include,
                exclude=exclude,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"yaml": yaml_text}
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


@app.post("/api/schemas/import-file")
async def schemas_import_file(file: UploadFile = File(...)) -> dict[str, str]:
    suffix = pathlib.Path(file.filename or "").suffix or ".yaml"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        tmp_path.write_bytes(await file.read())
        try:
            yaml_text = schema_service.import_build(upload_paths=[tmp_path])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"yaml": yaml_text}
    finally:
        tmp_path.unlink(missing_ok=True)


@app.post("/api/schemas/select")
def schemas_select(req: SchemaSelectRequest) -> dict[str, str]:
    if req.schema_id is None and req.yaml is None:
        raise HTTPException(status_code=422, detail="Provide either schema_id or yaml")
    yaml_text = req.yaml
    if yaml_text is None:
        try:
            yaml_text = schema_service.read_schema(req.schema_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        template = schema_service.select_for_grid(req.role, yaml_text, dh_dir=_DH_DIR)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"template": template}


# ---------------------------------------------------------------------------
# DataHarmonizer bundle: on-demand rebuild (SSE)
# ---------------------------------------------------------------------------


@app.post("/api/dh/build")
def dh_build(req: DhBuildRequest, _admin=Depends(auth.require_admin)) -> dict[str, str]:
    # Rebuilding the DH bundle spawns a sibling container on the host Docker
    # daemon and writes the globally-served bundle, so it is restricted to admins.
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
