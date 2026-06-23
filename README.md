# MIMICC ENA Submission Assistant

A web app for submitting **studies**, **samples**, and **sequencing reads** to
the European Nucleotide Archive (ENA) for the
[MIMICC](../mimicc) project.

It runs two ways from one codebase, selected by `DEPLOYMENT_MODE`:

- **local** (default) — single user on one machine; auto-logs-in as admin, no
  login screen. `docker compose` brings up everything.
- **hosted** — multi-user on a shared server. Username/password accounts gate
  access; session state and intermediate files live on the server (in Postgres);
  studies/samples are submitted server-side; and **reads upload goes direct from
  each user's machine to ENA** via a small local helper (the server never
  touches read files).

It ties together three existing tools:

| Concern | Reused from | How |
|---|---|---|
| Create/modify/list/delete **studies & samples** | [`ena-api-client`](../ena-api-client) + [`ena-dh-scripts`](https://github.com/timrozday-mgnify/ena-dh-scripts) | `WebinClient` REST submission (server-side) + the `submit_study`/`submit_sample` batch builders |
| Enter **sample metadata** | [DataHarmonizer](../DataHarmonizer) | embedded spreadsheet UI (Samples tab) → export → filter/rename → submit |
| Submit **reads** | [`read-helper`](../read-helper) | a local **[read-helper](https://github.com/timrozday-mgnify/read-helper)** runs `enasequence/webin-cli` on the user's machine; the browser bridges manifest (server) → helper → result (server) |

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
**production** (with a confirm). Webin credentials live in server memory only
(per user) and are never persisted.

## Architecture

```
Browser ── login cookie ──► FastAPI server (server/main.py)
   │                          ├── auth.py + orm/ ── Django ORM (accounts, sessions, reads ledger) → Postgres
   │                          ├── ena_service.py ── ena-api-client submit_study/submit_sample (REST/XML, server-side)
   │                          └── read_assign.py ── suggest + manifest text build
   │  fetch manifest + plan ◄─┘
   │  POST manifest + Webin creds
   ▼
Local read-helper (127.0.0.1:9100, https://github.com/timrozday-mgnify/read-helper) ── docker run enasequence/webin-cli ──► ENA dropbox
   │  SSE log stream ─► Browser ─► POST /api/reads/result (server updates the resume ledger)
```

- **Database**: Django's ORM (used as a standalone library — FastAPI stays the
  HTTP layer) over **PostgreSQL**. Accounts use Django's `auth.User`; sessions,
  their full UI state, and the reads resume ledger are owned per user. (When no
  `DATABASE_URL` is set the ORM falls back to SQLite — used for tests and
  lightweight local runs.)
- **Accounts**: a basic username/password system, separate from ENA Webin
  credentials, with an `admin` superuser (from `ADMIN_USERNAME`/`ADMIN_PASSWORD`)
  who can manage other accounts (Admin tab). Web logins are DB-backed cookies.
- **Reads**: the server builds the webin-cli manifest and the upload *plan*
  (what to upload vs. skip, via the ledger + ENA Reports API), but the upload
  itself runs on the user's machine in the [read-helper](https://github.com/timrozday-mgnify/read-helper)
  (built from a pinned tag, see "Pinned dependency versions" below) — reads
  never pass through the server.
- **DH bundle rebuild** (`POST /api/dh/build`) still spawns the
  `dh-builder` sibling container, but is now **admin-only** and needs the
  Docker socket mounted on the server (off by default — the bundle is baked at
  image-build time).

## Install & run

Prerequisites: Docker Desktop. All sibling code (`DataHarmonizer`, `dh-builder`,
`ena-dh-scripts`, `read-helper`, `linkml-lib`, `ena-api-client`,
`dataharmonizer-template-builder`) is pulled automatically at pinned versions
during `docker compose build` — no sibling checkouts to clone first. Node/Yarn
are **not** required on the host either — the Docker build compiles the
embedded DataHarmonizer bundle itself, in a dedicated build stage. The MIMICC
schemas + ENA XSDs (`schemas/`, `assets/ena_schema/`) are committed directly
in this repo — nothing to fetch for those either. See "Pinned dependency
versions" below for where the sibling-repo pins live.

### Local (single user)

```bash
# 1. Configure (admin/admin + bundled Postgres by default)
cp .env.example .env   # optional — sensible defaults work out of the box

# 2. Start the app + Postgres + DH sidecar + the local read-helper.
#    The "local" profile includes the read-helper so reads upload works on one box.
COMPOSE_PROFILES=local docker compose up -d --build
open http://localhost:9000
```

Postgres data, the DH bundle/schema, and the schema library are kept in named
Docker volumes (`docker volume ls | grep mimicc`); no host directories need
pre-creating. Migrations run automatically on startup.

If port 9000 is already taken, set `MIMICC_PORT` in `.env` and open
`http://localhost:<MIMICC_PORT>`. Stop with `docker compose down` (add
`--profile local` to also stop the helper).

### Hosted (multi-user)

```bash
cp .env.example .env
#   - set DEPLOYMENT_MODE=hosted
#   - change ADMIN_PASSWORD and set a long DJANGO_SECRET_KEY
#   - set strong POSTGRES_PASSWORD
#   - set ALLOWED_ORIGINS to your app's public origin if the API is cross-origin
docker compose up -d --build      # db + app + dhtb (NOT the read-helper)
```

Put the app behind a TLS-terminating reverse proxy (the login cookie is marked
`Secure` in hosted mode) and adjust the port binding to expose it. Sign in as
`admin`, then create user accounts from the **Admin** tab. Each user has their
own private sessions and submissions.

Each user installs and runs the [read-helper](https://github.com/timrozday-mgnify/read-helper)
on their **own workstation** (it is what uploads their reads directly to ENA).
See its README; point its `MIMICC_APP_ORIGIN` at your hosted app so the
browser page is allowed to drive the loopback helper.

If you don't have a `DataHarmonizer` checkout, or want the Samples tab to fall
back to DH export upload instead, see "DataHarmonizer bundle build" below.

### DataHarmonizer bundle build

The Samples tab embeds a built DataHarmonizer bundle (`server/static/dh/`) with
the MIMICC template, carrying the LinkML schema committed at
`schemas/mimicc_sample.yaml` (filtered from `mimicc_sample_experiment.yaml`
down to sample-scoped slots — see "Experiment metadata schema" below for the
sibling experiment template and the filter mechanism). `docker compose build`
produces this automatically via a `dh-builder` stage in the `Dockerfile` (Node +
Yarn + a pinned `DataHarmonizer` checkout, cloned at build time — see
`DATAHARMONIZER_REF` in the `Dockerfile`). If you need to build without it,
remove the `dh-builder` stage's `COPY --from=dh-builder` line in the final
image — the Samples tab still works via DH export upload either way.

For local non-Docker development, `scripts/build_dh_template.sh` does the same
build directly on the host (requires Node + Yarn there instead — see the
script's usage comment for the env vars it expects) against this repo's
committed `schemas/`. Both this script and the Dockerfile's `dh-builder` stage pull the
actual build steps (`dh_build_steps.sh`) from the standalone
[`dh-builder`](https://github.com/timrozday-mgnify/dh-builder) repo — its
single canonical copy, not vendored here — pinned to a tag (`DH_BUILDER_REF` in
the `Dockerfile`), so they can't drift apart.

#### On-demand rebuild

The bundle directory (`server/static/dh/`) and its source schema (`/dh-schema`
in the container) are bind-mounted from host directories
(`MIMICC_DH_BUNDLE_DIR` / `MIMICC_DH_SCHEMA_DIR` in `.env`, defaulting under
`~/.mimicc-ena/`), seeded from the image's build-time bundle/schema on first
run. This means a rebuild can be triggered at runtime — via
`POST /api/dh/build` (optionally with a `schema_yaml` body to overwrite the
schema first) then streaming `GET /api/dh/build/stream/{job_id}` — without
restarting the server or rebuilding the image; the result is immediately
served at `/dh`. This spawns the `dh-builder` sibling container, built
from the standalone [`dh-builder`](https://github.com/timrozday-mgnify/dh-builder)
repo at its pinned tag (build it once with:

```bash
docker build \
  --build-context dataharmonizer-src=https://github.com/timrozday-mgnify/DataHarmonizer.git#v2.1.0-mimicc \
  -t dh-builder https://github.com/timrozday-mgnify/dh-builder.git#v0.1.0
```

— substitute the tags above with whatever's currently pinned in this repo's
`Dockerfile`/`docker-compose.yml`), mirroring how reads submission spawns
`enasequence/webin-cli` via the [`read-helper`](https://github.com/timrozday-mgnify/read-helper) repo.

### Schema library (Schema tab)

The **Schema** tab lets you build, edit, save, and select LinkML schemas for the
sample/experiment grids, instead of being stuck with the two prebuilt MIMICC
templates:

- **Library** — schemas saved under `~/.mimicc-ena/schemas` (`/schemas` in the
  container; `SCHEMAS_CONTAINER_DIR`), seeded on first use from the bundled
  `schemas/*.yaml`. Each row can be edited, used for the sample or
  experiment grid, exported as a `.yaml` file, or deleted. You can also supply
  your own schema/checklist/XSD file from disk via the file picker.
- **Build** — merges fields from bundled ENA sample checklists (`assets/
  ena_schema/*.xml` and `.../checklists/*.xml`, fetched with
  `scripts/fetch_ena_checklists.sh`), ENA/SRA XSDs (`assets/ena_schema/
  *.xsd`), and/or existing saved schemas (`POST /api/schemas/import`, backed by
  `linkml_lib.pipeline.build` — the same XML/XSD→LinkML converters used
  elsewhere in this app). Earlier-selected sources win on conflicting fields.
- **Edit** — the merged/loaded schema opens in an embedded
  [`dataharmonizer-template-builder`](../dataharmonizer-template-builder)
  sidecar (the `dhtb` service in `docker-compose.yml`, built from a pinned
  git URL — see "Pinned dependency versions" below), via its `postMessage` bridge
  (`dhtb.loadYaml` / `dhtb.exportYaml` / `dhtb.ready` / `dhtb.exported`/
  `dhtb.error` — see its own `docs/integration-contract.md`). Saving writes the
  exported YAML to the library (`POST /api/schemas`).
- **Select** — choosing a schema for the sample or experiment grid
  (`POST /api/schemas/select {role, schema_id}`) compiles it in-process
  (`linkml_lib.dataharmonizer_compile`, the same pure-Python compiler DH's own
  `script/linkml.py` performs) and overwrites that grid's *existing* template
  folder's `schema.json` (`mimicc/` or `mimicc_experiment/` under
  `server/static/dh/templates/`) plus `dh-template-registry.json`. Because
  DataHarmonizer fetches `schema.json` over HTTP at runtime
  (`lib/utils/templates.js: fetchSchema`), this takes effect on the next
  iframe reload — **no DataHarmonizer bundle rebuild needed**. (This is
  different from the on-demand rebuild above, which recompiles the whole
  Node/Yarn bundle; schema selection only swaps the served JSON for an
  already-registered template folder.)
- **Experiment schema caveat**: selecting an experiment schema that doesn't use
  the column-title contract below (`Experiment name` / `Sample alias`) breaks
  read-pairing sync — the Reads tab shows a non-blocking warning when this is
  detected.

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
checkout pinned as the `dataharmonizer-src` build context:
- `lib/Toolbar.js`: `buildExportJson`/`getExportJson`/`loadExportJson` (full-grid export/import),
  plus a cell-level API (`getCellValue`, `setCellValue`, `findRowIndex`, `addRow`, `upsertRow`) used
  to sync individual columns without clobbering the rest of a row.
- `web/index.js`: expose all of the above on `window.dataHarmonizer` once the grid loads
  (`{ready, getExportJson, loadExportJson, getCellValue, setCellValue, getRowCount, findRowIndex,
  addRow, upsertRow}`).

Without this patch, the export button shows "isn't ready yet" and the Samples tab falls back to the
manual upload/paste flow; the experiment-metadata panel (below) similarly can't sync or merge.

### Sample and experiment metadata schemas

Sample metadata and experiment metadata (platform, instrument, library source/selection/strategy,
…) are entered through **two separate** DataHarmonizer templates, both filtered out of the original
combined schema (`ena-submission-dataharmonizer/schemas/mimicc_sample_experiment.yaml`) via
the standalone `linkml-lib` package's `linkml_lib.transform.filter`:

- **`mimicc_sample.yaml`** (Samples tab) — every slot whose `annotations.source` is one of
  `ERC000025`, `MIMICC.custom`, `ENA.sample`, `ENA.project` (44 slots). Generated with:
  ```python
  from linkml_lib import io, schema, transform
  from linkml_lib.dh_data import _select_slot_names

  s = io.load_yaml("schemas/mimicc_sample_experiment.yaml")
  rows = schema.slot_meta(s)
  names = _select_slot_names(rows, "source IN ('ERC000025', 'MIMICC.custom', 'ENA.sample', 'ENA.project')")
  ordered = [r["name"] for r in rows if r["name"] in names]
  io.write_yaml(transform.filter(s, include=ordered), "schemas/mimicc_sample.yaml")
  ```
  This reuses the same SQL-WHERE-on-slot-metadata mechanism `dh_data.filter_columns` already uses
  to filter exported *data* by source, applied here to the *schema*'s own slot list instead — and is
  exactly `ena_service.DEFAULT_SAMPLE_FILTER`, the WHERE the Prepare step already applies when
  going from a DataHarmonizer export to ENA submission fields, so the schema and that filter now
  describe the same set of fields by construction.
- **`mimicc_experiment.yaml`** (Reads tab, second panel) — the complementary 12
  `SRA.experiment`/`SRA.study`-scoped slots, plus two new slots (`PLATFORM`/`INSTRUMENT`, absent from
  the source schema — authored from scratch with standard ENA/SRA controlled-vocabulary enums) and
  two join-key slots (`experiment_name`/`sample_alias`) that don't exist in the sample/experiment
  source schema at all. `STUDY_REF`, `CENTER_NAME`, `LIBRARY_LAYOUT` and `TITLE` were dropped (the
  first three aren't needed by webin-cli or are redundant with the pairing table; `TITLE`'s original
  `ifabsent` formula referenced sample-only slots not present in this schema).

Both committed directly at `schemas/` in this repo (copied from
`ena-submission-dataharmonizer`'s `schemas/` directory, no per-file changes
needed there). The experiment template build step still tolerates
`mimicc_experiment.yaml` being absent (gracefully falling back to sample-template-only), even though
in practice both files now exist permanently.

- **Column-title contract** (experiment schema only — the sample schema needs no equivalent contract
  since the Samples tab just renders whatever the schema defines, with no app-side sync/merge logic
  reading specific column titles): the app syncs/merges by fixed, expected LinkML `title:` values
  (see `EXP_KEY_TITLE`/`EXP_SAMPLE_TITLE`/`EXP_FIELD_TITLES` near the top of the "Experiment metadata
  DataHarmonizer panel" section in `server/static/app.js`) — your schema's slots must use these exact
  titles:

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
- **Where** — in **PostgreSQL**, owned per user (the `SubmissionSession` model: the full UI snapshot,
  both DataHarmonizer grid exports, and the reads log are columns; the per-run reads ledger is the
  related `ReadsRun` model). Session **names are unique per user**, so two users can have a session of
  the same name. (This replaces the old single-user SQLite registry + per-session files on disk.)
- **Resumable reads** — each run gets a stable, session-scoped alias. The server's upload **plan**
  skips runs already submitted in this session or already present in ENA (checked via the Reports
  API) and shows their existing accessions, so an interrupted batch resumes by just clicking
  **Submit** again. Tick a run's **Re-upload** box (or the global "force re-upload all" toggle) to
  submit it again under a fresh alias (ENA aliases are permanent, so a forced re-upload necessarily
  creates a new experiment/run).

## Using it

0. **Sign in** (hosted mode only) — with your app account; local mode skips this and signs you in
   as admin automatically.
1. **Session** — create or open a named session (required before the tabs unlock).
2. **Credentials** — enter your Webin username/password (memory only; also forwarded to the local
   read-helper when it's running, so it can upload).
3. **Studies** — create a study → note the `PRJEB…` accession.
4. **Samples** — enter metadata in DataHarmonizer, click **Export to Prepare** (autosaves every
   30s too — see "Export integration" above), **Prepare** (filter + rename), then **Submit** with
   checklist `ERC000025` → `ERS…`/`SAMEA…`.
5. **Reads** — make sure the **read-helper** is running (the Reads tab shows "helper: running"),
   enter the absolute path to your **local** reads directory, **Scan** (the helper lists read
   groups), **Auto-assign samples** (or export/import the pairing as TSV), fill in
   platform/instrument/library fields in the **experiment metadata** DataHarmonizer panel (synced
   from the pairings — see "Experiment metadata schema" above), then **Submit reads to ENA**. The
   browser asks the server for the manifest/plan, the helper runs webin-cli locally and streams its
   log, and the experiment + run accessions are recorded back. Re-submit to resume.
6. **Records** — browse account records and release/hold/suppress/cancel.
7. **Admin** (admins only) — create/delete user accounts and reset passwords.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # full stack incl. Django, linkml, and the
                                          # pinned ena_api/linkml_lib/dh_builder_lib/
                                          # ena-dh-scripts git dependencies
pip install pytest pytest-asyncio anyio playwright

# Apply migrations. With no DATABASE_URL the ORM uses a local SQLite file
# (.data/app.db); set DATABASE_URL=postgresql://… to use Postgres instead.
python manage.py migrate

# Run the server locally (reads submission needs the local read-helper running;
# other tabs work without it). DEPLOYMENT_MODE defaults to local (auto-login).
PYTHONPATH=server:. uvicorn main:app --reload --port 9000 --app-dir server
```

The schemas/XSDs (`schemas/`, `assets/ena_schema/`) are committed directly in
this repo, so no extra setup is needed for them — `server/_bootstrap.py`
resolves them by default, with `ENA_DH_SCHEMA`/`ENA_DH_XSD`/
`ENA_DH_SCHEMAS_DIR` available to override the paths if needed.

### Tests

`pytest` (in-process ASGI API tests + read-assignment unit tests) and Playwright
(UI), mirroring `read-helper`'s patterns. No Docker or network
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
  main.py              FastAPI app: endpoints (auth/admin/sessions/reads plan+result), jobs, SSE
  auth.py              accounts, login sessions, admin bootstrap, FastAPI auth dependencies
  orm/                 Django app: settings.py, models.py (User/LoginSession/SubmissionSession/ReadsRun), migrations/
  dbsetup.py           one-time django.setup() bootstrap for using the ORM standalone
  ena_service.py       studies/samples/records/actions (wraps reused libraries, server-side REST)
  read_assign.py       scan / suggest / manifest (text) build for reads
  session_store.py     submission sessions + reads ledger, Django-ORM-backed, owner-scoped
  dh_builder_runner.py Docker-in-Docker adapter for the (admin-only) DH bundle rebuild
  schema_service.py    schema library: list/save/delete, ENA XML/XSD import/merge, grid selection
  _bootstrap.py        locates the committed schema/XSD assets (schemas/, assets/ena_schema/;
                        sys.path is no longer needed for ena_api/linkml_lib/dh_builder_lib/
                        ena-dh-scripts — they're pinned pip dependencies, see requirements.txt)
  static/              single-page UI (index.html, app.js) + DH bundle (dh/, volume-mounted)
manage.py          Django management entrypoint (migrations)
schemas/           committed MIMICC LinkML schemas (mimicc_sample.yaml, mimicc_experiment.yaml)
assets/ena_schema/ committed ENA/SRA XSDs + checklist XMLs (checklists/ filled by fetch_ena_checklists.sh)
scripts/
  fetch_ena_checklists.sh fetch the full set of public ENA sample-checklist XMLs
  build_dh_template.sh   build the embedded DataHarmonizer bundle (local dev)
  server_entrypoint.sh   seeds the bind-mounted DH bundle/schema dirs on first run
tests/             pytest + Playwright
Dockerfile             builds the main server image (includes a dh-builder stage and
                       pinned git-clone stages for DataHarmonizer/dh-builder)
docker-compose.yml
```

`dh_builder_lib` (the Docker executor `dh_builder_runner.py` wraps, now a pinned pip
dependency), the Dockerfile for the `dh-builder` image (shared with
[dataharmonizer-template-builder](https://github.com/timrozday-mgnify/dataharmonizer-template-builder),
which runs the same image with a different `TEMPLATE`), and `dh_build_steps.sh`
(the shared DH build steps, also pulled in by the Dockerfile's embedded
`dh-builder` stage and `scripts/build_dh_template.sh` above) all live in the
standalone [`dh-builder`](https://github.com/timrozday-mgnify/dh-builder) repo,
pulled at a pinned tag the same way [`read-helper`](https://github.com/timrozday-mgnify/read-helper)
is for reads upload.

### Pinned dependency versions

All sibling-repo code is pulled at a fixed git tag, never a local checkout or
`main`/`master`. The pins live in two places:

- **`requirements.txt`** — `ena-api-client`, `linkml-lib`, `dh-builder-lib`, and
  `ena-dh-scripts` as
  `name @ git+https://github.com/timrozday-mgnify/<repo>.git@<tag>` lines.
- **`Dockerfile`** — `DATAHARMONIZER_REF` / `DH_BUILDER_REF` build
  args, and **`docker-compose.yml`** — the `read-helper` and `dhtb` services'
  `build.context`/`additional_contexts` git URLs (`...git#<tag>`, or
  `...git#<tag>:<subdir>` for a subdirectory).

The MIMICC schemas + ENA XSDs (`schemas/`, `assets/ena_schema/`) aren't
pinned at all — they're committed directly in this repo, so they version
along with everything else.

To bump a pin: cut a new tag in the sibling repo, then update every reference
to that repo's tag across these two files (`grep -rn timrozday-mgnify .` from
the repo root finds them all).

## Notes

- **Webin credentials** are never written to disk or logged; held in server
  memory per user and re-entered after a restart. They are also forwarded to the
  local read-helper (in its memory only) so it can upload.
- **App accounts** are separate from Webin credentials. The admin account is
  (re)created from `ADMIN_USERNAME`/`ADMIN_PASSWORD` on every boot, so those env
  vars are authoritative for the admin password — change them before hosting.
- **Reads** go through webin-cli (Docker) on the **user's machine** via the
  read-helper, **not** the JAR path in `submit_reads.py` (that module is
  intentionally not imported — avoids its mgnify-toolkit dependency). The hosted
  server has no access to read files: no Docker socket, `/hostroot`, or reads
  mount.
- **Migrations**: `python manage.py makemigrations` / `migrate` (the entrypoint
  runs `migrate` automatically on startup).
