# Architecture Standard: Server-Side Web Apps

This is a **standard**, not a retrospective: it states what sibling projects
in this ecosystem (the `timrozday-mgnify` group of repos) must do, with the
reasoning preserved so it doesn't need to be re-derived or re-litigated per
project.

## 1. Purpose & Scope

This standard exists because [mimicc-ena-submission-assistant](.) migrated
off FastAPI onto Django for both its ORM and HTTP layer, and that migration
surfaced a set of decisions (auth model, CSRF, secret storage, deployment,
testing) worth fixing as the default for every future project of the same
shape, rather than re-deciding them from scratch each time.

**Applies to:** any project that persists state in a database and serves an
HTTP API to a browser UI — i.e. mimicc-ena-submission-assistant's shape.

**Does not apply to (or applies only partially):**
- [read-helper](https://github.com/timrozday-mgnify/read-helper) — a
  stateless local helper that runs on a user's own machine; no database, no
  multi-user concerns.
- [dataharmonizer-template-builder](https://github.com/timrozday-mgnify/dataharmonizer-template-builder)
  — a frontend-heavy sidecar without this standard's full shape (no
  multi-user database-backed state), so it isn't required to adopt this
  standard wholesale. Adopting individual pieces (deployment, testing
  approach, etc.) is fine and encouraged, following the same §6 deviation
  process as any partial adoption: the parts it doesn't adopt are simply
  out of scope by shape (§1), not an unrecorded deviation from a standard
  that applies to it in full.
- Libraries, CLI tools, and anything else without a database + browser-facing
  HTTP API.

This standard is scoped to **architecture** — framework, data storage,
auth, deployment, and testing strategy. It includes the code-level patterns
those decisions require (e.g. §3.4's validation snippet, §3.2's migration
steps) where a pattern needs to be concrete to be followed consistently, but
it does not cover linting, formatting, or other language-level conventions
— each project's own `ruff`/equivalent config handles those.

**Status:** mimicc-ena-submission-assistant is currently the only project in
scope, and is the reference implementation (§4), with no open deviations.
No other sibling project currently needs migrating; this standard exists to
set the default for the *next* one, and to give a clear basis for migrating
an existing project if one is later brought into scope.

## 2. The Standard Stack

| Concern | Standard | Rationale |
|---|---|---|
| Framework (ORM + HTTP) | Django, serving both | [§3.1](#31-django-for-orm--http) |
| Database | PostgreSQL in production (`DATABASE_URL`); SQLite fallback for local dev and tests | [§3.1](#31-django-for-orm--http) |
| Sessions / auth | Django's built-in session framework (`django.contrib.sessions`), not a custom model or JWT | [§3.2](#32-django-session-framework) |
| CSRF | Django's `CsrfViewMiddleware`, bypassed only in an explicit single-user/local mode | [§3.3](#33-csrf-csrfviewmiddleware--local-mode-bypass) |
| Request validation | Pydantic models, validated manually inside plain Django views (not DRF serializers) | [§3.4](#34-pydantic-models-validated-manually-in-views) |
| Per-user secrets (API keys, third-party creds) | Cache backend (Redis in production, in-process for local/dev), never the database | [§3.5](#35-per-user-secrets-in-cache-never-db) |
| Deployment | gunicorn (WSGI), behind a reverse proxy in hosted deployments | [§3.6](#36-gunicornwsgi-deployment) |
| Static files | Django's own static-serving views in dev; reverse proxy / whitenoise in production | [§3.7](#37-static-file-serving-pattern) |
| Sibling-repo dependencies | Pinned to a git tag (`name @ git+https://...@<tag>`), never `main`/`master` or a local checkout | [§3.8](#38-sibling-repo-pinning-strategy) |
| Testing | `pytest` + Django's `django.test.Client`, in-process (no live server, no Docker) for API tests | [§3.9](#39-django-test-client-for-tests) |

This table is the quick-reference; §3 has the reasoning and the rejected
alternatives behind each row.

## 3. Decisions & Rationale

### Django for ORM + HTTP

**Standard:** use Django for both the database layer and the HTTP layer
(views + routing) in the same project. Don't pair Django's ORM with a
different web framework (FastAPI, Flask), and don't pair a different ORM
(SQLAlchemy) with Django's HTTP layer.

**Database engine:** PostgreSQL in production, driven by a single
`DATABASE_URL` env var; fall back to SQLite (a local file) when
`DATABASE_URL` is unset, for local development and the test suite. This
needs no project-specific justification — it's just Django's own
well-supported, default-adjacent pairing, and the SQLite fallback means
`pytest` and a fresh local checkout never require a Postgres container to
get started.

**Why:**
- One framework instead of two cuts the integration surface to zero: no
  `django.setup()` bootstrapping inside a foreign app, no bridging async
  request handlers to Django's sync ORM, no duplicated settings/migration
  tooling.
- Django's HTTP layer (views, middleware, `CsrfViewMiddleware`,
  `django.contrib.auth`, the test `Client`) is built assuming Django owns
  the request/response cycle. Using only the ORM and bolting on a different
  framework's HTTP stack (as mimicc-ena-submission-assistant originally did
  with FastAPI) forfeits all of that for no benefit once you're already
  taking the Django dependency.
- Plain Django views (not Django REST Framework) are enough for a JSON API
  serving a same-origin SPA — DRF's serializers/viewsets/routers add
  structure this shape of project doesn't need. Pydantic (see §3.4) covers
  request validation without DRF.

**Rejected alternatives:**
- **FastAPI/Flask + Django ORM** — mimicc-ena-submission-assistant's
  original architecture. Worked, but every HTTP-layer feature (auth,
  CSRF, static serving, testing) had to be hand-rolled or bridged, instead
  of using what Django already provides once it's serving HTTP too.
  FastAPI's automatic OpenAPI docs and async-native routing were never
  used; their lack of dependency-injection in Django views is the FastAPI
  feature most likely to be missed, but it's small ceremony to live without.
- **Django + DRF** — more idiomatic "Django REST" structure (serializers,
  viewsets, routers, browsable API), but more boilerplate and a steeper
  learning curve than this shape of project (a backend for one specific
  SPA, not a public/versioned API) needs.
- **SQLAlchemy + Django HTTP** — no reason to mix ORMs; Django's own ORM is
  sufficient for typical CRUD-shaped data and is what `django.contrib.auth`
  is already built on.

### Django session framework

**Standard:** use `django.contrib.sessions` (with `SessionMiddleware` +
`AuthenticationMiddleware`) for login state, not a hand-rolled session model
and not JWTs.

**Why:**
- It's built-in, tested, and already does the things a hand-rolled session
  model has to reinvent: signed/opaque session keys, configurable expiry
  (`SESSION_COOKIE_AGE`), optional rolling expiry on activity
  (`SESSION_SAVE_EVERY_REQUEST`), and — critically — `django.contrib.auth`'s
  `login()`/`logout()` helpers rotate the session key on login, which is the
  standard defense against session fixation. A custom model has to remember
  to do this itself (or, as in mimicc-ena-submission-assistant today, doesn't).
- One less app-specific model + migration to maintain per project; the
  session table, cleanup, and settings are identical across every project
  that adopts this standard.
- JWTs are rejected for the same reason they're usually wrong for
  cookie-based, server-rendered-ish session auth: revocation requires either
  a server-side blocklist (at which point you've rebuilt a DB-backed session
  anyway) or accepting that a "logged out" token stays valid until it
  expires.

**What you lose vs. a custom model (and how to live with it):**
- **No direct FK from session to user.** Django's `django_session` table
  stores an opaque session key and a blob of serialized data (which
  includes the user id, e.g. `_auth_user_id`), not a queryable foreign key.
  There is no built-in "delete all sessions for user X" — deleting a `User`
  does **not** cascade to their sessions. If a project needs this (e.g. an
  admin "force logout" feature, or cleanup on account deletion), add a small
  helper that iterates `Session.objects.all()`, calls `.get_decoded()`, and
  deletes rows whose `_auth_user_id` matches — or only rely on the existing
  expiry (`clearsessions` management command) if forced logout isn't a
  real requirement.
- **No built-in per-session `last_seen`.** `SESSION_SAVE_EVERY_REQUEST=True`
  refreshes the session's overall expiry on activity, but doesn't give you a
  queryable "last seen at" timestamp the way a dedicated model field does. If
  a project's admin UI wants to show that, store it inside the session data
  dict (`request.session["last_seen"] = ...`) rather than adding a custom
  model back.

Neither of these is a reason to deviate — they're small, well-understood gaps
with documented workarounds, not a hard blocker.

### CSRF: CsrfViewMiddleware + local-mode bypass

**Standard:** use Django's built-in `CsrfViewMiddleware` (token-based,
cookie + header) for any project with cookie-based auth. If the project has
a genuine single-user "local mode" with no login screen, bypass CSRF
checks only in that mode, via a small middleware that sets
`request._dont_enforce_csrf_checks = True` — the same hook Django's own test
client uses for `enforce_csrf_checks=False`. Don't invent a custom
header-based CSRF scheme.

**Why:**
- Cookie-based auth needs CSRF protection against cross-site requests by
  definition — a cross-site `<form>` post carries the cookie automatically.
  Django's middleware is the standard, audited way to do this; a custom
  scheme (e.g. requiring an `X-Requested-With` header, which
  mimicc-ena-submission-assistant's FastAPI version did) provides
  materially the same protection — a cross-site request can't set arbitrary
  headers either — but means re-auditing a hand-rolled check instead of
  relying on a well-known, widely-reviewed implementation.
- The local-mode bypass is real, not a workaround: a true single-user local
  mode with no login screen has no cross-site session-riding session to
  protect — there's no second account to forge a request as. Gating it
  behind an explicit, named middleware (rather than just disabling
  `CsrfViewMiddleware` globally) keeps the bypass visible and scoped to
  exactly the deployment mode it's justified for.
- The frontend side of this is one function: read the `csrftoken` cookie,
  send it back as `X-CSRFToken` on state-changing requests
  (`CSRF_COOKIE_HTTPONLY = False` is required for JS to read it). Every
  fetch call in the SPA must go through this — including raw
  multipart/file-upload requests that bypass a shared `fetch()` wrapper, a
  real bug mimicc-ena-submission-assistant hit during its migration (two
  call sites built `FormData` and called `fetch()` directly, missing the
  CSRF header).

**Rejected alternatives:**
- **Custom header-based check** (e.g. require `X-Requested-With`) — works,
  but it's bespoke security logic with no upstream maintenance or review,
  for no real benefit over the built-in middleware once Django owns the
  HTTP layer anyway.
- **Disabling CSRF entirely, even in hosted/multi-user mode** — not
  acceptable; cookie auth without CSRF protection is the textbook
  vulnerability the middleware exists to close.
- **No local-mode bypass at all** — technically safer, but
  enforces a real-world-meaningless protection in a mode with no
  cross-account threat to protect against, and breaks any tooling/UI flow
  that doesn't thread a CSRF token through (this is exactly the regression
  mimicc-ena-submission-assistant's migration introduced and then fixed —
  see §4).

### Pydantic models, validated manually in views

**Standard:** define request bodies as Pydantic `BaseModel` classes, and
validate them explicitly inside plain Django views:

```python
def my_view(request):
    try:
        req = MyRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    ...
```

Don't use Django REST Framework serializers, and don't use raw
`request.POST`/`json.loads(request.body)` dicts with manual key-checking for
anything beyond a trivial one- or two-field body. Convention: define each
request model next to the view(s) that use it, not in a separate
models-only file — see §4 for how the reference implementation organizes
this (`server/views_*.py`).

**Why:**
- Django has no built-in request-body validation for plain views (DRF's
  serializers are the closest built-in answer, but pull in the rest of DRF's
  structure along with them — see §3.1's rejection of DRF). Pydantic gives
  typed, declarative validation with good error messages and minimal
  ceremony, without committing to a different overall HTTP framework.
- There's no FastAPI-style automatic dependency injection of the validated
  model into the view signature — Django views don't have that — but the
  manual `model_validate(json.loads(request.body))` pattern is one line and
  identical across every view, so the lost ceremony is small and consistent.
- Pydantic models double as living documentation of the API's request
  shapes, independent of whatever HTTP framework is fronting them — useful
  if a project's HTTP layer ever needs to change again, since the
  validation logic doesn't have to move with it.

**Rejected alternatives:**
- **Django REST Framework serializers** — would work, but means adopting
  DRF's broader structure (see §3.1) just for body validation.
- **Plain Django Forms** — designed around HTML form submission and
  `request.POST`, awkward for a JSON API body; no clean equivalent to
  Pydantic's nested-model/list validation.
- **Manual dict validation** (`if "name" not in data: ...`) — fine for a
  single-field body, but doesn't scale past that without re-implementing
  what a validation library already does, and silently drifts from
  whatever the frontend actually sends.

### Per-user secrets in cache, never DB

**Standard:** if a project temporarily holds per-user third-party
credentials or secrets that the user re-enters each session (not the
project's own login password, which `django.contrib.auth` already hashes
and stores correctly) — store them in Django's cache framework
(`django.core.cache`), never in the database, even encrypted. Make the
cache backend env-driven: in-process `LocMemCache` for local/single-process
use, Redis for hosted/multi-worker deployments — with the Redis instance
configured with **persistence disabled** (`--save "" --appendonly no`), so
the "never written to disk" guarantee holds in both modes. Key by the
project's own user id (e.g. `f"webin_creds:{user_id}"`) so entries can't
collide or leak across users, and set a TTL on every key — tie it to the
same value as the project's session lifetime, so a credential never outlives
the login it was entered under.

**Why:**
- These are credentials for a *different* system (e.g. mimicc-ena-submission-assistant's
  ENA Webin login) that the user explicitly doesn't want persisted —
  re-entering them each session is the accepted tradeoff for not storing
  them at rest. A database row — even encrypted — is something that gets
  backed up, replicated, and potentially exported; a non-persistent cache
  entry with a TTL is not.
- A single in-process dict (mimicc-ena-submission-assistant's original
  FastAPI implementation) only works with exactly one worker process —
  it silently breaks the moment a deployment scales to multiple gunicorn
  workers, since each worker has its own memory. The cache framework gives
  the same "never on disk" property while actually working across workers.
- Making the backend env-driven means local/dev/CI never need a Redis
  container — only a real hosted deployment does — which keeps the default
  development loop simple.

**Rejected alternatives:**
- **Database column (even encrypted)** — defeats the purpose; "encrypted at
  rest" is not the same guarantee as "never at rest," and the whole point
  here is the latter.
- **A single in-process dict** — works for exactly one worker process; not
  a real option for any deployment that might ever scale past one.
- **Redis with default persistence (RDB/AOF) left on** — would silently
  reintroduce the at-rest-storage problem the cache was chosen specifically
  to avoid; persistence must be explicitly disabled, not left as the
  default.

### gunicorn/WSGI deployment

**Standard:** serve the app with gunicorn against Django's standard WSGI
entrypoint (`config.wsgi:application`), with multiple workers in hosted
deployments. Only reach for an ASGI server (uvicorn/daphne) or Django
Channels if the project has a genuine async/streaming requirement (e.g. a
real Server-Sent-Events or WebSocket endpoint) that WSGI can't serve.

**Why:**
- Plain Django views (no async views, no SSE/WebSockets) have no use for
  ASGI's concurrency model — gunicorn + WSGI is simpler, better understood,
  and is what the rest of this stack (sync ORM calls, sync test client)
  already assumes.
- Multi-worker gunicorn is only safe once nothing in the app depends on
  in-process-only state — see §3.5 (secrets) and avoid any equivalent
  in-memory job/state dict. Once that's true, scaling workers is just a
  flag (`--workers N`), with no further architectural change needed.
- Anything that needs to run once at container start (e.g. bootstrapping an
  admin account) should be an explicit management command run by the
  entrypoint script before gunicorn starts — not something hooked into
  `wsgi.py` itself, since gunicorn workers each import the WSGI app
  independently and would race / repeat the work once per worker.

**Rejected alternatives:**
- **uvicorn/ASGI for a project with no actual async or streaming
  endpoints** — adds complexity (async-safety of ORM calls,
  `DJANGO_ALLOW_ASYNC_UNSAFE`-style footguns) for a concurrency model the
  project doesn't use. (mimicc-ena-submission-assistant's one SSE endpoint
  turned out to have no real caller and was removed on that basis alone —
  its removal also happened to remove the project's only reason to need
  ASGI, but that was incidental, not the justification for this standard's
  WSGI default.)
- **Django's dev server (`manage.py runserver`) in production** — fine for
  local development, explicitly not designed or hardened for production
  traffic.

### Static file serving pattern

**Standard:** in development, serve static assets (the SPA's JS/CSS/HTML,
any embedded third-party bundle) via Django's own static-serving views
(`django.views.static.serve`, or a small custom view if directory-index
fallback behavior is needed — e.g. an embedded SPA that expects
`index.html` at any unmatched sub-path). In production, hand static files
to a reverse proxy or `whitenoise` instead of routing them through Django —
`django.views.static.serve` explicitly warns it isn't production-hardened.

**Why:**
- Keeping dev and prod static serving on different mechanisms is normal
  Django practice, not a shortcut — the dev-time convenience helper is
  intentionally not the production path.
- If an embedded third-party frontend bundle needs directory-index
  fallback behavior that Django's static serve doesn't provide (it serves
  exact files, no implicit `index.html` for a directory root), write the
  ~10-line custom view rather than adding a dependency for it.

**Rejected alternatives:**
- **Routing all static assets through Django in production** — works, but
  ignores the framework's own guidance and a reverse proxy's strengths
  (caching, range requests, not tying up application workers serving
  bytes).
- **A third-party static-files package as the default everywhere** (e.g.
  always using `whitenoise`, even in dev) — unnecessary dependency for the
  dev loop, where Django's own dev-time serving is already sufficient.

### Sibling-repo pinning strategy

**Standard:** when a project depends on another repo in this ecosystem
(a shared library, or a sibling service built/cloned at image-build time),
pin it to a fixed git tag, never `main`/`master` and never a local
checkout. Two forms, same rule:

- **Python dependencies** — `name @ git+https://github.com/timrozday-mgnify/<repo>.git@<tag>`
  entries in `pyproject.toml`'s `[project.dependencies]`.
- **Docker build contexts** — a pinned tag in the git URL
  (`...git#<tag>`, or `...git#<tag>:<subdir>` for a subdirectory), via a
  `<REPO>_REF`-style build arg in the `Dockerfile`, or a service's
  `build.context`/`additional_contexts` in `docker-compose.yml`.

To bump a pin: cut a new tag in the sibling repo, then update every
reference to that tag across the dependent project (a repo-wide grep for
the org name finds them all).

**Why:**
- A local checkout (`../sibling-repo`) only works on a machine that happens
  to have that sibling cloned next to this repo, at whatever state it
  happens to be in — it isn't reproducible in CI, in a teammate's clone, or
  in a built Docker image.
- Tracking `main`/`master` means a sibling repo's unreleased changes can
  silently break a dependent project's build with no corresponding commit
  in the dependent repo — the breakage has no local cause to `git bisect`.
- A pinned tag makes every dependency version an explicit, reviewable line
  change, and means a project's exact dependency set is reconstructable
  from its own git history alone.

**Rejected alternatives:**
- **Local checkouts / vendored copies** — not reproducible outside the
  machine that has the checkout; mimicc-ena-submission-assistant's own
  history includes migrating *off* exactly this pattern.
- **Tracking `main`/`master`** — convenient until a sibling repo's
  in-progress work breaks a dependent build with no warning.
- **Vendoring a sibling's source directly into this repo** — duplicates
  code across repos and guarantees drift; if a project needs sibling code
  it doesn't depend on as a package today, that's a sign the sibling
  should expose it as one, not that it should be copied in.

### Django test client for tests

**Standard:** drive API tests in-process with `django.test.Client` against
a throwaway SQLite database (configured before any app module is
imported), not a live server and not Docker. Reserve a real running server
(via `manage.py runserver` or gunicorn in a background thread) only for
browser-driven UI tests (e.g. Playwright), where an actual HTTP server is
unavoidable. Mock third-party network calls (e.g. calls to an external
API) rather than hitting the real service in tests.

**Why:**
- `django.test.Client` dispatches directly into the WSGI stack with no
  socket, no event loop, and no external process — fast, and deterministic
  across machines and CI.
- A throwaway SQLite DB configured per test run keeps tests independent of
  whatever `DATABASE_URL` a developer's environment happens to have set, and
  needs no Postgres container just to run the test suite.
- UI tests genuinely need a real server (a browser can't dispatch into a
  WSGI callable), so that's the one place a live server fixture is
  justified — keep it scoped to exactly that, not the default for API
  tests too.

**Rejected alternatives:**
- **A live server (real socket) for every test, API and UI alike** — works,
  but is slower and adds failure modes (port conflicts, startup race
  conditions) that an in-process client simply doesn't have, for API tests
  that don't need a real socket at all.
- **Hitting real third-party services in tests** — slow, flaky, and
  requires real credentials in CI; mock at the service-call boundary
  instead.
- **Docker-in-tests for anything other than what's genuinely
  Docker-specific** — this stack's tests don't need it; reach for it only
  if a project's tests must exercise an actual container boundary.

## 4. Reference Implementation

[mimicc-ena-submission-assistant](.) is the reference implementation of
this standard. Map of decision → where it lives:

| Decision | Files |
|---|---|
| Django for ORM + HTTP | `server/config/` (settings/urls/wsgi), `server/views_*.py` |
| Database | `server/config/settings.py` (`_databases()`) |
| Sessions / auth | `server/auth.py`, `django.contrib.sessions` (no app-specific session model) |
| CSRF | `server/config/settings.py` (`CsrfViewMiddleware`), `server/middleware.py` (local-mode bypass), `server/static/app.js` (`csrfHeaders()`) |
| Request validation | Pydantic models defined alongside their views in `server/views_*.py` |
| Per-user secrets in cache | `server/credentials_store.py` |
| gunicorn/WSGI deployment | `Dockerfile` (`CMD`), `scripts/server_entrypoint.sh`, `server/orm/management/commands/bootstrap_admin.py` |
| Static file serving | `server/views_core.py` (`serve_dh`, `static_serve_view`) |
| Sibling-repo pinning | `pyproject.toml`, `Dockerfile` build args, `docker-compose.yml` build contexts |
| Testing | `tests/conftest.py` (`AsyncClient` wrapper around `django.test.Client`), `tests/test_ui.py` (live-server fixture) |

## 5. Adopting This Standard

### Starting a new project

1. Set up a Django project serving both ORM and HTTP from the start (§3.1)
   — don't begin on FastAPI/Flask with the intent to migrate later.
2. Wire `django.contrib.auth` + `django.contrib.sessions` for accounts and
   login (§3.2) — not a custom session model.
3. Enable `CsrfViewMiddleware` from the start (§3.3); add the local-mode
   bypass middleware only if the project genuinely has a single-user mode
   with no login screen.
4. Define request bodies as Pydantic models, validated manually in views
   (§3.4).
5. If the project holds any per-user third-party secret, put it behind
   `django.core.cache` from day one (§3.5) — never a model field, even as a
   placeholder "for now."
6. Deploy on gunicorn/WSGI (§3.6); only add ASGI if there's a concrete
   streaming/async requirement.
7. Use Django's dev-time static serving locally; decide the production
   static story (reverse proxy or whitenoise) before going live (§3.7).
8. Pin any sibling-repo dependency to a tag from the first commit that adds
   it (§3.8).
9. Write API tests against `django.test.Client` from the start (§3.9);
   reach for a live server fixture only once there's an actual
   browser-driven UI test to write.

### Migrating an existing project onto this standard

1. Diff the project against the §2 table to find where it deviates.
2. Prioritize by risk, not by ease: auth/sessions and CSRF (§3.2, §3.3)
   first, since getting those wrong is a security gap, not just a style
   mismatch. Deployment and testing conventions (§3.6, §3.9) are lower risk
   and can move last.
3. Migrate one decision at a time, behind the existing test suite — don't
   bundle multiple architectural changes into one change. §3.2's migration
   guide (custom session model → `django.contrib.sessions`) is a template
   for how to write this kind of guide for any other decision a project
   needs to migrate.
4. While a project has open deviations, record them explicitly (mirroring
   §4's "Known deviation" note) rather than leaving them implicit — this is
   what lets a partially-migrated project still cite this standard
   honestly.
5. A project not yet able to close a gap (e.g. a real blocking reason, not
   just unscheduled work) should record why under §6's deviation policy,
   not silently diverge.

## 6. Deviation Policy

This is a hard standard, not a set of suggestions: a project in scope (§1)
follows §2/§3 unless it has a specific, stated reason not to.

**A deviation is acceptable when it's recorded, not when it's silent.**
Concretely:

- **Allowed**: "we use X instead of Y because [specific technical reason
  that doesn't apply to the general case this standard covers]" — written
  down in the project's own architecture doc or README, the way §4 used to
  record mimicc-ena-submission-assistant's now-closed session-model
  deviation as an example of the format.
- **Not allowed**: diverging with no note anywhere, or "we didn't get to it
  yet" treated as a permanent state. Backlog items are tracked as known
  deviations with an intent to close them (§5's migration checklist), not
  treated as approved exceptions.

A deviation that turns out to apply to more than one project is a signal
this standard itself might need to change — propose an update to this doc
(a PR against mimicc-ena-submission-assistant, where it lives) rather than
letting multiple projects quietly diverge the same way independently.

**Keeping this doc current:** when this standard changes, update §4's
reference-implementation mapping and §5's checklists in the same change —
a standard whose own reference implementation has drifted from it isn't
trustworthy.
