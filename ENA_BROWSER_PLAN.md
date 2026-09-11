# Adopting `ena-browser` across the assistant

Step-by-step plan for an agent. Every record grid in this app moves onto the
[`ena-browser`](https://github.com/EBI-Metagenomics/ena-browser) custom element,
and the Records tab embeds it directly.

**Read first, before touching anything:**

1. `ena-browser/README.md` §3 (the public API), §4 (data sources), §5 (theming).
2. This repo's `CLAUDE.md` (the Playwright rule) and README "Record grids
   (ena-browser)".

Clone `ena-browser` somewhere scratch and keep it open; this plan describes the
integration without reproducing that repository.

---

## 0. Ground truth as of writing

| Fact | Value | Where |
|---|---|---|
| `ena-browser` latest tag | **v0.1.1**, release assets `ena-browser.iife.js` + `ena-browser.css` | GitHub releases |
| `ena-submission-toolkit` pin here | **v0.1.3** | `pyproject.toml` |
| Toolkit tag with `attr:` checklist columns + attribute editing | **v0.1.4** (`ATTRIBUTE_PREFIX`, `_apply_attribute_change`) | absent in v0.1.3 |
| Records endpoints today | `GET /api/records/<entity>` returns a **bare JSON list**; `POST /api/records/action` | `server/views_records.py` |
| Records rendering today | `renderRecordsWithActions()` builds an HTML string | `server/static/records.js` |
| Pairing samples list today | `renderReadSampleList()` builds `.sample-item` buttons | `server/static/reads.js:178` |
| Confirmation tables today | `renderTable()` / `renderSubmissionResult()` into `#studyOut`, `#sampleOut`, `#readsResults` | `server/static/core.js` |
| Theme | **light only**, no `data-theme`, no toggle | `server/static/index.html:11` |

Four integration collisions need handling. Each has a phase below; do not discover
them at Phase 3.

- **A. Keyboard is globally swallowed.** `core.js:199-200` calls
  `stopImmediatePropagation()` on every `keydown`/`keyup` at `body`. Handsontable
  mounts its shortcut recorder on `document.documentElement`, so an in-page grid
  gets **no keyboard at all** — no editing, no arrow keys, no Escape. Phase 1.
- **B. Global table CSS.** `index.html:42-45` styles bare `table`/`th`/`td`.
  The element uses light DOM, so those rules repaint Handsontable's own
  `table.htCore`. Phase 1.
- **C. Hidden tabs have no layout.** Grids live inside `.vf-tabs__section`
  elements that are `display:none` until their tab is picked; Handsontable
  measures zero and renders nothing. Phase 1 (a re-render hook).
- **D. Sessions persist `innerHTML`.** `sessions.js` snapshots
  `#recOut`/`#studyOut`/`#sampleOut` innerHTML and restores it. Restoring a
  custom element's serialized innards yields dead DOM. Phase 4.

---

## What lands where

| Concern | Owner after this change |
|---|---|
| Grid rendering, filter/sort/pin/hide/reorder, status toggles, selection, edit tracking, `attr:` columns, theming | **ena-browser** |
| Fetching rows, Webin credentials, TEST/PROD, sessions, the `recLog` debug log, lifecycle action handlers, the reads pairing logic, per-sample file counts | **this app** |
| Listing, MODIFY manifest building + submission, lifecycle actions, editable-column allow-list | **ena-submission-toolkit** (`records.py`), via `server/ena_service.py` |

Nothing ENA-specific gets written in `server/` beyond parameter plumbing. If a
step tempts you to build XML here, stop — it belongs in the toolkit.

---

## Phase 1 — Vendor the element and make the page able to host it

No behaviour change. This phase is purely "an `<ena-browser>` on this page
works".

### 1.1 Vendor the bundle

Add to `Taskfile.yml`:

```yaml
  vendor:ena-browser:
    desc: >
      Download the ena-browser IIFE bundle + CSS at ENA_BROWSER_REF into
      server/static/vendor/ena-browser/. Re-run after bumping the tag.
    vars:
      ENA_BROWSER_REF: v0.1.1
    cmds:
      - mkdir -p server/static/vendor/ena-browser
      - for: ["ena-browser.iife.js", "ena-browser.css"]
        cmd: >
          curl -fsSL -o server/static/vendor/ena-browser/{{.ITEM}}
          https://github.com/EBI-Metagenomics/ena-browser/releases/download/{{.ENA_BROWSER_REF}}/{{.ITEM}}
```

Run it, and **commit the two downloaded files**. This app ships in a Docker image
built by `COPY server/ server/`, and its Playwright suites serve
`server/static/` directly. Committing keeps the image build network-free and
keeps the tests from needing a vendor step. Treat them as build artefacts, like
the DataHarmonizer bundle.

`server/views_core.py` already serves `server/static/` recursively via
`static_serve_view` (`config/urls.py:32`) — verify by requesting
`/static/vendor/ena-browser/ena-browser.css`, do not assume.

### 1.2 Load it

In `server/static/index.html`, `<head>`, **after** the VF stylesheet (so
`ena-browser.css` wins where they overlap) and before the inline `<style>`:

```html
<link rel="stylesheet" href="/static/vendor/ena-browser/ena-browser.css">
```

and as the **first** script, before `core.js`:

```html
<script src="/static/vendor/ena-browser/ena-browser.iife.js"></script>
```

### 1.3 Fix collision A — the keyboard swallower

`core.js:199-200` currently reads:

```js
document.body.addEventListener("keydown", (e) => e.stopImmediatePropagation());
document.body.addEventListener("keyup", (e) => e.stopImmediatePropagation());
```

The comment above it explains why it exists: each DataHarmonizer iframe's
Handsontable attaches a listener to *this* page's `documentElement`, so a
keystroke in an ordinary field can be eaten as a grid shortcut. That reasoning
still holds for every element on the page **except** an `<ena-browser>`, whose
own Handsontable lives in this window and needs those events to reach
`documentElement`. Narrow it:

```js
// An <ena-browser> hosts its own in-page Handsontable, whose shortcut recorder
// also sits on documentElement — so keys typed inside one have to get through.
const _swallowKey = (e) => { if (!e.target.closest("ena-browser")) e.stopImmediatePropagation(); };
document.body.addEventListener("keydown", _swallowKey);
document.body.addEventListener("keyup", _swallowKey);
```

**This is the riskiest single edit in the plan.** Letting these events reach
`documentElement` also exposes them to any DH iframe instance that still thinks
it is listening. Verify both directions in Phase 3's tests: typing in the grid
edits the grid, *and* typing in the grid does not mutate the sample DH grid.
If the second one fails, the fix is to call the DH frames' deactivation path
(the `dataset.userActive = "0"` mousedown handler at `core.js:178`) from the
grid's own focus/mousedown as well — not to revert this edit.

### 1.4 Fix collision B — global table CSS

In `index.html`'s inline `<style>`, scope lines 42-45 to the containers this
app actually renders tables into (`renderTable()` and `renderSubmissionResult()`
always target a `.scroll` div, plus `#runTable` and the dialogs):

```css
.scroll table, #runTable { width:100%; border-collapse:collapse; font-size:12px; margin-top:10px; }
.scroll th, .scroll td, #runTable th, #runTable td { text-align:left; padding:6px 8px; border-bottom:1px solid var(--line); }
.scroll th, #runTable th { color:var(--muted); font-weight:600; }
```

Grep for every `renderTable(`/`renderSubmissionResult(` call site and confirm
each target container carries `class="scroll"` (or a table id covered above)
before committing this.

### 1.5 Fix collision C — a re-render hook

The element exposes no `render()`. `setRows()` re-renders, so:

```js
// core.js
function redrawGrids(root = document) {
  root.querySelectorAll("ena-browser").forEach((g) => {
    if (g.getRows && g.offsetParent !== null) g.setRows(g.getRows());
  });
}
```

Call it from:
- the VF tab-change path — add a `click` listener on `.vf-tabs__link` that calls
  `setTimeout(redrawGrids, 0)` (VF's `scripts.js` owns the switch; run after it);
- the end of `togglePanelMax()` in `core.js`, likewise deferred.

`ponytail:` this is a repaint-by-reset. If it ever costs measurably on a large
account, ask `ena-browser` for a public `redraw()` and use that instead.

### 1.6 Fix theming

The element defaults to `theme="auto"`, which resolves from the nearest
ancestor's `data-theme` and falls back to the OS `prefers-color-scheme`. This
page defines only a light palette and stamps no `data-theme`, so a user with a
dark OS gets a dark grid inside a white page.

Minimum fix — one attribute in `index.html`:

```html
<html lang="en" data-theme="light">
```

The element then inherits this app's `--bg`/`--panel`/`--line`/`--fg`/`--muted`/
`--accent` custom properties, which already have the names it looks for. Do not
set `theme="light"` on each element: that pins them and would have to be undone
if a dark palette is ever added.

**Out of scope, note it in the README instead:** a real light/dark switch. If
someone wants one later, it is a `:root[data-theme="dark"]` palette block plus a
header `<select>` that writes `data-theme` — the element follows with no extra
wiring, unlike the DH iframes.

### 1.7 Check

- Page loads; `window.customElements.get("ena-browser")` is defined.
- `task test` and `task test:ui` pass unchanged.
- Add one Playwright case in `tests/test_ui.py`:
  `test_ena_browser_element_registered` — asserts the custom element is defined
  and that `/static/vendor/ena-browser/ena-browser.iife.js` returned 200.

---

## Phase 2 — Backend: everything the grids need, in one pass

All in `server/views_records.py`, `server/ena_service.py`, `server/views_core.py`.
Keep the endpoint signatures consistent with the existing Records client and toolkit.

### 2.1 Bump the toolkit pin

`pyproject.toml`: `ena-submission-toolkit … @v0.1.4`. Re-lock (`uv lock`) and
commit `uv.lock`. v0.1.4 is what adds `ATTRIBUTE_PREFIX` (`attr:`-prefixed
checklist columns on a listing) and `_apply_attribute_change` (editing one back).
Without it, Phase 3's checklist-attribute columns and edits do not exist.

Confirm afterwards, from the venv:

```bash
.venv/bin/python -c "from ena_submission_toolkit import records; print(records.ATTRIBUTE_PREFIX, records.editable_columns('samples'))"
```

### 2.2 Widen the listing criteria

`_list_params()` in `views_records.py` currently passes `test`, `status`,
`full_fields`, `max_results`. `records.list_records` (v0.1.4) also takes
`search`, `linked_to`, `unlinked`. Pass them through:

```python
def _list_params(request: HttpRequest) -> dict[str, Any]:
    return {
        "test": request.GET.get("test", "true").lower() != "false",
        "status": request.GET.get("status", "all"),
        "search": request.GET.get("search", "").strip(),
        "linked_to": request.GET.get("linked_to", "").strip(),
        "unlinked": request.GET.get("unlinked") == "true",
        "full_fields": request.GET.get("full_fields", "false").lower() == "true",
        "max_results": int(request.GET.get("max_results", 5000)),
    }
```

Mirror the new kwargs on `ena_service.list_records`, which is a pass-through
wrapper. `full_fields` stays **off** by default because this app's
`study_list`/`sample_list` endpoints share `_list_params` and
the pairing panel does not need ~200 Portal columns to pick a sample.

Keep the response shape as it is — a bare JSON list. Changing it would touch
every existing caller and test for nothing.

### 2.3 New endpoints

Add to `views_records.py`, routed in `server/config/urls.py`:

| Route | Body | Delegates to |
|---|---|---|
| `POST /api/records/<entity>/fields` | `{accessions: [str]}` | `records.read_editable_fields(creds, entity, accessions, test=)` |
| `POST /api/records/modify/preview` | `{entity, records: [{accession, changes}], test}` | `records.preview_modify_records(..., submission_alias=MODIFY_ALIAS)` |
| `POST /api/records/modify` | same | `records.modify_records(..., submission_alias=MODIFY_ALIAS)` |

`MODIFY_ALIAS = "mimicc-assistant-modify"`, so this app's MODIFYs are
identifiable in ENA.

Follow the file's existing conventions: pydantic request models + `_parse()`,
`webin_creds.from_request`, `JsonResponse`, `ValueError →
400`. Wrap the toolkit calls so an unexpected exception becomes a `502` with
`{"detail": "<type>: <msg>"}` — the UI shows that text verbatim and a Django 500
page would show nothing useful.

Route these through `server/ena_service.py` (thin wrappers) rather than importing
`ena_submission_toolkit` into a view: every other ENA call in this app already
goes through `ena_service`, and the tests patch it there.

**Do not** reuse `submit_study`/`submit_sample` with `modify=True` for this. A
MODIFY replaces the whole object; a document rebuilt from a Reports row silently
drops everything ENA holds but does not report. `modify_records` fetches the
record's current XML and patches it — that is the whole point.

### 2.4 Health

`views_core.health()` gains:

```python
"editable_columns": {e: ena_service.editable_columns(e)
                     for e in ("studies", "samples", "runs", "experiments", "analyses", "files")},
"ena_browser_available": (STATIC_DIR / "vendor" / "ena-browser" / "ena-browser.iife.js").is_file(),
```

The server is the authority on what can be edited — it is what builds the XML.
The page reads this once at boot (`refreshHealth()` already stores it in
`HEALTH`) and never hard-codes an allow-list.

### 2.5 A write lock

Gate writes server-side. This app exists to submit, so a global read-only default
would be wrong. Instead gate only the two MODIFY endpoints on an explicit
UI opt-in — a `write` checkbox on the Records tab (Phase 3) — and keep
destructive lifecycle actions confirming as they already do. Record the decision
in a comment; do not add an env var nobody sets.

### 2.6 Check

`tests/test_server.py`, following the existing patch style in `tests/conftest.py`:

- `GET /api/records/samples?search=foo&linked_to=PRJEB1&unlinked=true` reaches
  `ena_service.list_records` with those exact kwargs.
- `POST /api/records/modify/preview` returns the toolkit's results verbatim and
  submits nothing (assert the submit path was never called).
- `POST /api/records/modify` calls `modify_records` with `submission_alias`.
- `POST /api/records/<entity>/fields` returns `{"fields": {...}}`.
- `GET /api/health` carries `editable_columns` and `ena_browser_available`.

`task test` is green before any frontend work starts.

---

## Phase 3 — Embed `ena-browser` in the Records tab

This is the biggest phase. Implement the Records-tab behavior in this app's idioms:
`$()`, `api()`, `banner(id, ok, msg)`, VF markup, and `appendLog("recLog", …)`.

### 3.1 Markup

In the Records section of `index.html`, replace the entity/status/fetch row and
`<div class="scroll" id="recOut">` with:

- **Fetch criteria row** (excluding the out-of-scope Portal "all of ENA" source):
  `#recEntity` (keep), `#recSearch`, `#recLinked`, `#recUnlinked`,
  `#recFullFields` (keep), `#recStatus` (keep — it is a *request* criterion the
  Reports API answers, distinct from the element's client-side status toggles),
  `#recClear`, and the Fetch button.
- A `write mode` checkbox `#recWrite`, unchecked by default.
- `<ena-browser id="recGrid" entity="studies" mode="read" height="600"></ena-browser>`.
- Three VF panels below the grid:
  **MODIFY manifests** (`#recManifestPanel`, `#recGenerate`, `#recManifestState`,
  `#recManifests`, `#recManifestEmpty`), **Submission log** (`#recSubmitLog`),
  and the existing **Debug log** panel (keep `#recLog` exactly as it is — it is
  how a blank linking-accession column gets diagnosed).
- A `#recSubmit` button and the confirm `<dialog id="recDiffDialog">`.

**Out of scope, and say so in the README:** the Portal "all of ENA (read-only)"
source, undo/redo (`getState`/`setState` stack), and the change-history stack.
They do not serve a submission workflow and are independently addable later.

### 3.2 `server/static/records.js`

Delete `RECORD_COLUMNS` and `renderRecordsWithActions()`. The element's
`entities.ts` owns per-entity defaults and appends unknown data keys —
which the current fixed list silently drops.

Keep `appendLog`/`clearLog` and `recAction()`'s prompt/confirm behaviour.

Implement the following in `server/static/records.js`:

- `ATTRIBUTE_PREFIX` / `attributeColumnsIn()` / `attributeColumnSpecs()` — the
  `attr:` columns arrive **visible**, unlike Portal extras.
- `editableFor()` / `canEdit()` / `editableColumnsNow()` / `applyMode()`, with
  `EDITABLE = HEALTH.editable_columns` and `WRITE = $("recWrite").checked`.
- The manifest gate: `MANIFESTS`, `MANIFEST_KEY`, `changeKey()`,
  `manifestsReady()`, `refreshSubmitButton()`, `renderManifestState()`,
  `renderManifests()`, and the `#recGenerate` handler. **Do not skip this** —
  submit stays locked until the exact XML for the current edits has been built
  and shown, and any further edit re-locks it.
- `withEditableFields()` → `POST /api/records/<entity>/fields`, called only in
  write mode (a run's title and an experiment's library/instrument are not in
  the Reports answer, so there is otherwise no cell to edit them in).
- `pendingChanges()`, `showDiff()`, the `#recSubmit` handler, `logSubmission()`.

Rewrite `loadRecords()` around the element:

```js
async function loadRecords() {
  const entity = $("recEntity").value;
  const query = recCriteriaQuery();          // test=…&status=…&search=…&linked_to=…&unlinked=…&full_fields=…
  appendLog("recLog", `Fetching ${entity} (${query})…`);
  $("recGrid").applyConfig({ entity, mode: "read", rowActions: ROW_ACTIONS });
  try {
    let rows = await api(`/api/records/${entity}?${query}`);
    appendLog("recLog", `Got ${rows.length} ${entity} row(s).`);
    if (rows.length) {
      const keys = [...new Set(rows.flatMap((r) => Object.keys(r)))];
      appendLog("recLog", `Fields present: ${keys.join(", ")}`);
      appendLog("recLog", `First row: ${JSON.stringify(rows[0])}`);
    }
    ATTRIBUTE_COLUMNS = attributeColumnsIn(rows);
    if (canEdit()) rows = await withEditableFields(rows);
    $("recGrid").applyConfig({ columns: attributeColumnSpecs() });  // BEFORE setRows
    applySavedGridLayout("records", entity);                        // Phase 4
    $("recGrid").setRows(rows);
    clearManifests();
    applyMode();
    banner("recBanner", true, `${rows.length} ${entity}.`);
    scheduleSave();
  } catch (e) {
    appendLog("recLog", `ERROR: ${e.message}`);
    banner("recBanner", false, e.message);
  }
}
```

`applyConfig({columns})` **before** `setRows()` is not cosmetic: a column the
grid first meets in the data arrives hidden and that sticks in the layout.

Wire the events once, at file scope:

```js
$("recGrid").addEventListener("ena-browser:row-action", (e) =>
  recAction(e.detail.action, e.detail.row?.accession || e.detail.key));
$("recGrid").addEventListener("ena-browser:change", () => refreshSubmitButton());
$("recGrid").addEventListener("ena-browser:error", (e) => banner("recBanner", false, e.detail.message));
for (const name of ["filter-change", "layout-change"]) {
  $("recGrid").addEventListener(`ena-browser:${name}`, (e) => {
    if (e.detail.source !== "api") scheduleSave();
  });
}
```

Existing `loadRecords('studies','studyOut')` call sites on the Studies and
Samples tabs are removed in Phases 5 and 6 — until then, keep a thin shim so
nothing breaks mid-series, and delete it in Phase 6.

`recAction()` keeps its current body but re-fetches on success: the status column
is how a user sees a release worked.

### 3.3 Check

Extend `tests/test_ui.py` (`live_server_url` already fakes
`ena_service.list_records` for studies/samples/runs/experiments):

- Records tab renders `ena-browser#recGrid`; after Fetch,
  `page.evaluate("document.getElementById('recGrid').getRows().length")` is
  non-zero, and `getRows()[0].experiment_accession` is `ERX111` for runs.
  Assert through the public API, **never** on grid internals.
- Rewrite `test_records_runs_and_experiments_views` accordingly — it currently
  asserts on `#recOut thead`/`tbody`.
- Row action: click a `release` button, assert `POST /api/records/action` fired
  (stub the route with Playwright) with the right accession.
- The manifest gate: with `#recWrite` on, edit a cell, assert `#recSubmit` is
  disabled; click Generate; assert it enables; edit again; assert it re-locks.
- Collision A regression: type into a grid cell and assert the value lands.
- **Do not re-test the grid itself.** Filtering, sorting, pinning and selection
  mechanics are `ena-browser`'s own Playwright suite. Test only the wiring.

Anything needing the real bundle behaviour in a Docker context goes to
`tests/test_compose_ui.py` per `CLAUDE.md`.

---

## Phase 4 — Session persistence

`sessions.js` today snapshots `innerHTML` for `_RESULT_IDS = ["studyPrepOut",
"studyOut", "prepOut", "sampleOut", "recOut", "readsResults"]`. Every id in that
list that becomes an `<ena-browser>` must leave it.

1. Remove `"recOut"` now; remove `"studyOut"` and `"sampleOut"` in Phases 5-6.
   `"studyPrepOut"`, `"prepOut"` and `"readsResults"` stay — they are receipt
   tables, not record grids.
2. Add to `collectState()`:

```js
grids: {
  records:  { entity: $("recEntity").value, layout: $("recGrid").getLayout(),
              filters: $("recGrid").getFilters() },
  pairing:  { layout: $("pairSamples")?.getLayout() },
  studyOut: { layout: $("studyGrid")?.getLayout() },
  sampleOut:{ layout: $("sampleGrid")?.getLayout() },
},
```

3. On restore, apply layout/filters **before** the rows are re-fetched
   (`applySavedGridLayout(key, entity)` used above), and re-fetch rather than
   restoring rows.
4. **Never persist row data.** A restored row shows a stale status — after a
   release or a suppress, a wrong one.
5. `resetToBlank()` must call `clearSelection()` and `setRows([])` on every
   grid, alongside the existing `RUN_ROWS = []` / `SELECTED_SAMPLE = ""`.
6. Bump the state blob's `v: 1` to `v: 2` and ignore `resultsHtml` entries for
   ids that no longer exist — an old session must not throw.

**Check:** open a session, fetch records, pin and reorder a column, add a filter,
switch session and back — the layout returns, the rows are re-fetched, and no
stale status is shown. Add a Playwright case for exactly that round trip.

---

## Phase 5 — Studies tab: the records grid at the end

Goal: after "3 · Submit studies", show the submitted studies as they exist in
ENA, **read-only**, limited to the rows in this submission.

1. Markup: keep `<div class="scroll" id="studyOut">` — it is the *receipt*
   (including failures), which is not the same thing as what ENA now holds.
   Add **below** it:

```html
<h3 class="log-heading">In ENA</h3>
<ena-browser id="studyGrid" entity="studies" mode="read" height="360"></ena-browser>
<p class="muted" id="studyGridEmpty">Submit studies (or click <b>Refresh from ENA</b>) to see them here.</p>
<button class="vf-button vf-button--secondary" onclick="refreshStudyGrid()">Refresh from ENA</button>
```

2. `samples.js` — replace `loadRecords('studies','studyOut')` with:

```js
// The manifest's accessions: what this session actually submitted. A study
// with no accession never reached ENA, so it has nothing to confirm.
function submittedStudyAccessions() {
  const submitted = (window.__lastStudySubmitResponse?.accessions || []).map((r) => r.accession);
  const prepared = (window.__preparedStudies || []).map((r) => r.accession);
  return [...new Set([...submitted, ...prepared])].filter(Boolean);
}

async function refreshStudyGrid() {
  const keep = submittedStudyAccessions();
  const grid = $("studyGrid");
  $("studyGridEmpty").style.display = keep.length ? "none" : "block";
  if (!keep.length) { grid.setRows([]); return; }
  try {
    const rows = await api(`/api/records/studies?test=${TEST}&status=all`);
    grid.applyConfig({ entity: "studies", mode: "read", selectionMode: "none", rowActions: [] });
    grid.setRows(rows);
    grid.setFilters([{ column: "accession", operator: "in", values: keep }]);
  } catch (e) { banner("studyBanner", false, e.message); }
}
```

   Call it at the end of `submitStudies()` on success, and from the button.

3. Read-only means read-only: `mode: "read"`, no `rowActions`, no
   `editableColumns`. Lifecycle actions and edits belong on the Records tab —
   one place where records change, not three.

**Check:** Playwright — submit studies against the faked service, assert
`studyGrid.getVisibleRows().length` equals the number of submitted accessions
while `getRows().length` is the full account listing, and that
`studyGrid.getAttribute("mode") === "read"`.

---

## Phase 6 — Samples tab: the same, for samples

Identical shape to Phase 5 — the three submission grids are parallel, and per
`CLAUDE.md` a test for one has an analog for the others.

1. Markup after `#sampleOut`: `<ena-browser id="sampleGrid" entity="samples"
   mode="read" height="360">`, `#sampleGridEmpty`, a **Refresh from ENA** button.
2. `refreshSampleGrid()` mirrors `refreshStudyGrid()`, filtering on the
   accessions in `r.accessions` from `/api/sample/submit` unioned with any
   accession carried by `window.__prepared`.
3. Delete the `loadRecords('samples','sampleOut')` button and the Phase-3 shim.
4. `submitSamples()` already does `READ_SAMPLES = r.accessions; SELECTED_SAMPLE = ""`
   — leave that; Phase 7 hangs the pairing grid refresh off the same place.

**Check:** the Phase-5 test, for samples.

---

## Phase 7 — Reads tab: the pairing panel

The samples side of read↔sample pairing becomes a grid with real search,
filter and sort, which is the point: picking the right sample out of hundreds
by eye is what the `.sample-item` list is bad at.

1. Markup — in `.assign-grid`, replace the `#readSampleList` div with:

```html
<ena-browser id="pairSamples" entity="samples" mode="read"
             selection-mode="single" height="380"></ena-browser>
```

   Add a small criteria row above it, reusing Phase 2's parameters, so the user
   can narrow to the right cohort before pairing: a free-text `#pairSearch`, a
   `#pairLinked` ("linked to accession, e.g. the study you just created"), and
   the existing **Load samples** button. These are criteria on the *fetch*; the
   element's own per-column filters and sort handle everything client-side after
   that, and need no code here.

   CSS: `.assign-grid > .scroll, .sample-list { height: var(--assign-table-height) }`
   at `index.html:66` and the `.panel.maximized` rules at lines 93-95 key off
   `.sample-list`. Replace that selector with `#pairSamples` in all of them, and
   delete the `.sample-item` / `.sample-count` rules (lines 68-71).

2. `reads.js` — delete `renderReadSampleList()` and `sampleLabel()`. **Keep**
   `sampleAccession()`, `rowFileCount()` and `sampleAssignmentCount()`: the run
   rows use them and computing how many files a sample has is this app's job.

```js
async function loadReadSamples() {
  const params = new URLSearchParams({ test: TEST, status: "all" });
  if ($("pairSearch").value.trim()) params.set("search", $("pairSearch").value.trim());
  if ($("pairLinked").value.trim()) params.set("linked_to", $("pairLinked").value.trim());
  try {
    READ_SAMPLES = await api(`/api/records/samples?${params}`);
    const grid = $("pairSamples");
    grid.applyConfig({
      entity: "samples", mode: "read", selectionMode: "single",
      customColumns: [{ name: "reads_assigned", title: "Reads", type: "numeric",
                        pinned: true, render: "badge" }],
    });
    grid.setRows(READ_SAMPLES);
    if (SELECTED_SAMPLE) grid.setSelection([SELECTED_SAMPLE]);
    refreshAssignedCounts();
    banner("readsBanner", true, `Loaded ${READ_SAMPLES.length} sample(s).`);
  } catch (e) { banner("readsBanner", false, e.message); }
}

// The count is derived from RUN_ROWS, so it must follow every mutation of them.
// setCustomValues patches cells in place — no re-sort, no lost selection.
function refreshAssignedCounts() {
  const map = {};
  READ_SAMPLES.forEach((s) => {
    const acc = sampleAccession(s);
    if (acc) map[acc] = sampleAssignmentCount(acc);
  });
  $("pairSamples")?.setCustomValues("reads_assigned", map);
}
```

3. Selection bridge — the *only* place `SELECTED_SAMPLE` is now set by a click:

```js
$("pairSamples").addEventListener("ena-browser:selection-change", (e) => {
  SELECTED_SAMPLE = e.detail.lastKey || "";
  renderRunTable();   // re-applies the .assignable affordance
});
```

   The run-table click handler (`RUN_ROWS[i].SAMPLE = SELECTED_SAMPLE`) is
   unchanged — pairing logic stays here, not in the element.

4. Replace **every** `renderReadSampleList()` call with `refreshAssignedCounts()`:
   `importPairingsTsv()`, `suggestSamples()`, `renderRunTable()` (its tail, and
   the row-delete and SAMPLE-input handlers inside it), `sessions.js:237` and
   `sessions.js:279`. Grep for the name; there must be no survivors.
5. `samples.js:submitSamples()` and `sessions.js:232`/`:275` set
   `SELECTED_SAMPLE` directly. Make each also call the grid's
   `clearSelection()` / `setSelection([SELECTED_SAMPLE])` so the variable and the
   UI cannot drift apart.
6. `loadReadSamples()` no longer needs `/api/sample/list` — leave that endpoint
   in place (tests use it), but stop calling it from the page.

**Check:** rewrite `test_reads_sample_assignment_and_row_delete`:

- Load samples; assert `pairSamples.getRows().length === 2`.
- Select `ERS111` via `setSelection(["ERS111"])` **and** via a real click on its
  row; assert `SELECTED_SAMPLE` in both cases.
- Click a run row; assert that row's `SAMPLE` input reads `ERS111` and that
  `pairSamples` reports `reads_assigned` of 2 for it (read it back through
  `getRows()` plus the element's custom-value map, or assert the rendered badge
  text — not internals).
- Delete the run row; assert the badge drops to 0.
- Filter the grid to one sample and assert the selection survives.

---

## Phase 8 — Reads tab: the records grid at the end

After "4 · Submit reads", the same confirmation treatment as Studies and Samples,
for the runs just submitted.

1. Markup after `#readsResults`: `<ena-browser id="readsGrid" entity="runs"
   mode="read" height="360">` plus a **Refresh from ENA** button.
2. `refreshReadsGrid()` fetches `/api/records/runs?test=…&status=all` and filters
   on the `run_accession` values in the submit results (union with `READS_RUNS`,
   the resume ledger, so a resumed batch shows its earlier runs too):
   `setFilters([{ column: "accession", operator: "in", values: keep }])`.
3. Call it at the end of `submitReads()`, in both the success and partial-failure
   paths — a partly failed batch is exactly when someone wants to see what
   actually landed.
4. Run rows carry `process_status` / `process_date` / `process_error` from the
   toolkit's run-processing report: whether ENA has finished *archiving* the read
   files, which registering a run does not say. They are default run columns in
   the element — no work here beyond not hiding them. Mention them in the
   README; "submitted" and "archived" being different things is the single most
   useful thing this grid tells a reads submitter.

**Check:** Playwright — after a faked reads submit, `readsGrid.getVisibleRows()`
is limited to the submitted run accessions and shows a `process_status` column.

---

## Phase 9 — Documentation and cleanup

1. **README** — "Record grids (ena-browser)" already describes the intent;
   update it to describe what now exists: four grids (Records, Studies, Samples
   confirmation, Reads pairing + confirmation), the manifest gate, the write-mode
   checkbox, `attr:` checklist columns, `process_status`, and the two things
   deliberately not ported (the Portal "all of ENA" source, undo/redo +
   change-history). Note that the app is light-only and how to add a dark palette.
2. **README "Pinned dependency versions"** — add an `ena-browser` row: pin
   `v0.1.1`, held in `Taskfile.yml` (`ENA_BROWSER_REF`), vendored into
   `server/static/vendor/ena-browser/` and **committed**; refresh with
   `task vendor:ena-browser`. Update the `ena-submission-toolkit` row to v0.1.4.
3. **CLAUDE.md** — add a line to the testing rule: grid *mechanics* are
   `ena-browser`'s suite; tests here assert the wiring through the element's
   public API (`getRows`, `getVisibleRows`, `getSelection`, `getChangeSet`),
   never on Handsontable internals or `.ht*` classes.
4. **Delete** `renderRecordsWithActions`, `RECORD_COLUMNS`,
   `renderReadSampleList`, `sampleLabel`, the `.sample-item`/`.sample-count`
   CSS, and the `loadRecords(entity, outId)` shim. Record what was deleted in
   the commit message, not in comments.
5. `renderTable()` and `renderSubmissionResult()` **stay** — they render receipts
   and prepare-output, which are not ENA records.
6. Re-run everything: `task test`, `task test:ui`, and `task test:compose`
   (slow, but it is the only suite that exercises the real DH bundle alongside
   the new in-page Handsontable — collision A's regression risk lives exactly
   there).

---

## Rollback

Phases are independently revertable and each is one commit:

- 1 is additive (a bundle, a link tag, three defensive fixes) and safe to keep
  even if everything after it is reverted.
- 2 is additive backend surface with no caller until 3.
- 3, 5, 6, 7, 8 are each a self-contained swap of one DOM node plus its render
  function; reverting one does not affect the others.
- 4 is the only cross-cutting one; it must land with or after 3.

Do not leave deleted render functions commented out in the files.

---

## Sequencing note for the agent

Phases 1, 2 and 4 are infrastructure — they have no visible payoff and are the
ones most likely to get skipped under pressure. Do them anyway and in order. The
grid work (3, 5-8) is mechanical once they are done, and impossible to debug if
they are not: a grid that renders nothing is usually collision C, and a grid that
will not accept a keystroke is always collision A.
