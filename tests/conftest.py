"""Shared pytest fixtures.

API tests drive Django views in-process via a thin async-compatible wrapper
around ``django.test.Client`` (no Docker, no network, no real event loop —
Django's test client dispatches synchronously, the ``async def``/``await``
shape in test files is kept only so the bulk of test bodies didn't need
rewriting). UI tests drive a real WSGI server with Playwright. Reads upload
now happens via a local helper, so it is exercised at the plan/result API
level rather than by running webin-cli.

The Django ORM is pointed at a throwaway SQLite database (configured before
any app module is imported); ``DEPLOYMENT_MODE=local`` makes every request
auto-authenticate as the admin user, matching the single-user local experience.
"""

from __future__ import annotations

import json as _json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

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
# Test bodies stay ``async def`` (see AsyncClient below) purely so they didn't
# need rewriting, but Django's test client/ORM underneath run synchronously
# inside that event loop; allow it (single throwaway SQLite DB, low concurrency).
os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

import dbsetup  # noqa: E402

dbsetup.migrate()

import auth as _auth  # noqa: E402

_auth.bootstrap_admin()

import credentials_store  # noqa: E402
import ena_service  # noqa: E402
from django.core.cache import cache as _cache  # noqa: E402
from django.test import Client as _DjangoClient  # noqa: E402


class AsyncClient:
    """Async-shaped wrapper around ``django.test.Client``.

    Lets test bodies keep ``await client.get(...)``-style calls (no real async
    I/O happens — Django's test client runs the WSGI stack synchronously).
    """

    def __init__(self, *, headers: dict | None = None, secure: bool = False):
        self._client = _DjangoClient()
        self._headers = headers or {}
        self._secure = secure

    def _merged_headers(self, headers: dict | None) -> dict:
        return {**self._headers, **(headers or {})}

    @staticmethod
    def _with_text(response):
        # django.http.HttpResponse has no .text — add it (httpx-style) so
        # callers don't need response.content.decode().
        response.text = response.content.decode()
        return response

    async def get(self, path: str, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.get(path, secure=self._secure, headers=self._merged_headers(headers), **kwargs)
        )

    async def delete(self, path: str, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.delete(path, secure=self._secure, headers=self._merged_headers(headers), **kwargs)
        )

    async def post(self, path: str, *, json=None, data=None, files=None, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.post(
                path,
                **self._body_kwargs(json, data, files),
                secure=self._secure,
                headers=self._merged_headers(headers),
            )
        )

    async def put(self, path: str, *, json=None, data=None, **kwargs):
        headers = kwargs.pop("headers", None)
        return self._with_text(
            self._client.put(
                path, **self._body_kwargs(json, data, None), secure=self._secure, headers=self._merged_headers(headers)
            )
        )

    @staticmethod
    def _body_kwargs(json, data, files) -> dict:
        if json is not None:
            return {"data": _json.dumps(json), "content_type": "application/json"}
        from django.core.files.uploadedfile import SimpleUploadedFile

        merged = dict(data or {})
        for key, (filename, content, content_type) in (files or {}).items():
            merged[key] = SimpleUploadedFile(filename, content, content_type=content_type)
        return {"data": merged or None}


@pytest.fixture
def client():
    return AsyncClient()


@pytest.fixture(autouse=True)
def clean_state():
    """Reset cache + per-user DB rows around every test."""
    from django.contrib.auth.models import User
    from django.contrib.sessions.models import Session
    from orm import models

    def _wipe():
        _cache.clear()
        models.ReadsRun.objects.all().delete()
        models.SubmissionSession.objects.all().delete()
        Session.objects.all().delete()
        User.objects.exclude(username=_auth.admin_username()).delete()

    _wipe()
    yield
    _wipe()


@pytest.fixture
def with_creds():
    """Configure Webin credentials for the (auto-logged-in) admin user."""
    admin = _auth.get_admin_user()
    credentials_store.set_creds(admin.id, "Webin-test", "secret")
    return ("Webin-test", "secret")


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
    import config.wsgi as wsgi_module

    admin = _auth.get_admin_user()
    credentials_store.set_creds(admin.id, "Webin-test", "secret")

    original_list_records = ena_service.list_records
    original_validate_credentials = ena_service.validate_credentials

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

    ena_service.list_records = list_records
    ena_service.validate_credentials = lambda *a, **k: None

    server = make_server("127.0.0.1", 9911, wsgi_module.application, server_class=WSGIServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
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
    server.shutdown()
    thread.join(timeout=5)
    ena_service.list_records = original_list_records
    ena_service.validate_credentials = original_validate_credentials
