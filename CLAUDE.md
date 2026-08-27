# CLAUDE.md

Guidance for AI agents working in this repo. Read this before changing UI code.

## Testing — this project HAS a Playwright + docker-compose test suite

Do not re-invent it. It lives in `tests/` and runs via `task` (see `Taskfile.yml`):

- `task test` — full pytest suite (API views + unit tests, no Docker). Run this
  for any server-side change.
- `task test:ui` — fast in-process Playwright UI suite (`tests/test_ui.py`):
  a real WSGI server in a thread with `ena_service` mocked (`tests/conftest.py`
  `live_server_url`). Use for anything that can be exercised without the real
  DataHarmonizer bundle or the `dhtb` sidecar.
- `task test:compose` — Playwright against the **real** `docker compose` stack
  (`tests/test_compose_ui.py`, gated on `COMPOSE_TEST=1`). Builds the image and
  starts the containers, so it's slow (minutes) but it's the only test that
  exercises the built DH bundle, the fixed template folders, and the real
  cross-origin `dhtb` iframe. See README "Docker Compose tests".

**Rule:** any change under `server/static/*` (tabs, DataHarmonizer panels, JS)
or to the `Dockerfile` dh-builder stage MUST add or extend a Playwright test —
`test_ui.py` for mockable behaviour, `test_compose_ui.py` for anything needing
the real bundle / sidecar. The three submission grids (Studies, Samples, Reads)
are parallel: a test for one usually has an analog for the others.

## DataHarmonizer template folders

Each submission role is bound to a **fixed** DH template folder
(`server/schema_service.py` `ROLE_FOLDERS`): sample→`mimicc`,
experiment→`mimicc_experiment`, study→`study`. Selecting a schema from a tab's
dropdown compiles it *into* that folder (`/api/schemas/select`) and reloads the
grid — the folder must already exist in the built bundle. New role or new fixed
folder ⇒ add a build step to the `Dockerfile` dh-builder stage AND a startup
branch in `server/static/dataharmonizer.js` `initDhFrames()`.
