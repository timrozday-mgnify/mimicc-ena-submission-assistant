# The MIMICC ENA Submission Ecosystem

This document describes the group of related `timrozday-mgnify` projects that
together let the MIMICC project submit studies, samples and sequencing reads to the
**European Nucleotide Archive (ENA)**. It explains what each project is for, where
each kind of functionality belongs, which language/tool implements what (and why),
and the role of each external dependency.

---

## 1. Overview

End to end, the ecosystem does this:

1. **Create an ENA study** for the project.
2. **Enter sample and experiment metadata** in an embedded **DataHarmonizer**
   spreadsheet whose columns are driven by a **LinkML** schema.
3. **Prepare and submit samples** — metadata is filtered, renamed to ENA field
   names, turned into SRA XML, validated against ENA's XSDs, and POSTed to the
   Webin Submission API.
4. **Scan local sequencing reads**, group paired-end mates, and assign each read
   group to a sample/experiment.
5. **Submit reads** via ENA's `webin-cli`, run on the *user's own machine* so the
   large data files and credentials never pass through the server.

The pieces are layered: a thin **vanilla-JS UI** on top of a **Python/Django**
application, which orchestrates a set of **Python libraries** (ENA transport,
LinkML utilities, submission builders) and delegates heavy work to **Docker service
companions** (a schema editor, a bundle builder, and a local reads uploader).

```
                ┌───────────────────────────────────────────────┐
                │  mimicc-ena-submission-assistant (Django app) │
                │  vanilla-JS SPA  +  embedded DataHarmonizer   │
                └───────┬────────────────┬───────────────┬──────┘
                        │ imports        │ postMessage   │ HTTP
        ┌───────────────┴───┐    ┌───────┴─────────┐  ┌──┴──────────┐
        │  Python libraries │    │ dataharmonizer- │  │ read-helper │
        │  • ena-api-client │    │ template-builder│  │ (webin-cli  │
        │  • linkml-lib     │    │ (schema editor) │  │  on user PC)│
        │  • ena-submission-│    └───────┬─────────┘  └──────┬──────┘
        │    toolkit        │            │ Docker            │
        └─────────┬─────────┘      ┌─────┴──────┐            │
                  │                │ dh-builder │            │
                  │                │ (DH bundle │            │
                  │                │  builder)  │            │
                  │                └─────┬──────┘            │
                  │                      │ builds            │
                  │                ┌─────┴───────────┐       │
                  │                │ DataHarmonizer  │       │
                  │                │ fork (UI engine)│       │
                  │                └─────────────────┘       │
                  ▼                                          ▼
            ┌───────────────────────────────────────────────────┐
            │         ENA — Webin Submission & Reports APIs     │
            └───────────────────────────────────────────────────┘
```

---

## 2. Summary table

| Project | Type | Language(s) | Purpose | Key dependencies | Consumed by |
|---|---|---|---|---|---|
| **mimicc-ena-submission-assistant** | Web app | Python/Django + vanilla JS | The product: end-to-end UI for studies, samples, reads submission to ENA | ena-api-client, ena-submission-toolkit, linkml-lib, DataHarmonizer, dh-builder, read-helper, dhtb | — (top of stack) |
| **dataharmonizer-template-builder** (dhtb) | Web app / embeddable component | Python/Django + React/TypeScript/Vite | Interactive editor for LinkML DataHarmonizer schemas (YAML ↔ tables ↔ schema.json) | linkml-lib, DataHarmonizer, dh-builder, Handsontable | mimicc-assistant (iframe + postMessage) |
| **ena-api-client** | Library | Python | Typed client for ENA Webin Submission (XML) and Reports (JSON) APIs | httpx, pydantic | toolkit, assistant |
| **linkml-lib** | Library | Python | LinkML utilities: schema I/O, editable-table conversion, XML/XSD↔LinkML, DataHarmonizer compilation, diagnostics | linkml, linkml-runtime, PyYAML | toolkit, assistant, dhtb |
| **ena-submission-toolkit** | Library + CLI | Python | Schema-driven study/sample XML builders, unit normalisation, XSD validation, batch submit | ena-api-client, linkml-lib, lxml, typer | assistant |
| **read-helper** | Docker service | Python (Django) | Runs `webin-cli` read uploads locally on the user's machine | django, django-cors-headers, gunicorn, pydantic, docker, webin-cli image | assistant (HTTP on :9100) |
| **dh-builder** | Docker service + executor | Shell + Python + Node | Rebuilds a DataHarmonizer web bundle from a LinkML schema on demand | DataHarmonizer source, Node/Yarn | assistant, dhtb |
| **DataHarmonizer** (fork) | UI engine (vendored) | JavaScript + Handsontable | The spreadsheet editor/validator embedded for metadata entry | handsontable | assistant, dhtb, dh-builder |

---

## 3. Architectural layers

**Foundation libraries.** `ena-api-client` is the only thing that talks HTTP to ENA;
`linkml-lib` is the only thing that understands LinkML schemas and converts between
LinkML, editable tables, XSD/XML and DataHarmonizer's `schema.json`. Everything else
builds on these two.

**Orchestration library + CLI.** `ena-submission-toolkit` sits on top of the two
foundation libraries and turns structured (or DataHarmonizer-exported) data into
validated SRA XML and submits it in batches. It is both an importable library and a
`ena-submission-toolkit` CLI.

**Applications.** `mimicc-ena-submission-assistant` is the user-facing product.
`dataharmonizer-template-builder` (dhtb) is a focused companion app for *editing the
schema* that drives the grids.

**Service companions (Docker, spawned on demand).** `read-helper` runs `webin-cli`
on the user's machine; `dh-builder` rebuilds the DataHarmonizer bundle whenever the
schema changes. Both are decoupled over HTTP/JSON or Docker, not Python imports.

**Vendored UI engine.** The `DataHarmonizer` fork (pinned at `v2.1.0-mimicc`) is the
Handsontable-based spreadsheet that both apps embed.

---

## 4. Per-project documentation

### 4.1 mimicc-ena-submission-assistant

The product. A **Django** application (Python 3.11+, Django 5.x) serving a
**single-page vanilla-JavaScript** UI with **no Node/npm build step**. Runs in two
modes: *local* (single-user auto-login, Postgres + companions via Docker Compose) and
*hosted* (multi-user login, Redis-backed cache, each user runs their own read-helper).

- **Backend** (`server/`): `views_*.py` split by domain (auth, credentials, sessions,
  records, schemas, core); `orm/models.py` (`User`, `SubmissionSession`, `ReadsRun`);
  `ena_service.py` wraps `ena-api-client` + `ena-submission-toolkit`; `schema_service.py`
  wraps `linkml-lib`; `read_assign.py` groups reads and builds webin-cli manifests;
  `credentials_store.py` keeps per-user Webin credentials in the cache only (never the
  DB); `session_store.py` persists submission sessions to Postgres.
- **Frontend** (`server/static/`): `index.html` shell + `app.js` (~1,700 lines) for
  state, the CSRF-aware API client, the DataHarmonizer lifecycle, and reads-upload
  orchestration. The DataHarmonizer bundle is built in Docker and volume-mounted at
  `server/static/dh/`.
- **Schema artifacts** committed in the repo: `schemas/*.yaml` (LinkML) and
  `assets/ena_schema/` (ENA/SRA XSDs and checklist XMLs).
- **Pinned sibling libraries** (in `pyproject.toml`, no submodules):
  `ena-api-client @ ...@v0.1.0`, `linkml-lib @ ...@v0.1.0`,
  `ena-submission-toolkit @ ...@v0.1.0`.

### 4.2 dataharmonizer-template-builder (dhtb)

A browser-based editor for LinkML DataHarmonizer schemas. It loads a schema (YAML),
converts it to editable tables (Schemasheets-style: classes, slots, enums,
annotations), lets the user edit them in a Handsontable grid, and converts back to
LinkML YAML — plus produces a DataHarmonizer preview. Runs standalone or embedded.

- **Type:** full-stack web app — **Django** backend + **React 18 / TypeScript /
  Vite 6** frontend. Not published to npm/PyPI; deployed as a single Docker container
  on port **8765**.
- **Backend** (`src/dataharmonizer_template_builder/`): `conversion.py`, `tables.py`,
  `table_sync.py`, `dh_compile.py`, `validation.py` — all thin wrappers over
  `linkml-lib` (`edit_tables`, `io`, `dataharmonizer_compile`, `diagnostics`). In-memory
  per-session store.
- **Frontend** (`frontend/src/`): `App.tsx` (the editor), `DataGrid.tsx`
  (Handsontable wrapper), `api.ts` (HTTP client), `tableSync.ts` (client-side mirror
  of the backend sync logic).
- **Dependencies:** `linkml-lib @ ...@v0.1.0`, the `DataHarmonizer` fork
  (`file:../DataHarmonizer` for dev, `v2.1.0-mimicc` in Docker), `dh-builder-lib` for
  on-demand bundle rebuilds, `handsontable` / `@handsontable/react-wrapper` 17.1.0.
- **Integration:** embedded by the assistant as a cross-origin iframe; they exchange
  schema YAML over `postMessage` (`dhtb.loadYaml` → `dhtb.exported`).

### 4.3 ena-api-client

Typed **Python** client for ENA's two Webin HTTP APIs: the v2 Submission API (XML
submission, lifecycle actions release/hold/suppress/cancel) and the Reports API
(querying studies/samples/runs). Built on **httpx** (transport) and **pydantic** /
**pydantic-settings** (typed models + env config). It is the single point of HTTP
contact with ENA. Distributed via pip; consumed by `ena-submission-toolkit` and the
assistant. Module layout: `ena_api/` (client, config, models, submit, reports).

### 4.4 linkml-lib

Reusable **Python** utilities for LinkML schemas used with DataHarmonizer and ENA
tooling. Built on **linkml** / **linkml-runtime** / **PyYAML**. Key modules
(`src/linkml_lib/`): `io.py` (YAML I/O), `edit_tables.py` (schema ↔ editable tables),
`convert_xml.py` / `convert_xsd.py` (ENA XML/XSD ↔ LinkML), `dataharmonizer_compile.py`
(LinkML → DataHarmonizer `schema.json`), `schema.py` (introspection, `UnitRule`),
`pipeline.py` / `transform.py` / `dh_data.py` (build/merge schemas, filter exports),
`diagnostics.py`. It is the shared schema brain consumed by the toolkit, the
assistant *and* dhtb.

### 4.5 ena-submission-toolkit

Schema-driven **Python** library + **Typer** CLI (`ena-submission-toolkit`) for
building and submitting ENA records. It orchestrates XML manifest building, unit
normalisation, duplicate detection, **lxml** XSD validation (ENA/SRA XSDs bundled in
`assets/ena_schema/`), and submission via the Webin API. Depends on `ena-api-client`
(transport) and `linkml-lib` (schema utilities/unit rules), both pinned at `v0.1.0`.
Key modules (`src/ena_submission_toolkit/`): `submit_sample.py`, `submit_study.py`,
`prepare_dh_output.py`, `common.py`, `cli.py`. The assistant imports its
`submit_batch()` builders directly.

### 4.6 read-helper

A local **Python/Django** companion that runs `webin-cli` read uploads from the
user's machine — large read files and credentials never reach the server. Built on
**Django** served by **gunicorn** over WSGI, with **django-cors-headers** for the
cross-origin browser calls and **pydantic** for request validation; it shells out to
the host **docker** binary to run the `enasequence/webin-cli` image. Listens on
**:9100** (bound to `127.0.0.1` only); the assistant's UI detects it on localhost and
drives uploads. Jobs run in a background thread and logs are retrieved by **plain
HTTP polling** (`POST /api/scan`, `POST /api/submit`, then
`GET /api/status/<job_id>?since=N` to pull accumulated log lines and completion
state) — no SSE, so the service can run under plain WSGI. Key files:
`app/read_helper/views.py` (API + status page), `app/config/` (Django settings/urls/wsgi),
`app/webin_cli_lib.py`, `app/read_assign.py`, plus per-OS installer scripts in `install/`.

### 4.7 dh-builder

A standalone **Docker image + minimal Python executor** that rebuilds a
DataHarmonizer web bundle from a LinkML schema supplied at runtime — decoupling
schema changes from app rebuilds. Build steps are **shell** over **Node 20 / Yarn**
(building the DataHarmonizer fork pulled in via Docker build context); the Python
side (`dh_builder_lib`) exposes `iter_dh_builder_logs()` / `run_dh_builder()`. Used
by the assistant (`POST /api/dh/build`) and by dhtb (`TEMPLATE=template_builder_preview`).

### 4.8 DataHarmonizer (fork)

The external **JavaScript** spreadsheet editor/validator from CIDGOH, forked and
pinned at **`v2.1.0-mimicc`**. It provides the **Handsontable**-based grid UI that
both apps embed for metadata entry. It is not edited as part of normal work; it is
built into a bundle by `dh-builder` and consumed as a static asset (assistant) or via
`@handsontable/react-wrapper` (dhtb).

---

## 5. Language & tool choices in the two large apps — and why

Both large apps share a **Python/Django** backend for the same reason: *all* of the
domain logic — the ENA HTTP client, the LinkML utilities, the submission builders —
is Python, so the backend can call it directly with no FFI or service boundary. The
interesting difference is the **frontend**.

### mimicc-ena-submission-assistant — Python backend, *vanilla JS* frontend

- **Python/Django** for everything server-side: ORM models, auth and sessions,
  per-user credential handling, and orchestration of `ena-api-client`,
  `ena-submission-toolkit` and `linkml-lib`. Chosen because the submission stack is
  already Python; Django adds the ORM, auth, CSRF and session machinery the hosted
  mode needs without extra services.
- **Vanilla JavaScript with no build step.** The app does not ship React or a
  bundler. The heavy interactive UI — the metadata spreadsheet — is the embedded
  **DataHarmonizer** bundle, so the app shell only needs to manage tabs, API calls
  and the DataHarmonizer/read-helper lifecycle. Hand-written JS keeps the app
  build-free and dependency-light: there is no `package.json` to maintain.
- **DataHarmonizer embedded as an iframe** with a patched `window.dataHarmonizer`
  bridge (`getExportJson()` / `loadExportJson()`) so grid exports flow back into
  Django sessions.
- **Schema editing delegated to the dhtb sidecar** over `postMessage`, rather than
  reimplementing a schema editor in the assistant.
- **Reads upload pushed to a local read-helper** so credentials and large files stay
  on the user's machine — a deliberate trust/security boundary.

### dataharmonizer-template-builder — Python backend, *React/TypeScript/Vite* frontend

- **Python/Django** backend again, but here it is a thin wrapper around `linkml-lib`
  doing YAML ↔ editable-tables ↔ `schema.json` conversion and validation.
- **React + TypeScript + Vite** frontend — unlike the assistant — because this app is
  a genuinely interactive *editor*: it maintains real client-side state (tables,
  cross-references between classes/slots/enums, edit history, diagnostics, preview)
  and reuses DataHarmonizer's **Handsontable** grid through
  `@handsontable/react-wrapper`. That state-heavy, component-driven UI is exactly
  what React is good at, so the build toolchain earns its keep.

**The contrast in one line:** the assistant *hosts* finished DataHarmonizer grids
(no React needed — vanilla JS glue is enough), whereas the template builder *builds
and edits* schemas interactively (React + TS pays for itself).

---

## 6. External dependencies & their roles

| Dependency | Where | Role |
|---|---|---|
| **DataHarmonizer** (fork `v2.1.0-mimicc`) | assistant, dhtb, dh-builder | Browser spreadsheet editor/validator for metadata entry; the UI engine both apps embed |
| **Handsontable** 17.1.0 | inside DataHarmonizer; dhtb directly | The spreadsheet grid widget DataHarmonizer is built on |
| **LinkML / linkml-runtime** (≥1.7 / ≥1.8) | via linkml-lib | Schema metamodel, validation and runtime used for all schema work |
| **Django** 5.x | assistant, dhtb, read-helper | Backend framework: ORM, HTTP, auth, sessions, CSRF, cache abstraction (read-helper uses only the HTTP/routing layer) |
| **React 18 / TypeScript / Vite 6** | dhtb frontend | Component UI, typing and build for the interactive schema editor |
| **Django / gunicorn / django-cors-headers** | read-helper | Local HTTP service (WSGI) exposing webin-cli logs via HTTP polling |
| **httpx** (≥0.27) | ena-api-client, toolkit | HTTP transport to ENA |
| **pydantic / pydantic-settings** (≥2) | ena-api-client, dhtb, assistant | Typed request/response models and env-based config |
| **lxml** (≥5) | toolkit, linkml-lib | SRA XML building and XSD validation |
| **Typer** | toolkit | CLI framework for `ena-submission-toolkit` |
| **PyYAML** (≥6) | linkml-lib | LinkML YAML parsing/dumping |
| **PostgreSQL / Redis** | assistant | Persistent session/state storage (Postgres); per-user credential + cache store (Redis, hosted mode) |
| **Docker / docker-compose** | assistant, read-helper, dh-builder | Packaging and on-demand spawning of companion containers |
| **webin-cli** (`enasequence/webin-cli` image) | read-helper | ENA's official read-upload tool, run in a container on the user's machine |
| **Node 20 / Yarn** | dh-builder | Build toolchain for the DataHarmonizer bundle |

---

## 7. Cross-project wiring

- **Sibling Python libraries are pinned git dependencies**, not submodules or
  vendored copies. The assistant's `pyproject.toml` pins
  `ena-api-client @ git+...@v0.1.0`, `linkml-lib @ ...@v0.1.0` and
  `ena-submission-toolkit @ ...@v0.1.0`; dhtb pins `linkml-lib` and `dh-builder-lib`
  the same way. Upgrades happen by bumping a tag.
- **DataHarmonizer is built, not imported.** A Docker build stage clones the fork
  (`...DataHarmonizer.git#v2.1.0-mimicc`) and runs `dh-builder`'s build steps
  (Node/Yarn) to produce a bundle, which is volume-mounted into the assistant at
  `server/static/dh/`.
- **read-helper and dhtb run as separate Docker Compose services.** read-helper is on
  **:9100** (local profile only), dhtb on **:8765**. The assistant reaches read-helper
  over cross-origin HTTP (job submit + status polling) and dhtb over an iframe
  `postMessage` bridge.
- **Data flows over three channels:** HTTP/JSON (assistant ↔ libraries via Python
  imports, and assistant ↔ read-helper over the network), `postMessage` (assistant ↔
  dhtb), and shared Docker volumes (the built DH bundle and the schema library).

```
ena-api-client ──┐
                 ├─► ena-submission-toolkit ──► mimicc-ena-submission-assistant
linkml-lib ──────┤                                    │  │    │
   │             └────────────────────────────────────┘  │    │
   └──► dataharmonizer-template-builder ◄─ postMessage ──┘    │
                 │                                            │
            dh-builder ──builds──► DataHarmonizer (fork)      │
                                                              │
                                          read-helper ◄──HTTP─┘
```

---

## Historical lineage

`ena-submission-dataharmonizer` (the original monolithic toolkit/predecessor) and
`ena-dh-scripts` (an intermediate extraction, now legacy) were both superseded by
`ena-submission-toolkit`; they are kept only for history and are not part of the
active ecosystem documented above.
