"""Playwright UI tests.

Driven against a real WSGI server (``live_server_url`` fixture) with the
webin-cli runner patched, so no Docker is required. Skipped automatically if
Playwright (and its browsers) are not installed.
"""

from __future__ import annotations

import json
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
        assert page.query_selector(f"a.vf-tabs__link:has-text('{tab}')")


def test_env_pill_default_test(page):
    assert page.inner_text("#envPill").strip() == "TEST"


def test_tab_switching(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    # VF JS assigns the section ID to the tab anchor too; target content sections by class position
    sections = page.locator(".vf-tabs-content .vf-tabs__section")
    reads_idx = 3  # 0=creds, 1=studies, 2=samples, 3=reads
    assert sections.nth(reads_idx).is_visible()
    assert not sections.nth(0).is_visible()  # creds section should be hidden


def test_credentials_indicator(page):
    # Credentials are entered in the browser (held for this tab only); saving
    # them flips the indicator to "set".
    page.fill("#username", "Webin-test")
    page.fill("#password", "secret")
    page.click("#vf-tabs__section--creds button:has-text('Save')")
    page.wait_for_timeout(100)
    assert "set" in page.inner_text("#credStatus")


def test_library_preset_ui_removed(page):
    # Experiment metadata now comes from its own DataHarmonizer panel, not a
    # hardcoded preset dropdown.
    page.click("a.vf-tabs__link:has-text('Reads')")
    assert page.query_selector("#presetSelect") is None
    assert page.query_selector("#expDhPanel") is not None


def test_study_submit_displays_submission_logs(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    assert page.locator("#studyLog.log").count() == 1
    assert page.inner_text("#studyLog").strip() == "No study submission run yet."
    assert page.locator("#vf-tabs__section--studies h3:has-text('Submission log')").count() == 1
    page.evaluate(
        """() => {
            CREDS = { username: 'Webin-test', password: 'secret' };
            window.__preparedStudies = [{ alias: "study-a", TITLE: "Study A" }];
        }"""
    )
    page.route(
        "**/api/study/submit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=(
                '{"success":false,"accessions":[],"error":"receipt rejected",'
                '"logs":["INFO: XSD validation passed","ERROR: Receipt: invalid study"]}'
            ),
        ),
    )

    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyLog')?.innerText.includes('XSD validation passed')")
    assert page.inner_text("#studyBanner").strip() == "invalid study"
    assert "INFO: XSD validation passed" in page.inner_text("#studyLog")
    assert "ERROR: Receipt: invalid study" in page.inner_text("#studyLog")
    assert "No records." in page.inner_text("#studyOut")
    assert page.evaluate("() => window.__lastStudySubmitResponse?.error") == "receipt rejected"


def test_study_submit_without_prepared_records_logs_error(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyBanner')?.innerText.includes('No prepared studies')")
    assert "No prepared studies" in page.inner_text("#studyBanner")
    assert page.inner_text("#studyLog").strip() == "ERROR: No prepared studies. Click Prepare first."


def test_study_submit_displays_log_area_when_response_has_no_logs(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.evaluate(
        """() => {
            CREDS = { username: 'Webin-test', password: 'secret' };
            window.__preparedStudies = [{ alias: "study-a", TITLE: "Study A" }];
        }"""
    )
    page.route(
        "**/api/study/submit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success":false,"accessions":[],"error":"receipt rejected"}',
        ),
    )

    page.click("button:has-text('Submit prepared studies')")
    page.wait_for_function("() => document.querySelector('#studyLog')?.innerText.includes('receipt rejected')")
    assert page.inner_text("#studyLog").strip() == "ERROR: receipt rejected"


def test_study_prepare_displays_table_and_banner_in_prepare_panel(page):
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.route(
        "**/api/study/prepare",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"records":[{"alias":"study-a","TITLE":"Study A"}]}',
        ),
    )
    # studyDhApi() is normally backed by the DH iframe; stub it directly so
    # Prepare can run without a real DataHarmonizer bundle in this test.
    page.evaluate("() => { studyDhApi = () => ({ getExportJson: () => ({}) }); }")
    page.click("#vf-tabs__section--studies button:has-text('Prepare')")
    page.wait_for_selector("#studyPrepOut table")
    assert "study-a" in page.inner_text("#studyPrepOut")
    assert "Prepared 1 study record(s)" in page.inner_text("#studyPrepBanner")
    # The old shared banner must NOT pick up prepare feedback anymore.
    assert page.inner_text("#studyBanner").strip() == ""


def test_sample_prepare_displays_table_like_study(page):
    page.click("a.vf-tabs__link:has-text('Samples')")
    page.route(
        "**/api/sample/prepare",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"records":[{"alias":"sample-a","TITLE":"Sample A"}],"count":1}',
        ),
    )
    # No DH iframe is loaded in this test environment, so dhApi() returns
    # null and Prepare falls back to the textarea, matching the documented
    # "DataHarmonizer isn't available" path.
    page.fill("#dhExport", '{"Container": {}}')
    page.click("#vf-tabs__section--samples button:has-text('Prepare')")
    page.wait_for_selector("#prepOut table")
    assert "sample-a" in page.inner_text("#prepOut")
    assert "Prepared 1 sample record(s)" in page.inner_text("#prepBanner")


def test_iframe_loses_focus_on_outside_click(page):
    page.click("a.vf-tabs__link:has-text('Samples')")
    page.click("#dhFrame")
    assert page.evaluate("() => document.activeElement.id") == "dhFrame"
    page.click("#sampleFilter")
    assert page.evaluate("() => document.activeElement.id") == "sampleFilter"
    page.fill("#sampleFilter", "hello")
    assert page.evaluate("() => $('sampleFilter').value") == "hello"


def test_maximize_controls_for_reads_and_dataharmonizer(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#readsAssignPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#readsAssignPanel", "class")
    # The fixed panel must sit above Visual Framework's hero and tab layers;
    # otherwise those elements are visible through the maximized pairing view.
    page.evaluate("window.scrollTo(0, 0)")
    stacking_check = page.evaluate(
        """() => {
            const panel = document.querySelector('#readsAssignPanel');
            return ['.vf-hero__heading', '.vf-tabs__link'].map((selector) => {
                const rect = document.querySelector(selector).getBoundingClientRect();
                const topElement = document.elementFromPoint(
                    rect.left + rect.width / 2, rect.top + rect.height / 2,
                );
                return { selector, covered: panel.contains(topElement) };
            });
        }"""
    )
    assert all(result["covered"] for result in stacking_check), stacking_check
    page.click("#readsAssignPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#readsAssignPanel", "class")

    page.click("a.vf-tabs__link:has-text('Studies')")
    page.click("#studyDhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#studyDhPanel", "class")
    page.click("#studyDhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#studyDhPanel", "class")

    page.click("a.vf-tabs__link:has-text('Samples')")
    page.click("#dhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#dhPanel", "class")
    page.click("#dhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#dhPanel", "class")

    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#expDhPanel button[aria-label='Maximize panel']")
    assert "maximized" in page.get_attribute("#expDhPanel", "class")
    page.click("#expDhPanel button[aria-label='Minimize panel']")
    assert "maximized" not in page.get_attribute("#expDhPanel", "class")


def test_reads_pairing_splitter_resizes_full_height_grid(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.click("#readsAssignPanel button[aria-label='Maximize panel']")
    page.wait_for_timeout(100)

    grid = page.locator("#pairSamples")
    divider = page.locator("#readsAssignDivider")
    grid_box = grid.bounding_box()
    workspace_box = page.locator("#readsAssignGrid").bounding_box()
    # The sample browser consumes the left pane's available height rather than
    # keeping its old fixed height when the panel is maximized.
    assert grid_box["height"] > 250
    assert workspace_box["y"] + workspace_box["height"] - (grid_box["y"] + grid_box["height"]) < 2
    row_viewport = page.locator("#pairSamples .ht_master .wtHolder").first.bounding_box()
    assert row_viewport["height"] > grid_box["height"] - 120
    assert row_viewport["y"] + row_viewport["height"] <= grid_box["y"] + grid_box["height"] + 2

    page.evaluate("() => banner('readsBanner', true, 'Samples loaded.')")
    page.wait_for_timeout(100)
    assert grid.bounding_box()["y"] + grid.bounding_box()["height"] <= page.locator("#readsBanner").bounding_box()["y"]
    row_viewport = page.locator("#pairSamples .ht_master .wtHolder").first.bounding_box()
    grid_box = grid.bounding_box()
    assert row_viewport["y"] + row_viewport["height"] <= grid_box["y"] + grid_box["height"] + 2

    before = page.locator(".assign-samples-pane").bounding_box()["width"]
    divider_box = divider.bounding_box()
    page.mouse.move(divider_box["x"] + 7, divider_box["y"] + 30)
    page.mouse.down()
    page.mouse.move(divider_box["x"] + 120, divider_box["y"] + 30)
    page.mouse.up()

    assert page.locator(".assign-samples-pane").bounding_box()["width"] > before + 80
    assert int(divider.get_attribute("aria-valuenow")) > 42


def _load_pairing_samples(page):
    page.click("button:has-text('Load samples')")
    page.wait_for_function("() => document.getElementById('pairSamples').getRows().length > 0")


def _assigned_count(page, accession):
    """The reads_assigned badge, read through the element's rendered cell."""
    return page.evaluate(
        """(acc) => {
            const grid = document.getElementById('pairSamples');
            const index = grid.getRows().findIndex((row) => row.accession === acc);
            const cell = document.querySelectorAll(
                '#pairSamples .ht_clone_inline_start td.ena-browser-badge')[index];
            return cell ? cell.innerText.trim() : null;
        }""",
        accession,
    )


def test_reads_sample_assignment_and_row_delete(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _load_pairing_samples(page)
    assert page.evaluate("() => document.getElementById('pairSamples').getRows().length") == 2

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

    # Selecting through the element's API and by a real click must both reach
    # SELECTED_SAMPLE — it is the only thing the run-row click reads.
    page.evaluate("() => document.getElementById('pairSamples').setSelection(['ERS222'])")
    assert page.evaluate("() => SELECTED_SAMPLE") == "ERS222"
    page.locator("#pairSamples .ht_master td", has_text="ERS111").first.click()
    page.wait_for_function("() => SELECTED_SAMPLE === 'ERS111'")

    page.click("#runTable tbody tr:first-child td.wrap")
    first_sample = page.locator("#runTable tbody tr").nth(0).locator("input").nth(1)
    assert first_sample.input_value() == "ERS111"
    assert _assigned_count(page, "ERS111") == "2"

    page.click("#runTable tbody tr:first-child .icon-btn")
    assert page.locator("#runTable tbody tr").count() == 1
    assert _assigned_count(page, "ERS111") == "0"


def test_reads_pairing_selection_survives_a_filter(page):
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    _load_pairing_samples(page)

    page.evaluate("() => document.getElementById('pairSamples').setSelection(['ERS111'])")
    page.evaluate(
        """() => document.getElementById('pairSamples')
            .setFilters([{ column: 'accession', operator: 'eq', value: 'ERS111' }])"""
    )
    assert page.evaluate("() => document.getElementById('pairSamples').getVisibleRows().length") == 1
    assert page.evaluate("() => document.getElementById('pairSamples').getSelection()") == ["ERS111"]
    assert page.evaluate("() => SELECTED_SAMPLE") == "ERS111"


def _fetch_records(page, entity):
    """Fetch one entity into the grid and wait for the rows to land."""
    page.select_option("#recEntity", entity)
    page.click("button:has-text('Fetch')")
    page.wait_for_function(
        "() => document.getElementById('recGrid').getRows().length > 0",
    )
    return page.evaluate("() => document.getElementById('recGrid').getRows()")


def test_records_runs_and_experiments_views(page):
    """The grid is fed the rows the API returned, linking accessions included.

    Asserted through the element's public API — the grid's own rendering,
    filtering and sorting are ena-browser's Playwright suite, not this one's.
    """
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")

    rows = _fetch_records(page, "runs")
    assert rows[0]["experiment_accession"] == "ERX111"
    assert rows[0]["study_accession"] == "ERP111"
    assert rows[0]["sample_accession"] == "ERS111"

    rows = _fetch_records(page, "experiments")
    assert rows[0]["accession"] == "ERX111"
    assert rows[0]["sample_accession"] == "ERS111"


def test_records_criteria_reach_the_request(page):
    """The fetch criteria are request criteria — they go on the query string."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")

    seen = []
    page.on("request", lambda r: seen.append(r.url) if "/api/records/samples?" in r.url else None)
    page.fill("#recSearch", "MIMICC")
    page.fill("#recLinked", "PRJEB1234")
    page.check("#recUnlinked")
    _fetch_records(page, "samples")

    assert "search=MIMICC" in seen[-1]
    assert "linked_to=PRJEB1234" in seen[-1]
    assert "unlinked=true" in seen[-1]


def _enable_write(page):
    """Tick write mode. It confirms first (edits go to ENA), so accept that."""
    page.on("dialog", lambda dialog: dialog.accept())
    page.check("#recWrite")


def test_records_row_action_posts_accession(page):
    """A row-action button the element renders reaches /api/records/action."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    posted = []

    def handle(route):
        posted.append(route.request.post_data_json)
        route.fulfill(status=200, content_type="application/json", body='{"success": true, "messages": "released"}')

    page.route("**/api/records/action", handle)
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)  # row actions only exist in write mode
    _fetch_records(page, "samples")

    # The frozen-column clone is the copy on top; the master one under it is
    # covered by design. Clicking it is also the regression test for the page
    # scroll pinning in core.js — without it the grid slides out from under the
    # cursor between mousedown and mouseup and the click never lands.
    page.locator("ena-browser#recGrid .ht_clone_inline_start button:has-text('Release')").first.click()
    page.wait_for_timeout(300)

    assert posted, "no lifecycle action was posted"
    assert posted[0]["action"] == "release"
    assert posted[0]["accession"] == "ERS111"


def _edit_title(page, current, text):
    """Type into a grid cell — also the regression test for the narrowed
    keyboard swallower (core.js): without it Handsontable gets no keys at all."""
    page.locator("ena-browser#recGrid td", has_text=current).first.dblclick()
    page.keyboard.press("ControlOrMeta+a")
    page.keyboard.type(text)
    page.keyboard.press("Enter")
    page.wait_for_function(
        "() => document.getElementById('recGrid').getChangeSet().rows.length > 0",
    )


def test_records_edit_lands_in_the_change_set(page):
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.route(
        "**/api/records/samples/fields",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"fields": {}}'),
    )
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)
    _fetch_records(page, "samples")

    _edit_title(page, "Sample A1", "Edited A1")
    changes = page.evaluate("() => pendingChanges()")
    assert changes[0]["accession"] == "ERS111"
    assert changes[0]["changes"]["title"] == "Edited A1"


def test_records_manifest_gate(page):
    """Submit stays locked until the manifests for the current edits have been
    built, and re-locks as soon as anything is edited again."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.route(
        "**/api/records/samples/fields",
        lambda route: route.fulfill(status=200, content_type="application/json", body='{"fields": {}}'),
    )
    page.route(
        "**/api/records/modify/preview",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "results": [{"accession": "ERS111", "success": true, '
            '"xml": "<SAMPLE_SET/>", "changes": {"title": "Edited A1"}, "messages": []}]}',
        ),
    )
    page.click("a.vf-tabs__link:has-text('Records')")
    _enable_write(page)
    _fetch_records(page, "samples")

    _edit_title(page, "Sample A1", "Edited A1")
    assert page.is_disabled("#recSubmit"), "staged edits alone must not unlock submit"
    assert not page.is_disabled("#recGenerate")

    page.click("#recGenerate")
    page.wait_for_function("() => !document.getElementById('recSubmit').disabled")
    assert "manifest(s) built" in page.inner_text("#recManifestState")

    _edit_title(page, "Edited A1", "Edited again")
    assert page.is_disabled("#recSubmit"), "a further edit must re-lock submit"


def test_records_grid_layout_survives_a_session_round_trip(page):
    """A session stores the grid's arrangement, never its rows: switching away
    and back returns the layout and filters, and re-fetches the records."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    _fetch_records(page, "samples")

    page.evaluate(
        """() => {
            const grid = document.getElementById('recGrid');
            grid.setLayout({ ...grid.getLayout(), hidden: ['alias'], pinned: ['title'] });
            grid.setFilters([{ column: 'status', operator: 'eq', value: 'PRIVATE' }]);
        }"""
    )
    first = page.evaluate("() => SESSION.id")
    page.evaluate("() => saveSessionNow()")
    page.wait_for_timeout(300)

    # A second session: a blank grid, with none of the first session's state.
    page.evaluate("() => openSessionModal()")
    _open_session(page)
    assert page.evaluate("() => document.getElementById('recGrid').getRows().length") == 0
    assert page.evaluate("() => document.getElementById('recGrid').getFilters()") == []

    # Back to the first: arrangement restored, rows re-fetched (not restored).
    page.evaluate("(id) => openSession(id)", first)
    page.wait_for_function("() => document.getElementById('recGrid').getRows().length > 0")
    layout = page.evaluate("() => document.getElementById('recGrid').getLayout()")
    assert layout["hidden"] == ["alias"]
    assert layout["pinned"] == ["title"]
    assert page.evaluate("() => document.getElementById('recGrid').getFilters()")[0]["column"] == "status"


def test_session_state_holds_no_grid_rows(page):
    """Row data is never persisted — a saved status is a stale status."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Records')")
    _fetch_records(page, "samples")

    state = page.evaluate("() => collectState()")
    assert state["v"] == 2
    assert "recOut" not in state["resultsHtml"]
    assert set(state["grids"]["records"]) == {"layout", "filters", "entity"}
    # The debug log deliberately keeps the first raw row; nothing else may.
    assert "ERS111" not in json.dumps({k: v for k, v in state.items() if k != "logs"})


def test_studies_grid_confirms_only_this_submission(page):
    """After a submit, the grid shows what ENA holds — filtered to the
    accessions this submission produced, and read-only."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.route(
        "**/api/study/submit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "logs": ["INFO: done"], '
            '"accessions": [{"alias": "studyA", "accession": "ERP111"}]}',
        ),
    )
    page.click("a.vf-tabs__link:has-text('Studies')")
    page.evaluate("() => { window.__preparedStudies = [{ alias: 'studyA' }]; }")
    page.click("button:has-text('Submit prepared studies')")

    page.wait_for_function("() => document.getElementById('studyGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('studyGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('studyGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERP111"]
    assert page.get_attribute("#studyGrid", "mode") == "read"
    assert page.evaluate("() => document.getElementById('studyGridEmpty').style.display") == "none"


def test_samples_grid_confirms_only_this_submission(page):
    """The Phase-5 study check, for samples — the three grids are parallel."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.route(
        "**/api/sample/submit",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body='{"success": true, "logs": ["INFO: done"], '
            '"accessions": [{"alias": "MIMICC_A_1", "accession": "ERS111"}]}',
        ),
    )
    page.click("a.vf-tabs__link:has-text('Samples')")
    page.evaluate(
        """() => {
            window.__prepared = [{ alias: 'MIMICC_A_1' }];
            document.getElementById('sampleSubmitBtn').disabled = false;
        }"""
    )
    page.click("#sampleSubmitBtn")

    page.wait_for_function("() => document.getElementById('sampleGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('sampleGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('sampleGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERS111"]
    assert page.get_attribute("#sampleGrid", "mode") == "read"


def test_reads_grid_confirms_submitted_runs(page):
    """The reads confirmation grid is limited to this session's runs and shows
    ENA's archiving state, which "submitted" does not imply."""
    page.evaluate("() => { CREDS = { username: 'Webin-test', password: 'secret' }; }")
    page.click("a.vf-tabs__link:has-text('Reads')")
    page.evaluate(
        """() => { READS_RUNS = { runA: { run_name: 'runA', status: 'done',
                                          run_accession: 'ERR111',
                                          experiment_accession: 'ERX111' } }; }"""
    )
    page.click("#vf-tabs__section--reads button:has-text('Refresh from ENA')")

    page.wait_for_function("() => document.getElementById('readsGrid').getRows().length > 0")
    assert page.evaluate("() => document.getElementById('readsGrid').getRows().length") == 2
    visible = page.evaluate("() => document.getElementById('readsGrid').getVisibleRows()")
    assert [row["accession"] for row in visible] == ["ERR111"]
    assert visible[0]["process_status"] == "COMPLETED"
    headers = page.eval_on_selector_all("#readsGrid th", "els => els.map((e) => e.innerText)")
    assert any("rocess status" in h for h in headers)


def test_confirmation_grids_explain_when_nothing_was_submitted(page):
    """Refreshing before a submission must not leave a header-only grid."""
    for tab, grid, empty in (
        ("Studies", "studyGrid", "studyGridEmpty"),
        ("Samples", "sampleGrid", "sampleGridEmpty"),
        ("Reads", "readsGrid", "readsGridEmpty"),
    ):
        page.click(f"a.vf-tabs__link:has-text('{tab}')")
        page.locator(f"#vf-tabs__section--{tab.lower()} button:has-text('Refresh from ENA')").click()
        assert page.evaluate(f"() => document.getElementById('{grid}').style.display") == "none"
        assert "submitted in this session" in page.locator(f"#{empty}").inner_text()


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
    page.click("a.vf-tabs__link:has-text('Reads')")
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
    page.click("a.vf-tabs__link:has-text('Reads')")
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


def test_ena_browser_element_registered(page, live_server_url):
    """The vendored bundle is served and defines the custom element."""
    resp = page.request.get(f"{live_server_url}/static/vendor/ena-browser/ena-browser.iife.js")
    assert resp.status == 200
    assert page.evaluate("() => !!window.customElements.get('ena-browser')")
