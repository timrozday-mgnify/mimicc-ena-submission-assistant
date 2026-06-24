"""Static/index/health views.

Mirrors the static mounts the previous FastAPI server's ``StaticFiles`` used to provide:
``/static``, ``/dh`` (with an SPA-style index fallback for directory
requests), and ``/templates`` — a deliberate duplicate of ``/dh/templates``
at the root path, because DataHarmonizer's own JS fetches template schemas
via an absolute root path that ignores the ``/dh`` mount prefix.
"""

from __future__ import annotations

import os
import pathlib

import auth
import credentials_store
import ena_service
from django.http import FileResponse, HttpRequest, JsonResponse
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.static import serve as static_serve

STATIC_DIR = pathlib.Path(__file__).resolve().parent / "static"
DH_DIR = STATIC_DIR / "dh"
DH_TEMPLATES_DIR = DH_DIR / "templates"

# Mounted unconditionally (even if empty) so a later on-demand rebuild starts
# populating it without needing a server restart.
DH_DIR.mkdir(parents=True, exist_ok=True)
DH_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)

_HELPER_PORT = int(os.environ.get("HELPER_PORT", "9100"))
_DHTB_URL = os.environ.get("DHTB_URL", "http://localhost:8765")


@ensure_csrf_cookie
def index(request: HttpRequest) -> FileResponse:
    return FileResponse((STATIC_DIR / "index.html").open("rb"))


def static_serve_view(request: HttpRequest, path: str, document_root: str) -> FileResponse:
    return static_serve(request, path, document_root=document_root)


def serve_dh(request: HttpRequest, path: str = "") -> FileResponse:
    candidate = DH_DIR / path if path else DH_DIR / "index.html"
    if not path or not candidate.is_file():
        candidate = DH_DIR / "index.html"
    return static_serve(request, str(candidate.relative_to(DH_DIR)), document_root=str(DH_DIR))


def health(request: HttpRequest) -> JsonResponse:
    if auth.is_local():
        user = auth.get_admin_user()
    else:
        user = request.user if request.user.is_authenticated else None
    creds_set = user is not None and credentials_store.has_creds(user.id)
    return JsonResponse(
        {
            "status": "ok",
            "deployment_mode": auth.deployment_mode(),
            "authenticated": user is not None,
            "username": getattr(user, "username", None),
            "is_admin": bool(getattr(user, "is_superuser", False)),
            "credentials_configured": creds_set,
            "helper_port": _HELPER_PORT,
            "dh_available": any(DH_DIR.iterdir()),
            "default_sample_filter": ena_service.DEFAULT_SAMPLE_FILTER,
            "dhtb_url": _DHTB_URL,
        }
    )
