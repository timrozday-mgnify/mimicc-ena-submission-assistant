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
    frame.locator(".ht_master .htCore tbody td").first.wait_for(timeout=15_000)
    first_cell = frame.locator(".ht_master .htCore tbody td").first
    assert first_cell.evaluate("el => getComputedStyle(el).whiteSpace") == "nowrap"
    assert first_cell.evaluate("el => getComputedStyle(el).textOverflow") == "ellipsis"

    before = frame.locator(".ht_master .htCore tbody tr").evaluate_all(
        "rows => rows.map(row => Math.round(row.getBoundingClientRect().height))"
    )
    frame.locator(".ht_master .wtHolder").first.evaluate(
        """el => {
            el.scrollLeft = 900;
            el.dispatchEvent(new Event('scroll', { bubbles: true }));
        }"""
    )
    page.wait_for_timeout(100)
    after = frame.locator(".ht_master .htCore tbody tr").evaluate_all(
        "rows => rows.map(row => Math.round(row.getBoundingClientRect().height))"
    )
    assert after == before


def _wait_for_dh_iframe_ready(page, frame_id):
    page.wait_for_function(
        """(frameId) => {
            const frame = document.querySelector(frameId);
            return Boolean(
                frame?.contentWindow?.dataHarmonizer?.ready &&
                frame?.contentDocument?.querySelector('.handsontable')
            );
        }""",
        arg=frame_id,
        timeout=20_000,
    )
    page.frame_locator(frame_id).locator(".ht_master .htCore tbody td").first.wait_for(timeout=15_000)


def _wait_for_banner_text(page, selector, text, errors):
    try:
        page.wait_for_function(
            "([selector, text]) => document.querySelector(selector)?.innerText.includes(text)",
            arg=[selector, text],
            timeout=35_000,
        )
    except Exception as exc:
        banner_text = page.inner_text(selector)
        raise AssertionError(f"{selector} never contained {text!r}. Banner: {banner_text!r}. Errors: {errors}") from exc


def test_schema_selection_reloads_real_dh_iframes_without_toolbar_error(page):
    errors = []
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)

    page.click("nav button:has-text('Samples')")
    page.wait_for_selector("#sampleSchemaSelect option[value='mimicc_experiment']", state="attached")
    page.select_option("#sampleSchemaSelect", "mimicc_experiment")
    page.click("#dhPanel button:has-text('Use this schema')")
    _wait_for_banner_text(page, "#prepBanner", "Switched the sample grid", errors)
    assert "Switched the sample grid" in page.inner_text("#prepBanner")
    _wait_for_dh_iframe_ready(page, "#dhFrame")

    page.click("nav button:has-text('Reads')")
    page.wait_for_selector("#expSchemaSelect option[value='mimicc_sample']", state="attached")
    page.select_option("#expSchemaSelect", "mimicc_sample")
    page.click("#expDhPanel button:has-text('Use this schema')")
    _wait_for_banner_text(page, "#readsBanner", "Switched the experiment grid", errors)
    assert "Switched the experiment grid" in page.inner_text("#readsBanner")
    _wait_for_dh_iframe_ready(page, "#expDhFrame")

    assert not [message for message in errors if "getColumnCoordinates" in message]


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
