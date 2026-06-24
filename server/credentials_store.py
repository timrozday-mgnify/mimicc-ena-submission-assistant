"""Per-user Webin (ENA) credentials, held only in a non-persistent cache.

Replaces the old in-process dict (the previous FastAPI server's ``_user_credentials``) with
Django's cache framework so it survives across multiple gunicorn workers.
Backend is env-driven (see ``config/settings.py``): Redis in hosted
deployments, in-process ``LocMemCache`` for local/dev/test. Either way,
credentials are never written to the database, matching the original
"never persisted to disk" intent. When Redis is used, the deployment must
disable its persistence (no RDB/AOF) — see docker-compose.yml's redis
service and the README security section.
"""

from __future__ import annotations

import ena_service
from django.conf import settings
from django.core.cache import cache
from django.http import JsonResponse

_TTL_SECONDS = settings.SESSION_COOKIE_AGE


def _key(user_id: int) -> str:
    return f"webin_creds:{user_id}"


def set_creds(user_id: int, username: str, password: str) -> None:
    cache.set(_key(user_id), (username, password), timeout=_TTL_SECONDS)


def clear_creds(user_id: int) -> None:
    cache.delete(_key(user_id))


def has_creds(user_id: int) -> bool:
    return cache.get(_key(user_id)) is not None


def get_creds(user) -> tuple[ena_service.Credentials | None, JsonResponse | None]:
    pair = cache.get(_key(user.id))
    if pair is None:
        return None, JsonResponse(
            {"detail": "Credentials not set. Enter your Webin username and password."}, status=401
        )
    return ena_service.Credentials(username=pair[0], password=pair[1]), None
