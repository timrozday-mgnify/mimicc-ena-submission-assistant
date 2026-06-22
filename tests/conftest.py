"""Shared pytest fixtures.

API tests drive the FastAPI app in-process via httpx ASGITransport (no Docker,
no network). UI tests drive a real uvicorn server with Playwright. Reads upload
now happens via a local helper, so it is exercised at the plan/result API level
rather than by running webin-cli.

The Django ORM is pointed at a throwaway SQLite database (configured before any
app module is imported); ``DEPLOYMENT_MODE=local`` makes every request
auto-authenticate as the admin user, matching the single-user local experience.
"""

from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO))

# Configure the ORM to use a throwaway SQLite DB BEFORE importing app modules.
_DB_FD, _DB_PATH = tempfile.mkstemp(suffix=".sqlite3", prefix="mimicc-test-")
os.close(_DB_FD)
os.environ["SQLITE_PATH"] = _DB_PATH
os.environ.setdefault("DEPLOYMENT_MODE", "local")
# Tests call the sync ORM directly from async test bodies (low concurrency,
# single throwaway SQLite DB); allow it. App code keeps ORM off the event loop.
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import dbsetup  # noqa: E402

dbsetup.migrate()

import auth as _auth  # noqa: E402

_auth.bootstrap_admin()

import main as _main  # noqa: E402


@pytest.fixture
def app():
    return _main.app


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def clean_state():
    """Reset in-memory state and per-user DB rows around every test."""
    from django.contrib.auth.models import User
    from orm import models

    def _wipe():
        _main._jobs.clear()
        _main._user_credentials.clear()
        models.ReadsRun.objects.all().delete()
        models.SubmissionSession.objects.all().delete()
        models.LoginSession.objects.all().delete()
        User.objects.exclude(username=_auth.admin_username()).delete()

    _wipe()
    yield
    _wipe()


@pytest.fixture
def with_creds():
    """Configure Webin credentials for the (auto-logged-in) admin user."""
    admin = _auth.get_admin_user()
    _main._user_credentials[admin.id] = ("Webin-test", "secret")
    return _main._user_credentials[admin.id]


# ---------------------------------------------------------------------------
# Mock helpers (shared)
# ---------------------------------------------------------------------------

# A webin-cli log as the local helper would stream it back to the browser; the
# browser relays the final log to /api/reads/result, which parses accessions.
MOCK_READS_LOG = (
    "INFO: validating manifest\n"
    "INFO: The submission has been completed successfully.\n"
    "INFO: experiment ERX9000001 run ERR9000001\n"
)


# ---------------------------------------------------------------------------
# Live server for Playwright UI tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server_url():
    import uvicorn

    admin = _auth.get_admin_user()
    _main._user_credentials[admin.id] = ("Webin-test", "secret")

    original_list_records = _main.ena_service.list_records
    original_validate_credentials = _main.ena_service.validate_credentials

    def list_records(creds, entity, **kwargs):
        if entity == "samples":
            return [
                {"alias": "MIMICC_A_1", "accession": "ERS111", "title": "Sample A1", "status": "PRIVATE"},
                {"alias": "MIMICC_B_2", "accession": "ERS222", "title": "Sample B2", "status": "PRIVATE"},
            ]
        if entity == "runs":
            return [
                {
                    "alias": "runA",
                    "accession": "ERR111",
                    "experiment_accession": "ERX111",
                    "study_accession": "ERP111",
                    "sample_accession": "ERS111",
                    "status": "PRIVATE",
                },
            ]
        if entity == "experiments":
            return [
                {
                    "alias": "expA",
                    "accession": "ERX111",
                    "title": "Experiment A",
                    "study_accession": "ERP111",
                    "sample_accession": "ERS111",
                    "status": "PRIVATE",
                },
            ]
        return original_list_records(creds, entity, **kwargs)

    _main.ena_service.list_records = list_records
    _main.ena_service.validate_credentials = lambda *a, **k: None

    config = uvicorn.Config(_main.app, host="127.0.0.1", port=9911, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = "http://127.0.0.1:9911"
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                break
        except Exception:
            time.sleep(0.1)
    else:
        raise RuntimeError("live server did not start")

    yield url
    server.should_exit = True
    thread.join(timeout=5)
    _main.ena_service.list_records = original_list_records
    _main.ena_service.validate_credentials = original_validate_credentials
