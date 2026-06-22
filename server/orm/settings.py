"""Minimal Django settings: just enough to drive the ORM + auth from FastAPI.

The database is Postgres in production (``DATABASE_URL``) and SQLite when no URL
is given (tests and lightweight local runs). Only the ORM, auth and
contenttypes apps are installed — there is no Django HTTP stack here.
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent

# Used only by Django's password hashers / signing; not security-critical for
# the ORM, but settable so deployments can pin it.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "mimicc-ena-insecure-dev-key")

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "orm",
]

# No middleware / templates / URLs: FastAPI owns the HTTP layer.
MIDDLEWARE: list[str] = []


def _databases() -> dict:
    url = os.environ.get("DATABASE_URL", "").strip()
    if url:
        parsed = urlparse(url)
        return {
            "default": {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": parsed.path.lstrip("/"),
                "USER": parsed.username or "",
                "PASSWORD": parsed.password or "",
                "HOST": parsed.hostname or "",
                "PORT": str(parsed.port) if parsed.port else "",
                "CONN_MAX_AGE": int(os.environ.get("DB_CONN_MAX_AGE", "60")),
            }
        }
    # Fallback: SQLite (tests + single-user local without Postgres).
    sqlite_path = os.environ.get("SQLITE_PATH", str(BASE_DIR.parent.parent / ".data" / "app.db"))
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": sqlite_path}}


DATABASES = _databases()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
