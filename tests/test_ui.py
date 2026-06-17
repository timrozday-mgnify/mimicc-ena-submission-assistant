"""Playwright UI tests.

Driven against a real uvicorn server (``live_server_url`` fixture) with the
webin-cli runner patched, so no Docker is required. Skipped automatically if
Playwright (and its browsers) are not installed.
"""

from __future__ import annotations

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402


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


def test_library_presets_populated(page):
    page.click("nav button:has-text('Reads')")
    options = page.eval_on_selector_all("#presetSelect option", "els => els.map(e => e.value)")
    assert "illumina_amplicon_ssu" in options


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
                    SAMPLE: "", STUDY: "", PLATFORM: "", INSTRUMENT: "",
                    LIBRARY_SOURCE: "", LIBRARY_SELECTION: "", LIBRARY_STRATEGY: "", confidence: "none"
                },
                {
                    NAME: "runB", files: ["runB.fastq.gz"], paired: false,
                    FASTQ1: "", FASTQ2: "", FASTQ: "runB.fastq.gz",
                    SAMPLE: "", STUDY: "", PLATFORM: "", INSTRUMENT: "",
                    LIBRARY_SOURCE: "", LIBRARY_SELECTION: "", LIBRARY_STRATEGY: "", confidence: "none"
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
