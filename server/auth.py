"""Username/password accounts + web session management (Django auth + ORM).

Accounts are completely separate from ENA Webin credentials. Django's built-in
``auth.User`` provides password hashing and the ``is_superuser`` admin flag; an
``admin`` superuser is bootstrapped from ``ADMIN_USERNAME`` / ``ADMIN_PASSWORD``
env vars. Web logins are DB-backed (``LoginSession``), addressed by an opaque
cookie token.

Deployment mode (``DEPLOYMENT_MODE``):
  * ``local`` (default) — single user; every request auto-authenticates as the
    admin user, so the local single-user experience needs no login screen.
  * ``hosted`` — the login screen and cookie auth are enforced.
"""

from __future__ import annotations

import os
from typing import Any

import dbsetup

dbsetup.ensure()

from django.contrib.auth.models import User  # noqa: E402
from django.utils import timezone  # noqa: E402
from fastapi import HTTPException, Request, Response  # noqa: E402
from orm import models  # noqa: E402

COOKIE_NAME = "mimicc_sid"


# ---------------------------------------------------------------------------
# Deployment mode
# ---------------------------------------------------------------------------


def deployment_mode() -> str:
    return (os.environ.get("DEPLOYMENT_MODE", "local") or "local").strip().lower()


def is_local() -> bool:
    return deployment_mode() != "hosted"


def admin_username() -> str:
    return (os.environ.get("ADMIN_USERNAME", "admin") or "admin").strip()


# ---------------------------------------------------------------------------
# Admin bootstrap
# ---------------------------------------------------------------------------


def bootstrap_admin() -> User:
    """Create or update the admin superuser from env. Env is authoritative for
    the admin password, so it is (re)applied whenever it differs."""
    username = admin_username()
    password = os.environ.get("ADMIN_PASSWORD", "admin")
    user, created = User.objects.get_or_create(
        username=username, defaults={"is_staff": True, "is_superuser": True, "is_active": True}
    )
    changed = False
    if not (user.is_superuser and user.is_staff and user.is_active):
        user.is_superuser = user.is_staff = user.is_active = True
        changed = True
    if created or not user.check_password(password):
        user.set_password(password)
        changed = True
    if changed:
        user.save()
    return user


_admin_cache: User | None = None


def get_admin_user() -> User:
    global _admin_cache
    user = User.objects.filter(username=admin_username()).first()
    if user is None:
        user = bootstrap_admin()
    _admin_cache = user
    return user


# ---------------------------------------------------------------------------
# Login sessions
# ---------------------------------------------------------------------------


def authenticate(username: str, password: str) -> User | None:
    user = User.objects.filter(username=(username or "").strip()).first()
    if user and user.is_active and user.check_password(password):
        return user
    return None


def create_login(user: User) -> str:
    session = models.LoginSession(user=user, expires_at=timezone.now() + models.LOGIN_SESSION_TTL)
    session.save()
    return session.token


def resolve_user(token: str | None) -> User | None:
    if not token:
        return None
    session = models.LoginSession.objects.filter(pk=token).select_related("user").first()
    if session is None:
        return None
    if session.is_expired:
        session.delete()
        return None
    session.save(update_fields=["last_seen"])  # refresh activity (auto_now)
    return session.user


def destroy_login(token: str | None) -> None:
    if token:
        models.LoginSession.objects.filter(pk=token).delete()


def set_login_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        secure=not is_local(),
        max_age=int(models.LOGIN_SESSION_TTL.total_seconds()),
        path="/",
    )


def clear_login_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/")


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def current_user(request: Request) -> User:
    """Resolve the request's user, or 401. In local mode, auto-login as admin."""
    if is_local():
        return get_admin_user()
    user = resolve_user(request.cookies.get(COOKIE_NAME))
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_admin(request: Request) -> User:
    user = current_user(request)
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="Admin privileges required")
    return user


# ---------------------------------------------------------------------------
# Account management (admin)
# ---------------------------------------------------------------------------


def user_to_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_superuser,
        "is_active": user.is_active,
        "date_joined": user.date_joined.isoformat() if user.date_joined else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


def list_users() -> list[dict[str, Any]]:
    return [user_to_dict(u) for u in User.objects.order_by("username")]


def create_user(username: str, password: str, *, is_admin: bool = False) -> dict[str, Any]:
    username = (username or "").strip()
    if not username or not password:
        raise ValueError("Username and password are required")
    if User.objects.filter(username=username).exists():
        raise ValueError(f"A user named {username!r} already exists")
    user = User.objects.create_user(username=username, password=password)
    if is_admin:
        user.is_superuser = user.is_staff = True
        user.save(update_fields=["is_superuser", "is_staff"])
    return user_to_dict(user)


def delete_user(user_id: int) -> None:
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise ValueError("User not found")
    if user.username == admin_username():
        raise ValueError("The admin account cannot be deleted")
    user.delete()


def set_password(user_id: int, password: str) -> None:
    if not password:
        raise ValueError("Password is required")
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        raise ValueError("User not found")
    user.set_password(password)
    user.save(update_fields=["password"])
