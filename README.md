# MIMICC ENA Submission Assistant

A single local web app for submitting **studies**, **samples**, and **sequencing
reads** to the European Nucleotide Archive (ENA) for the
[MIMICC](../ena-submission-dataharmonizer/mimicc) project.

It ties together three existing tools:

| Concern | Reused from | How |
|---|---|---|
| Create/modify/list/delete **studies & samples** | [`ena-api-client`](../ena-api-client) + [`ena-submission-dataharmonizer`](../ena-submission-dataharmonizer) `scripts/` | `WebinClient` REST submission + the `submit_study`/`submit_sample` batch builders |
| Enter **sample metadata** | [DataHarmonizer](../DataHarmonizer) | embedded spreadsheet UI (Samples tab) → export → filter/rename → submit |
| Submit **reads** | [`webin-cli-browser-assistant`](../webin-cli-browser-assistant) | `enasequence/webin-cli` in a Docker sibling container, logs streamed over SSE |

New glue added here:

- **Read-to-sample pairing** — scan a reads folder, auto-suggest the sample for
  each FASTQ group by filename, export/import the pairing as TSV; experiment
  metadata (platform, instrument, library source/selection/strategy, …) is
  entered separately via its own embedded DataHarmonizer panel (see
  "Experiment metadata schema" below), kept in sync with the pairings; build
  webin-cli manifests, submit.
- **DH → submission pipeline** — filter a DataHarmonizer export to sample fields
  and rename columns to ENA field names (the `submit_mimicc_samples.sh` flow).
- **Account records browser** — list studies/samples/runs/experiments and run
  lifecycle actions (release/hold/suppress/cancel).

Everything runs against ENA **test** by default; a header toggle switches to
**production** (with a confirm). Webin credentials live in server memory only.

## Architecture

```
browser (single-page UI, SSE)
        │
   FastAPI server (server/main.py)
   ├── ena_service.py ── ena-api-client + submit_study/submit_sample (REST/XML)
   ├── read_assign.py ── scan / suggest / manifest build
   ├── webin_runner + webin_cli_lib ── docker run enasequence/webin-cli  (reads)
   └── dh_builder_runner + dh_builder_lib ── docker run mimicc-dh-builder  (DH bundle rebuild)
        │ (docker.sock)
   enasequence/webin-cli / mimicc-dh-builder  (sibling containers)
```

The server runs in Docker and spawns both the webin-cli image and the
`mimicc-dh-builder` image as **sibling containers** via the mounted docker
socket — the webin-cli path is identical to `webin-cli-browser-assistant`;
`mimicc-dh-builder` follows the same pattern for on-demand DataHarmonizer
bundle rebuilds (`POST /api/dh/build` + `GET /api/dh/build/stream/{job_id}`),
currently used for the build-on-demand plumbing rather than a front-end
schema editor.

## Install & run

Prerequisites: Docker Desktop, and a `DataHarmonizer` checkout (default sibling
path `../DataHarmonizer`, override with `DATAHARMONIZER_DIR` in `.env`). Node/Yarn
are **not** required on the host — the Docker build compiles the embedded
DataHarmonizer bundle itself, in a dedicated build stage.

```bash
# 1. Vendor the sibling code into ./vendor (ena-api-client + ena-dh scripts/schemas/XSDs)
bash scripts/vendor.sh

# 2. Put FASTQ/BAM/CRAM files in the reads workspace (default ~/.mimicc-ena/reads),
#    or set MIMICC_READS_DIR. Also pre-create the DH bundle/schema + sessions dirs
#    (bind mounts must exist before `docker compose up` — see MIMICC_DH_BUNDLE_DIR /
#    MIMICC_DH_SCHEMA_DIR / MIMICC_SESSIONS_DIR in .env.example).
mkdir -p ~/.mimicc-ena/reads ~/.mimicc-ena/dh-bundle ~/.mimicc-ena/dh-schema ~/.mimicc-ena/sessions

# 3. Start (this also builds the embedded DataHarmonizer bundle — see
#    "DataHarmonizer bundle build" below)
docker compose up -d --build
open http://localhost:9000
```

If you don't have a `DataHarmonizer` checkout, or want the Samples tab to fall
back to DH export upload instead, see "DataHarmonizer bundle build" below.

If port 9000 is already taken on your machine, set `MIMICC_PORT` in `.env`
(see `.env.example`) to expose the server on a different host port and open
`http://localhost:<MIMICC_PORT>` instead.

Stop with `docker compose down`, or the in-app shutdown endpoint.

### DataHarmonizer bundle build

The Samples tab embeds a built DataHarmonizer bundle (`server/static/dh/`) with
the MIMICC template, carrying the LinkML schema vendored at
`vendor/schemas/mimicc_sample_experiment.yaml`. `docker compose build` produces
this automatically via a `dh-builder` stage in the `Dockerfile` (Node + Yarn +
the `DataHarmonizer` checkout supplied as the `dataharmonizer-src` build
context — see `DATAHARMONIZER_DIR` in `.env.example`). If that checkout isn't
available at build time, the build will fail; remove the `dh-builder` stage's
`COPY --from=dh-builder` line in the final image (or point `DATAHARMONIZER_DIR`
elsewhere) to build without it — the Samples tab still works via DH export
upload either way.

For local non-Docker development, `scripts/build_dh_template.sh` does the same
build directly on the host (requires Node + Yarn there instead). Both share
the actual build steps (`scripts/dh_build_steps.sh`) with the Dockerfile's
`dh-builder` stage and `Dockerfile.dh-builder`, so they can't drift apart.

#### On-demand rebuild

The bundle directory (`server/static/dh/`) and its source schema (`/dh-schema`
in the container) are bind-mounted from host directories
(`MIMICC_DH_BUNDLE_DIR` / `MIMICC_DH_SCHEMA_DIR` in `.env`, defaulting under
`~/.mimicc-ena/`), seeded from the image's build-time bundle/schema on first
run. This means a rebuild can be triggered at runtime — via
`POST /api/dh/build` (optionally with a `schema_yaml` body to overwrite the
schema first) then streaming `GET /api/dh/build/stream/{job_id}` — without
restarting the server or rebuilding the image; the result is immediately
served at `/dh`. This spawns the `mimicc-dh-builder` sibling container
(build it once with `docker build -f Dockerfile.dh-builder --build-context
dataharmonizer-src=../DataHarmonizer -t mimicc-dh-builder .`), mirroring how
reads submission spawns `enasequence/webin-cli`. There's no UI for this yet —
it's scaffolding for a future in-app template editor.

#### Export integration (requires a patched DataHarmonizer fork)

The Samples tab's **Export to Prepare** button and its 30s autosave pull the current grid data
straight out of the embedded DataHarmonizer iframe (same-origin, via
`iframe.contentWindow.dataHarmonizer.getExportJson()`), persist it to the active session
(`POST /api/sessions/{id}/dh-export/sample` → `<id>/dh_export.json`) and populate the `#dhExport`
textarea that the **Prepare** step already reads — no manual File → Save As → upload round trip. On
reopening a session the saved export is loaded **back into the grid** via
`iframe.contentWindow.dataHarmonizer.loadExportJson(...)`. The Reads tab's experiment-metadata panel
(below) uses the same mechanism under `kind=experiment`.

**This requires `window.dataHarmonizer` to exist in the DataHarmonizer bundle** — vanilla
DataHarmonizer doesn't expose it; it's a small patch applied directly to the `DataHarmonizer`
checkout used as the `dataharmonizer-src` build context:
- `lib/Toolbar.js`: `buildExportJson`/`getExportJson`/`loadExportJson` (full-grid export/import),
  plus a cell-level API (`getCellValue`, `setCellValue`, `findRowIndex`, `addRow`, `upsertRow`) used
  to sync individual columns without clobbering the rest of a row.
- `web/index.js`: expose all of the above on `window.dataHarmonizer` once the grid loads
  (`{ready, getExportJson, loadExportJson, getCellValue, setCellValue, getRowCount, findRowIndex,
  addRow, upsertRow}`).

Without this patch, the export button shows "isn't ready yet" and the Samples tab falls back to the
manual upload/paste flow; the experiment-metadata panel (below) similarly can't sync or merge.

### Experiment metadata schema

Sample metadata and experiment metadata (platform, instrument, library source/selection/strategy,
…) are entered through **two separate** DataHarmonizer templates rather than one combined schema:
sample metadata stays in `vendor/schemas/mimicc_sample_experiment.yaml` (Samples tab); experiment
metadata gets its own template, sourced from an **optional**, separately-authored
`mimicc_experiment.yaml`, shown in a second DataHarmonizer panel on the Reads tab.

- **File location**: add `ena-submission-dataharmonizer/schemas/mimicc_experiment.yaml` (picked up
  automatically by `scripts/vendor.sh`, which already copies the whole `schemas/` directory) and
  rebuild. **This file doesn't exist yet** — until you add it, the build produces only the sample
  template (exactly as before this feature existed; the experiment panel shows "schema not built
  yet").
- **Column-title contract**: the app can't introspect a schema that doesn't exist yet, so it syncs
  and merges by fixed, expected LinkML `title:` values (see `EXP_KEY_TITLE`/`EXP_SAMPLE_TITLE`/
  `EXP_FIELD_TITLES` near the top of the "Experiment metadata DataHarmonizer panel" section in
  `server/static/app.js`) — your schema's slots must use these exact titles:

  | Manifest field | Required `title:` |
  |---|---|
  | (row key, matches a pairing row's NAME) | `Experiment name` |
  | (matches a pairing row's SAMPLE) | `Sample alias` |
  | PLATFORM | `Platform` |
  | INSTRUMENT | `Instrument` |
  | LIBRARY_SOURCE | `Library source` |
  | LIBRARY_SELECTION | `Library selection` |
  | LIBRARY_STRATEGY | `Library strategy` |
  | INSERT_SIZE (optional) | `Insert size` |
  | LIBRARY_NAME (optional) | `Library name` |
  | DESCRIPTION (optional) | `Description` |

  Use your schema's own `ifabsent` defaults for PLATFORM/INSTRUMENT/etc. (replacing the removed
  hardcoded "library preset" dropdown) — new rows added by the sync below pick those up
  automatically (`addRows()`'s normal default-population behaviour).
- **How sync works**: whenever the Reads tab's pairing table changes (scan, auto-assign, manual
  edit, TSV import), each pairing row's NAME/SAMPLE is upserted into the experiment grid by `NAME`
  — only those two columns are touched, so anything already filled in (manually, or via a default)
  on that row is preserved. At submit time, each pairing row is merged with its matching experiment
  row (by NAME) to build the webin-cli manifest; a row with no experiment-grid match, or an
  experiment grid that isn't built/ready, blocks submission with a clear error rather than sending
  an incomplete manifest.
- The on-demand rebuild path (`POST /api/dh/build`) is **not** extended to the experiment template —
  it stays scoped to the sample template; rebuilding the experiment template requires a full
  `docker compose build`.

### Read-sample pairing TSV

The Reads tab's pairing table can be exported/imported as TSV (**Export pairings (TSV)** /
**Import pairings (TSV)** buttons), columns: `NAME, SAMPLE, STUDY, paired, FASTQ1, FASTQ2, FASTQ`.
This is a full round-trip of a pairing row (not just the sample assignment), so importing works
standalone without scanning first; importing onto an existing table merges by `NAME` (updates a
matching row, appends a new one otherwise).

## Submission sessions

All work is organised around a **named submission session**, picked or created when the app opens
(the tabs stay locked until one is active; the header shows the current session and a **Switch…**
button). Everything about a session is saved to disk and restored when you reopen it:

- **What's persisted** — every text field, checkbox and selection, the DataHarmonizer grid data,
  all result tables, and the Reads/Records logs. Saving is automatic (debounced as you type, plus
  immediately after submits); the header shows "saved …". **Credentials are never saved** — re-enter
  them after a restart.
- **Where** — `MIMICC_SESSIONS_DIR` (default `~/.mimicc-ena/sessions`): a SQLite registry
  (`sessions.db`) plus a per-session directory (`<id>/state.json`, `<id>/dh_export.json` (sample
  metadata grid), `<id>/dh_export_experiment.json` (experiment metadata grid), `<id>/logs/reads.log`).
- **Resumable reads** — each run gets a stable, session-scoped alias. On submit, runs already
  submitted in this session or already present in ENA (checked via the Reports API) are
  **auto-skipped** and shown with their existing accessions, so an interrupted batch resumes by just
  clicking **Submit** again. Tick a run's **Re-upload** box (or the global "force re-upload all"
  toggle) to submit it again under a fresh alias (ENA aliases are permanent, so a forced re-upload
  necessarily creates a new experiment/run).

## Using it

1. **Session** — create or open a named session (required before the tabs unlock).
2. **Credentials** — enter your Webin username/password (memory only).
3. **Studies** — create a study → note the `PRJEB…` accession.
4. **Samples** — enter metadata in DataHarmonizer, click **Export to Prepare** (autosaves every
   30s too — see "Export integration" above), **Prepare** (filter + rename), then **Submit** with
   checklist `ERC000025` → `ERS…`/`SAMEA…`.
5. **Reads** — **Scan** the active reads directory (default workspace, or **Browse…** to point at
   any folder on disk), **Auto-assign samples** (or export/import the pairing as TSV), fill in
   platform/instrument/library fields in the **experiment metadata** DataHarmonizer panel (synced
   from the pairings — see "Experiment metadata schema" above), then **Submit reads to ENA** and
   watch the streamed webin-cli log → experiment + run accessions. Re-submit to resume.
6. **Records** — browse account records and release/hold/suppress/cancel.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # full stack (incl. linkml) for all features
pip install pytest pytest-asyncio anyio playwright
bash scripts/vendor.sh                    # so ena_api / ena_common etc. import

# Run the server locally (reads submission needs Docker; other tabs work without it)
PYTHONPATH=server:. uvicorn main:app --reload --port 9000 --app-dir server
```

### Tests

`pytest` (in-process ASGI API tests + read-assignment unit tests) and Playwright
(UI), mirroring `webin-cli-browser-assistant`'s patterns. No Docker or network
needed — the webin-cli runner and `ena_service` calls are mocked.

```bash
pip install pytest pytest-asyncio anyio playwright
python -m playwright install chromium     # for the UI tests
python -m pytest -q                        # all tests
python -m pytest tests/test_server.py -q   # API only
```

## Layout

```
server/
  main.py              FastAPI app: endpoints, jobs, SSE
  ena_service.py       studies/samples/records/actions (wraps reused libraries)
  read_assign.py       scan / suggest / manifest build for reads
  session_store.py     submission sessions: SQLite registry + reads ledger + per-session files
  webin_runner.py      Docker-in-Docker adapter (from webin-cli-browser-assistant)
  dh_builder_runner.py Docker-in-Docker adapter for the DH bundle rebuild
  _bootstrap.py        puts vendored sibling code on sys.path
  static/              single-page UI (index.html, app.js) + DH bundle (dh/, bind-mounted)
webin_cli_lib/     webin-cli Docker executor (from webin-cli-browser-assistant)
dh_builder_lib/    mimicc-dh-builder Docker executor (mirrors webin_cli_lib)
scripts/
  vendor.sh              copy sibling repos into ./vendor
  build_dh_template.sh   build the embedded DataHarmonizer bundle (local dev)
  dh_build_steps.sh       shared DH build steps (used by the above + both Dockerfiles)
  dh_builder_entrypoint.sh entrypoint for the mimicc-dh-builder image
  server_entrypoint.sh   seeds the bind-mounted DH bundle/schema dirs on first run
tests/             pytest + Playwright
Dockerfile             builds the main server image (includes a dh-builder stage)
Dockerfile.dh-builder  builds the on-demand DH-rebuild sibling image
docker-compose.yml
```

## Notes

- **Credentials** are never written to disk or logged; re-enter after a restart.
- **Reads workspace** is mounted read-write so generated manifests sit next to
  their FASTQs (one `-inputDir` for webin-cli). The default workspace can be
  overridden per-session: **Browse…** on the Reads tab lists directories via
  the `/hostroot` mount (now read-write, not just for validation) and lets you
  point scanning/manifest-writing at any folder on disk — the server itself
  already controls the host's Docker daemon via the socket mount, so this
  doesn't meaningfully change the trust boundary of what's meant to be a
  single-trusted-local-user tool. `/api/reads/browse` and `/api/reads/set-dir`
  back this; `GET /api/health` reports the active vs. default directory.
- **Reads** go through webin-cli (Docker), **not** the JAR path in
  `submit_reads.py` — that module is intentionally not imported (avoids its
  mgnify-toolkit dependency).
