"""Shared pytest fixtures.

API tests drive the FastAPI app in-process via httpx ASGITransport (no Docker,
no network). UI tests drive a real uvicorn server with Playwright, with the
webin-cli runner patched so no Docker is needed.
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO))

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
    """Reset in-memory jobs and credentials around every test."""
    saved = _main._credentials
    _main._jobs.clear()
    _main._credentials = None
    yield
    _main._jobs.clear()
    _main._credentials = saved


@pytest.fixture
def with_creds():
    _main._credentials = ("Webin-test", "secret")
    return _main._credentials


# ---------------------------------------------------------------------------
# Mock webin-cli runner generators (shared with UI tests)
# ---------------------------------------------------------------------------


def mock_logs_success(**kwargs):
    yield "INFO: validating manifest"
    yield "INFO: The submission has been completed successfully."
    yield "INFO: experiment ERX9000001 run ERR9000001"
    return 0


def mock_logs_failure(**kwargs):
    yield "ERROR: invalid sample accession"
    return 1


# ---------------------------------------------------------------------------
# Live server for Playwright UI tests
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def live_server_url():
    import uvicorn

    _main._credentials = ("Webin-test", "secret")
    _main.webin_runner.iter_webin_cli_logs = lambda **kw: mock_logs_success(**kw)
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
