"""Shared pytest fixtures.

Single-user, local-only: no database, no accounts, no server-side state. API
tests drive Django views in-process via a thin async-compatible wrapper around
``django.test.Client``. Webin credentials are supplied per-request as headers
(see ``server/webin_creds.py``); the ``with_creds`` fixture injects them into the
test client. UI tests drive a real WSGI server with Playwright; the browser
holds credentials itself.
"""

from __future__ import annotations

import json as _json
import os
import sys
import threading
import time
from pathlib import Path
from wsgiref.simple_server import WSGIServer, make_server

import httpx
import pytest

_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "server"))
sys.path.insert(0, str(_REPO))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

import django  # noqa: E402

django.setup()

import ena_service  # noqa: E402
from django.test import Client as _DjangoClient  # noqa: E402

# Test creds, attached as headers by the with_creds fixture (the stateless
# backend only checks they're present; ena_service is mocked in tests).
_TEST_CREDS_HEADERS = {"X-Webin-Username": "Webin-test", "X-Webin-Password": "secret"}


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


@pytest.fixture
def with_creds(client):
    """Attach Webin credential headers to the test client for the request."""
    client._headers.update(_TEST_CREDS_HEADERS)
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

    original_list_records = ena_service.list_records
    original_validate_credentials = ena_service.validate_credentials

    def list_records(creds, entity, **kwargs):
        if entity == "studies":
            return [
                {"alias": "studyA", "accession": "ERP111", "title": "Study A", "status": "PRIVATE"},
                {"alias": "studyB", "accession": "ERP222", "title": "Study B", "status": "PRIVATE"},
            ]
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
                    # ENA's run-processing report: whether the read files are
                    # archived, which registering a run does not say.
                    "process_status": "COMPLETED",
                    "process_date": "2026-01-02",
                },
                {
                    "alias": "runB",
                    "accession": "ERR222",
                    "experiment_accession": "ERX222",
                    "study_accession": "ERP111",
                    "sample_accession": "ERS222",
                    "status": "PRIVATE",
                    "process_status": "IN_QUEUE",
                    "process_date": "2026-01-02",
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
