"""Playwright UI tests against the real `docker compose` stack.

Unlike ``test_ui.py`` (an in-process WSGI thread with ``ena_service`` mocked),
this drives the actual built images: real Postgres-backed sessions and a real
``dhtb`` sidecar container reached over the network. There's no way to
monkeypatch a function inside a process this test doesn't run, so this file
only covers what doesn't depend on mocked ENA data (page load, sessions, tab
switching, the DH bundle iframe, the dhtb sidecar iframe) — the
ENA-data-dependent tests in ``test_ui.py`` stay there.

Opt-in and slow (image build + container startup): skipped unless
``COMPOSE_TEST=1`` is set. See README "Docker Compose tests".
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("COMPOSE_TEST"), reason="set COMPOSE_TEST=1 to run the docker-compose UI tests"
)

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

_REPO = Path(__file__).resolve().parent.parent
_PORT = os.environ.get("MIMICC_PORT", "19000")
_DHTB_PORT = os.environ.get("MIMICC_DHTB_PORT", "18765")


def _open_session(pg):
    pg.wait_for_selector("#sessionModal.show")
    name = f"compose-ui-test-{int(time.time() * 1000)}"
    pg.fill("#newSessionName", name)
    pg.click("#sessionModal button:has-text('Create & open')")
    pg.wait_for_selector("#sessionModal:not(.show)", state="attached")
    pg.wait_for_function("() => !document.body.classList.contains('no-session')")


@pytest.fixture(scope="session")
def compose_url():
    env = {**os.environ, "MIMICC_PORT": _PORT, "MIMICC_DHTB_PORT": _DHTB_PORT}
    subprocess.run(
        ["docker", "compose", "up", "--build", "-d", "db", "mimicc-server", "dhtb"],
        cwd=_REPO,
        env=env,
        check=True,
    )
    try:
        url = f"http://127.0.0.1:{_PORT}"
        deadline = time.time() + 300  # image build included
        while time.time() < deadline:
            try:
                if httpx.get(f"{url}/api/health", timeout=1).status_code == 200:
                    break
            except Exception:
                time.sleep(1)
        else:
            raise RuntimeError("composed stack did not become healthy")
        yield url
    finally:
        subprocess.run(["docker", "compose", "down", "-v"], cwd=_REPO, env=env, check=True)


@pytest.fixture
def page(compose_url):
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page()
        pg.goto(compose_url)
        _open_session(pg)
        yield pg
        browser.close()


def test_page_loads_with_tabs(page):
    assert "MIMICC ENA Submission Assistant" in page.title()
    for tab in ("Credentials", "Studies", "Samples", "Reads", "Records"):
        assert page.query_selector(f"nav button:has-text('{tab}')")


def test_env_pill_default_test(page):
    assert page.inner_text("#envPill").strip() == "TEST"


def test_tab_switching(page):
    page.click("nav button:has-text('Reads')")
    assert page.query_selector("#tab-reads").is_visible()
    assert not page.query_selector("#tab-creds").is_visible()


def test_maximize_controls_for_reads_and_dataharmonizer(page):
    page.click("nav button:has-text('Reads')")
    page.click("#readsAssignPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#readsAssignPanel", "class")
    page.click("#readsAssignPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#readsAssignPanel", "class")

    page.click("nav button:has-text('Samples')")
    page.click("#dhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#dhPanel", "class")
    page.click("#dhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#dhPanel", "class")


def test_dh_bundle_iframe_loads(page):
    # Real DataHarmonizer bundle (server/static/dh/, seeded at container
    # start), not a stub — confirms the dh-builder image stage actually
    # produced a usable bundle.
    page.click("nav button:has-text('Samples')")
    page.wait_for_function("() => document.getElementById('dhFrame').src.includes('/dh/')")
    frame = page.frame_locator("#dhFrame")
    frame.locator("body").wait_for(timeout=15_000)


def test_dhtb_sidecar_iframe_loads(page):
    # The dhtb iframe is cross-origin (separate container on MIMICC_DHTB_PORT)
    # — this is the integration the in-process fixture (test_ui.py) can't
    # exercise, since there's no real second container to reach there.
    page.click("nav button:has-text('Schema')")
    page.wait_for_function("() => !!document.getElementById('schemaEditorFrame').src")
    deadline = time.time() + 15
    while time.time() < deadline:
        if any(f"127.0.0.1:{_DHTB_PORT}" in f.url for f in page.frames):
            return
        page.wait_for_timeout(200)
    raise AssertionError("dhtb sidecar iframe never loaded")
