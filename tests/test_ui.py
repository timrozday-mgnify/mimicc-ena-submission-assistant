"""Playwright UI tests.

Driven against a real WSGI server (``live_server_url`` fixture) with the
webin-cli runner patched, so no Docker is required. Skipped automatically if
Playwright (and its browsers) are not installed.
"""

from __future__ import annotations

import time

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


def _open_session(pg):
    """Create + open a session so the tabs unlock (the app gates on a session)."""
    pg.wait_for_selector("#sessionModal.show")
    name = f"ui-test-{int(time.time() * 1000)}"
    pg.fill("#newSessionName", name)
    pg.click("#sessionModal button:has-text('Create & open')")
    pg.wait_for_selector("#sessionModal:not(.show)", state="attached")
    pg.wait_for_function("() => !document.body.classList.contains('no-session')")


@pytest.fixture
def page(live_server_url):
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception as exc:  # browsers not installed
            pytest.skip(f"Chromium not available: {exc}")
            return
        pg = browser.new_page()
        pg.goto(live_server_url)
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


def test_credentials_indicator(page):
    # live_server pre-sets credentials, so health reports them configured.
    page.reload()
    page.wait_for_timeout(300)
    assert "set" in page.inner_text("#credStatus")


def test_library_preset_ui_removed(page):
    # Experiment metadata now comes from its own DataHarmonizer panel, not a
    # hardcoded preset dropdown.
    page.click("nav button:has-text('Reads')")
    assert page.query_selector("#presetSelect") is None
    assert page.query_selector("#expDhPanel") is not None


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

    page.click("nav button:has-text('Reads')")
    page.click("#expDhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#expDhPanel", "class")
    page.click("#expDhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#expDhPanel", "class")


def test_reads_sample_assignment_and_row_delete(page):
    page.click("nav button:has-text('Reads')")
    page.evaluate(
        """async () => {
            await fetch('/api/credentials', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'Webin-test', password: 'secret'})
            });
        }"""
    )
    page.click("button:has-text('Load samples')")
    page.wait_for_selector("#readSampleList .sample-item")
    assert "0 files" in page.inner_text("#readSampleList")

    page.evaluate(
        """() => {
            RUN_ROWS = [
                {
                    NAME: "runA", files: ["runA_R1.fastq.gz", "runA_R2.fastq.gz"], paired: true,
                    FASTQ1: "runA_R1.fastq.gz", FASTQ2: "runA_R2.fastq.gz", FASTQ: "",
                    SAMPLE: "", STUDY: "", confidence: "none"
                },
                {
                    NAME: "runB", files: ["runB.fastq.gz"], paired: false,
                    FASTQ1: "", FASTQ2: "", FASTQ: "runB.fastq.gz",
                    SAMPLE: "", STUDY: "", confidence: "none"
                }
            ];
            renderRunTable();
        }"""
    )

    page.click("#readSampleList .sample-item:has-text('MIMICC_A_1')")
    page.click("#runTable tbody tr:first-child td.wrap")
    first_sample = page.locator("#runTable tbody tr").nth(0).locator("input").nth(1)
    assert first_sample.input_value() == "ERS111"
    assert "2 files" in page.inner_text("#readSampleList .sample-item:has-text('MIMICC_A_1')")

    page.click("#runTable tbody tr:first-child .icon-btn")
    assert page.locator("#runTable tbody tr").count() == 1
    assert "0 files" in page.inner_text("#readSampleList .sample-item:has-text('MIMICC_A_1')")


def test_records_runs_and_experiments_views(page):
    page.evaluate(
        """async () => {
            await fetch('/api/credentials', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({username: 'Webin-test', password: 'secret'})
            });
        }"""
    )
    page.click("nav button:has-text('Records')")

    page.select_option("#recEntity", "runs")
    page.click("button:has-text('Fetch')")
    page.wait_for_selector("#recOut table")
    headers = page.inner_text("#recOut thead")
    body = page.inner_text("#recOut tbody")
    assert "experiment_accession" in headers
    assert "study_accession" in headers
    assert "sample_accession" in headers
    assert "ERX111" in body
    assert "ERP111" in body
    assert "ERS111" in body

    page.select_option("#recEntity", "experiments")
    page.click("button:has-text('Fetch')")
    page.wait_for_function("() => document.querySelector('#recOut thead')?.innerText.includes('sample_accession')")
    headers = page.inner_text("#recOut thead")
    body = page.inner_text("#recOut tbody")
    assert "study_accession" in headers
    assert "sample_accession" in headers
    assert "ERP111" in body
    assert "ERS111" in body


def _inject_fake_experiment_dh(page, rows):
    """Stand in for a loaded experiment DataHarmonizer grid: the real second
    template isn't built in this (non-Docker) test environment, but the merge
    logic only ever talks to window.dataHarmonizer.getExportJson(), so a
    minimal fake covering that one call is enough to test it."""
    page.evaluate(
        """(rows) => {
            const frame = document.getElementById('expDhFrame');
            frame.contentWindow.dataHarmonizer = {
                ready: true,
                getExportJson: () => ({ Container: { MIMICC_Experiment: rows } }),
            };
        }""",
        rows,
    )


def test_reads_submit_merges_experiment_metadata(page):
    page.click("nav button:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                {
                    NAME: "runA", files: ["runA_R1.fastq.gz", "runA_R2.fastq.gz"], paired: true,
                    FASTQ1: "runA_R1.fastq.gz", FASTQ2: "runA_R2.fastq.gz", FASTQ: "",
                    SAMPLE: "ERS111", STUDY: "ERP111", confidence: "manual"
                }
            ];
            renderRunTable();
        }"""
    )
    _inject_fake_experiment_dh(
        page,
        [
            {
                "Experiment name": "runA",
                "Sample alias": "ERS111",
                "Platform": "ILLUMINA",
                "Instrument": "Illumina MiSeq",
                "Library source": "METAGENOMIC",
                "Library selection": "PCR",
                "Library strategy": "AMPLICON",
            }
        ],
    )

    captured = {}

    def capture(route):
        captured["body"] = route.request.post_data_json
        # Respond with an empty plan so submitReads() finishes without needing
        # the local helper (which isn't running in this test).
        route.fulfill(status=200, content_type="application/json", body='{"plan": [], "warnings": []}')

    # Pretend the local upload helper is running + a reads dir is set, so the
    # flow proceeds to request the plan from the server.
    page.evaluate("() => { HELPER_OK = true; document.getElementById('readsLocalDir').value = '/tmp/reads'; }")
    page.route("**/api/reads/plan", capture)
    page.evaluate("() => submitReads(true)")
    page.wait_for_timeout(500)

    assert captured.get("body"), "submitReads() never reached /api/reads/plan"
    run = captured["body"]["runs"][0]
    assert run["NAME"] == "runA"
    assert run["SAMPLE"] == "ERS111"
    assert run["STUDY"] == "ERP111"
    assert run["PLATFORM"] == "ILLUMINA"
    assert run["INSTRUMENT"] == "Illumina MiSeq"
    assert run["LIBRARY_SOURCE"] == "METAGENOMIC"
    assert run["LIBRARY_SELECTION"] == "PCR"
    assert run["LIBRARY_STRATEGY"] == "AMPLICON"
    assert run["FASTQ1"] == "runA_R1.fastq.gz" and run["FASTQ2"] == "runA_R2.fastq.gz"


def test_reads_submit_blocks_without_matching_experiment_row(page):
    page.click("nav button:has-text('Reads')")
    page.evaluate(
        """() => {
            RUN_ROWS = [
                { NAME: "runB", files: ["runB.fastq.gz"], paired: false,
                  FASTQ1: "", FASTQ2: "", FASTQ: "runB.fastq.gz",
                  SAMPLE: "ERS222", STUDY: "ERP111", confidence: "manual" }
            ];
            renderRunTable();
        }"""
    )
    # Experiment grid is "loaded" but has no row for runB.
    _inject_fake_experiment_dh(page, [])

    submitted = {"called": False}
    page.route("**/api/reads/plan", lambda route: submitted.update(called=True) or route.continue_())
    page.evaluate("() => submitReads(true)")
    page.wait_for_timeout(300)

    assert submitted["called"] is False
    assert "No experiment metadata row found" in page.inner_text("#submitReadsBanner")
