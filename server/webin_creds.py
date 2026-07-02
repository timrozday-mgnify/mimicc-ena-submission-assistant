"""Webin (ENA) credentials, supplied per-request by the single-user client.

Single-user, local-only: there is no account system and nothing is persisted
server-side. The browser holds the Webin username/password in memory and sends
them on every request as ``X-Webin-Username`` / ``X-Webin-Password`` headers;
this turns them into the ``Credentials`` object the ENA service expects. Over
loopback for one local user this is equivalent to the old in-memory cache, with
no server-side state to manage.
"""

from __future__ import annotations

import ena_service
from django.http import HttpRequest, JsonResponse


def from_request(request: HttpRequest) -> tuple[ena_service.Credentials | None, JsonResponse | None]:
    username = (request.headers.get("X-Webin-Username") or "").strip()
    password = request.headers.get("X-Webin-Password") or ""
    if not username or not password:
        return None, JsonResponse(
            {"detail": "Credentials not set. Enter your Webin username and password."}, status=401
        )
    return ena_service.Credentials(username=username, password=password), None
