"""Auth + account management + per-user isolation (hosted-mode behaviour).

The default test deployment mode is ``local`` (auto-login as admin). These tests
flip ``DEPLOYMENT_MODE=hosted`` to exercise the real login/cookie path, so they
use an HTTPS base URL (so the Secure login cookie is retained) and send the
``X-Requested-With`` header that the CSRF guard requires.
"""

from __future__ import annotations

import httpx
import main as _main
import pytest

_HEADERS = {"X-Requested-With": "fetch"}


@pytest.fixture
def hosted(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_MODE", "hosted")


@pytest.fixture
async def hclient(hosted):
    transport = httpx.ASGITransport(app=_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test", headers=_HEADERS) as c:
        yield c


async def _login(c, username="admin", password="admin"):
    return await c.post("/api/auth/login", json={"username": username, "password": password})


# ---------------------------------------------------------------------------
# Local mode: auto-login
# ---------------------------------------------------------------------------


async def test_local_mode_autologins_as_admin(client):
    r = await client.get("/api/auth/me")
    assert r.status_code == 200
    assert r.json()["user"]["username"] == "admin"
    assert r.json()["user"]["is_admin"] is True


# ---------------------------------------------------------------------------
# Hosted mode: login / logout
# ---------------------------------------------------------------------------


async def test_hosted_requires_login(hclient):
    assert (await hclient.get("/api/auth/me")).status_code == 401


async def test_login_logout_flow(hclient):
    assert (await _login(hclient)).status_code == 200
    me = await hclient.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["username"] == "admin"

    assert (await hclient.post("/api/auth/logout")).status_code == 200
    assert (await hclient.get("/api/auth/me")).status_code == 401


async def test_login_rejects_bad_password(hclient):
    assert (await _login(hclient, password="wrong")).status_code == 401


async def test_csrf_blocks_state_change_without_header(hosted):
    # No X-Requested-With header -> blocked in hosted mode.
    transport = httpx.ASGITransport(app=_main.app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        await c.post("/api/auth/login", json={"username": "admin", "password": "admin"})  # login is exempt
        r = await c.post("/api/sessions", json={"name": "x"})
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# Admin account management
# ---------------------------------------------------------------------------


async def test_admin_can_create_and_list_users(hclient):
    await _login(hclient)
    r = await hclient.post("/api/admin/users", json={"username": "alice", "password": "pw"})
    assert r.status_code == 200
    usernames = [u["username"] for u in (await hclient.get("/api/admin/users")).json()]
    assert "alice" in usernames


async def test_admin_cannot_create_duplicate(hclient):
    await _login(hclient)
    await hclient.post("/api/admin/users", json={"username": "bob", "password": "pw"})
    r = await hclient.post("/api/admin/users", json={"username": "bob", "password": "pw"})
    assert r.status_code == 400


async def test_admin_cannot_delete_admin_account(hclient):
    await _login(hclient)
    admin_id = (await hclient.get("/api/auth/me")).json()["user"]["id"]
    r = await hclient.delete(f"/api/admin/users/{admin_id}")
    assert r.status_code == 400


async def test_non_admin_forbidden_from_admin_endpoints(hclient):
    await _login(hclient)
    await hclient.post("/api/admin/users", json={"username": "carol", "password": "pw"})
    await hclient.post("/api/auth/logout")

    await _login(hclient, "carol", "pw")
    assert (await hclient.get("/api/admin/users")).status_code == 403


# ---------------------------------------------------------------------------
# Per-user session isolation
# ---------------------------------------------------------------------------


async def test_sessions_are_isolated_between_users(hclient):
    # Admin creates a user and a session.
    await _login(hclient)
    await hclient.post("/api/admin/users", json={"username": "dave", "password": "pw"})
    sid = (await hclient.post("/api/sessions", json={"name": "admin-session"})).json()["id"]
    await hclient.post("/api/auth/logout")

    # dave cannot see or fetch the admin's session.
    await _login(hclient, "dave", "pw")
    assert (await hclient.get("/api/sessions")).json() == []
    assert (await hclient.get(f"/api/sessions/{sid}")).status_code == 404

    # Same session name is allowed for a different owner (unique per user).
    assert (await hclient.post("/api/sessions", json={"name": "admin-session"})).status_code == 200
