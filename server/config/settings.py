"""Django settings: ORM + auth + the HTTP layer (views_*.py, urls.py).

The database is Postgres in production (``DATABASE_URL``) and SQLite when no
URL is given (tests and lightweight local runs).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

BASE_DIR = Path(__file__).resolve().parent.parent

# Used for Django's password hashers / signing and CSRF token signing.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "mimicc-ena-insecure-dev-key")

DEBUG = (os.environ.get("DJANGO_DEBUG", "") or "").strip().lower() in ("1", "true", "yes")

_allowed_hosts = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "").split(",") if h.strip()]
ALLOWED_HOSTS = _allowed_hosts or ["*"]

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "corsheaders",
    "orm",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "middleware.LocalModeCsrfBypassMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

# CSRF: the SPA reads this cookie in JS and echoes it back as a header (see
# server/static/app.js's api() wrapper), so it must not be HttpOnly.
CSRF_COOKIE_HTTPONLY = False
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_HEADER_NAME = "HTTP_X_CSRFTOKEN"
CSRF_COOKIE_SECURE = (os.environ.get("DEPLOYMENT_MODE", "local") or "local").strip().lower() == "hosted"
_csrf_trusted = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _csrf_trusted:
    CSRF_TRUSTED_ORIGINS = _csrf_trusted

# Sessions: cookie name/age preserve the previous custom LoginSession model's
# behaviour (same cookie name, same 7-day TTL) so existing logins aren't
# invalidated by this migration.
SESSION_COOKIE_NAME = "mimicc_sid"
SESSION_COOKIE_AGE = 7 * 24 * 60 * 60
SESSION_COOKIE_SECURE = CSRF_COOKIE_SECURE
SESSION_SAVE_EVERY_REQUEST = True

# CORS: the SPA is served same-origin, so this only matters for explicitly
# configured cross-origin callers. Credentials are allowed (cookie auth).
CORS_ALLOW_CREDENTIALS = True
_allowed_origins = [o.strip() for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o.strip()]
if _allowed_origins:
    CORS_ALLOWED_ORIGINS = _allowed_origins
else:
    CORS_ALLOWED_ORIGIN_REGEXES = [r"^http://localhost(:\d+)?$"]


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
    sqlite_path = os.environ.get("SQLITE_PATH", str(BASE_DIR.parent / ".data" / "app.db"))
    Path(sqlite_path).parent.mkdir(parents=True, exist_ok=True)
    return {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": sqlite_path}}


DATABASES = _databases()


def _caches() -> dict:
    redis_url = os.environ.get("REDIS_URL", "").strip()
    if redis_url:
        return {"default": {"BACKEND": "django.core.cache.backends.redis.RedisCache", "LOCATION": redis_url}}
    # Local/dev/test: no Redis container needed — credentials live only in this
    # process's memory, matching the original never-persisted-to-disk intent.
    return {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


CACHES = _caches()

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
