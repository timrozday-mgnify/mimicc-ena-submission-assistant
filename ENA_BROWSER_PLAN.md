# Adopting `ena-browser` in the assistant

Step-by-step plan for an agent to replace this app's hand-rolled record tables
with the [`ena-browser`](https://github.com/timrozday-mgnify/ena-browser)
element once that repo has cut a tag. Read `ena-browser/README.md` (the API
contract) first.

**Prerequisite:** `ena-browser` v0.1.0 tagged, with `dist/ena-browser.iife.js`
and `dist/ena-browser.css` published as release assets. Do not start otherwise —
none of the steps below can be verified without the build.

---

## What changes

| Today | After |
|---|---|
| `server/static/records.js` → `renderRecordsWithActions()` builds an HTML `<table>` string; no filtering, no sorting, no editing | `<ena-browser>` in `mode="edit"`, per-column filters/sort/pin/reorder, change set feeding an ENA MODIFY submission |
| Records tab's `Status` `<select>` (all/private/public) | The element's status toggles (include/exclude cancelled + suppressed) plus any per-column filter; the `<select>` goes away |
| `server/static/reads.js` → `renderReadSampleList()` builds `.sample-item` buttons with a `sample-count` tag | `<ena-browser>` in `selection-mode="single"` with a pinned `reads_assigned` custom column updated via `setCustomValues()` |
| `core.js` `SELECTED_SAMPLE` set by a button's `onclick` | `SELECTED_SAMPLE` set from the `ena-browser:selection-change` event's `lastKey` — the rest of the pairing flow (`renderRunTable`'s row click) is unchanged |
| `renderTable()` in `core.js` used for every result table | Unchanged. It stays for prepare/submit result tables; only the *record* tables move |

---

## What stays in this app

Do not push any of these into `ena-browser`:

- Webin credentials, the test/production switch, and every `fetch` to
  `/api/records/*`. The element receives rows via `setRows()`.
- The `recLog` debug log (fetch counts, field names present, first raw row) —
  keep it; it is how a blank linking-accession column gets diagnosed.
- Lifecycle actions (release / hold / suppress / cancel). The element renders
  the buttons if configured with `rowActions` and emits `ena-browser:row-action`;
  `recAction()` in `records.js` still does the work.
- Building and submitting the MODIFY manifest — that goes to
  `ena-submission-toolkit` via a new endpoint (Phase 4).
- Session persistence. Layout, filters and the loaded rows are saved into the
  existing IndexedDB session blob by `sessions.js`.
- Computing how many read files a sample has (`sampleAssignmentCount()`), and
  everything about the run rows.

---

## Phase 1 — Vendor the build

1. Download the release assets to `server/static/vendor/ena-browser/`
   (`ena-browser.iife.js`, `ena-browser.css`) and commit them, the same way the
   DataHarmonizer bundle is treated as a build artefact rather than a source
   dependency.
2. Add to `server/static/index.html`, **before** the app scripts:

```html
<link rel="stylesheet" href="/static/vendor/ena-browser/ena-browser.css">
<script src="/static/vendor/ena-browser/ena-browser.iife.js"></script>
```

3. Confirm `server/views_core.py`'s static serving covers the new subdirectory
   (it serves `server/static/` recursively — verify, don't assume).
4. Add an "ena-browser" entry to the README's **Pinned dependency versions**
   section recording the tag the vendored files came from.

**Check:** the page loads, `window.customElements.get("ena-browser")` is defined,
nothing else changed. Existing Playwright tests still pass.

---

## Phase 2 — Records tab, read-only

Smallest useful change first: same data, better grid, no editing yet.

1. In `index.html`, replace `<div class="scroll" id="recOut"></div>` with:

```html
<ena-browser id="recGrid" entity="studies" mode="read" height="520"></ena-browser>
```

   and delete the `Status` `<select>` (`recStatus`) — the element owns status
   filtering now. Keep the entity `<select>` and the Fetch button; they choose
   *what to fetch*, which is this app's job.
2. In `records.js`:
   - Delete `renderRecordsWithActions()`.
   - `loadRecords()` keeps its fetch and its debug logging, then does:

```js
const grid = $("recGrid");
grid.config = {
  entity,
  mode: "read",
  rowActions: [
    { action: "release", label: "release" },
    { action: "hold", label: "hold" },
    { action: "suppress", label: "suppress" },
    { action: "cancel", label: "cancel", variant: "danger" },
  ],
};
grid.setRows(rows);
```

   - Wire the action bridge once, at load:

```js
$("recGrid").addEventListener("ena-browser:row-action", (e) => {
  recAction(e.detail.action, e.detail.key);
});
```

   `recAction()` is unchanged — it already prompts for the hold date and
   confirms destructive actions.
3. Drop `RECORD_COLUMNS` from `records.js`; the element's `entities.ts` owns the
   per-entity column defaults, and it appends unknown data keys automatically
   (which the current fixed list silently drops).

**Check:** fetch each entity; columns match what the old table showed plus any
extra Reports fields; filter and sort work; release/hold/suppress/cancel behave
exactly as before.

---

## Phase 3 — Persist layout and filters

1. `sessions.js` — extend the saved state blob with
   `recordsGrid: { entity, layout, filters, sort }`, written from the element's
   `ena-browser:layout-change` and `ena-browser:filter-change` events through
   the existing `scheduleSave()` debounce.
2. On session load, apply with `setLayout()` / `setFilters()` / `setSort()`
   before `setRows()`.
3. Do **not** persist the row data — records are re-fetched from ENA. Persisting
   them would show stale statuses after a lifecycle action.

**Check:** pin and reorder columns, add a filter, reload the page, reopen the
session — the layout comes back, the rows are re-fetched.

---

## Phase 4 — Records editing → MODIFY submission

This is the only phase that touches the backend.

1. Flip the Records grid to `mode: "edit"` with an explicit
   `editableColumns` list per entity — start with the fields ENA actually
   accepts on a MODIFY (`alias`, `title`, and the sample attribute columns for
   samples). Everything else stays locked; accessions and `status` are never
   editable.
2. Add a "Submit changes" button under the grid, enabled only when
   `getChangeSet().rows.length > 0` (listen to `ena-browser:change`).
3. New endpoint `POST /api/records/modify` in `server/views_records.py`:
   - Pydantic request model `{ entity, records: list[dict], test: bool }` where
     each record is the element's `after` object plus its accession.
   - Delegate straight to `ena_submission_toolkit.records.modify_records()`,
     which already does this generically for every entity: fetch the record's
     current XML from the ENA Browser API, patch the edited fields into it,
     resubmit as a MODIFY. Do **not** route this through the
     `submit_study` / `submit_sample` builders with `modify=True` — a MODIFY
     replaces the whole object, and a document rebuilt from a Reports row
     silently drops everything ENA holds but does not report (a study's
     description, a sample's attributes).
   - `records.editable_columns(entity)` is the per-entity allow-list to feed
     the grid's `editableColumns`; it is small on purpose (alias/title, alias
     only for runs, nothing for files). Widening it is an entry in
     `records._EDITABLE` plus a test, in the toolkit.
4. On success: `clearChanges()`, then re-fetch so the grid shows ENA's state
   rather than the optimistic local one.
5. On failure: surface the receipt messages in `recBanner` and `recLog`, and
   **leave the change set intact** so the user can fix and retry.

**Check:** edit an alias against ENA test, submit, confirm the receipt, confirm
the re-fetch shows the new value; edit-then-revert produces no submission; a
rejected submission keeps the edits.

---

## Phase 5 — Pairing panel (samples side)

1. In `index.html`, replace the `#readSampleList` div with:

```html
<ena-browser id="pairSamples" entity="samples" mode="read"
             selection-mode="single" height="420"></ena-browser>
```

2. In `reads.js`:
   - Delete `renderReadSampleList()`, `sampleLabel()` and the `.sample-item` /
     `.sample-count` CSS (keep `sampleAccession()` — the run rows use it).
   - `loadReadSamples()` becomes:

```js
const grid = $("pairSamples");
grid.config = {
  entity: "samples",
  mode: "read",
  selectionMode: "single",
  customColumns: [{ name: "reads_assigned", title: "Reads", type: "numeric",
                    pinned: true, render: "badge" }],
};
grid.setRows(rows);
refreshAssignedCounts();
```

   - `refreshAssignedCounts()` builds the map from the run rows using the
     existing `sampleAssignmentCount()` and pushes it in:

```js
function refreshAssignedCounts() {
  const map = {};
  READ_SAMPLES.forEach((s) => {
    const acc = sampleAccession(s);
    if (acc) map[acc] = sampleAssignmentCount(acc);
  });
  $("pairSamples").setCustomValues("reads_assigned", map);
}
```

   - Call `refreshAssignedCounts()` wherever `renderRunTable()` is called today
     (scan, suggest, TSV import, row click, submit result) — the count is
     derived from `RUN_ROWS`, so it must follow every mutation of them.
   - Selection bridge:

```js
$("pairSamples").addEventListener("ena-browser:selection-change", (e) => {
  SELECTED_SAMPLE = e.detail.lastKey || "";
  renderRunTable();   // re-applies the .assignable affordance
});
```

   The run-table click handler (`RUN_ROWS[i].SAMPLE = SELECTED_SAMPLE`) is
   unchanged — that logic stays here, not in the element.
   - `sessions.js` restore path: after `SELECTED_SAMPLE` is read back from the
     session, call `setSelection([SELECTED_SAMPLE])` on the grid.
3. `samples.js:65` and `sessions.js:232` currently clear `SELECTED_SAMPLE`
   directly; make them call the grid's `clearSelection()` too so the UI and the
   variable can't drift apart.

**Check:** load samples, click one, click read-group rows — assignment works as
before; the Reads badge updates immediately and stays visible when the grid is
scrolled or filtered; selection survives filtering; the count survives a
session reload.

---

## Phase 6 — Confirmation views after submission

Wherever a submission currently ends with a `renderTable()` of accessions
(studies, samples, reads results), optionally add a read-only
`<ena-browser>` fed by a fresh `/api/records/<entity>` fetch filtered to the
just-submitted accessions (`setFilters([{ column: "accession", operator: "in",
values: [...] }])`). This is the "prove it landed in ENA" use case.

Keep the existing result tables — they show the *receipt* (including failures),
which is not the same thing as what ENA now holds.

---

## Testing

- **Existing tests first.** `tests/test_ui.py` and `tests/test_compose_ui.py`
  drive the current DOM. Every phase that changes `index.html` must update them
  in the same commit. `test_page_loads_with_tabs` and `test_tab_switching` are
  unaffected; anything asserting on `#recOut`, `#recStatus` or `.sample-item`
  is not.
- **New Playwright tests** in `tests/test_ui.py`:
  - Records tab renders an `ena-browser` element and populates it (assert via
    `page.evaluate` on `getRows().length`, not on grid internals).
  - The status toggles change the visible row count.
  - Pairing: select a sample in the grid, click a run row, assert the run row's
    `SAMPLE` and the `reads_assigned` badge both updated.
  - Layout persistence across a session reload.
- **Do not re-test the grid itself** — filtering, sorting, pinning and selection
  mechanics are `ena-browser`'s Playwright suite. Test only the wiring.
- **Backend:** `tests/test_server.py` gains a case for
  `POST /api/records/modify` with the ENA client patched (follow the existing
  patching pattern in `tests/conftest.py`), asserting the toolkit is called with
  `modify=True`.

---

## Rollback

Each phase is independently revertable: Phases 2 and 5 are self-contained
swaps of one DOM node plus its render function. Keep the deleted render
functions in the commit message, not commented out in the file.
