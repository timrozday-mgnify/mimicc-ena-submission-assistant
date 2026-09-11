"""Studies, samples, the generic records browser/actions, and reads (browser-bridged).

Reads upload goes DIRECT from the user's machine to ENA via the local helper.
The server never touches local read files. It is fully stateless: Webin
credentials arrive per-request (see ``webin_creds``) and the resume ledger lives
in the browser. The reads endpoints only:
  * suggest  — matches scanned read groups to ENA samples,
  * plan     — decides which runs to upload vs. skip (client ledger + ENA
               lookup) and hands the browser the webin-cli manifest text,
  * result   — parses the helper's outcome the browser relays back.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

import ena_service
import read_assign
import views_core
import webin_creds
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError

# reads ledger status values (the browser stores these; we only read them).
STATUS_DONE = "done"
STATUS_ALREADY_IN_ENA = "already_in_ena"

# Submission alias for MODIFYs from this app, so they are identifiable in ENA.
MODIFY_ALIAS = "mimicc-assistant-modify"


def _slug(text: str) -> str:
    """Filesystem/alias-safe slug: keep word chars, collapse the rest to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-")
    return s or "x"


def _list_params(request: HttpRequest) -> dict[str, Any]:
    """The query string of a record listing, as ``list_records`` kwargs.

    ``full_fields`` is off by default: it costs a Browser API request per 100
    records (and, on production, a Portal request per 50), which is only worth
    paying when someone actually wants the checklist columns.
    """
    return {
        "test": request.GET.get("test", "true").lower() != "false",
        "status": request.GET.get("status", "all"),
        "search": request.GET.get("search", "").strip(),
        "linked_to": request.GET.get("linked_to", "").strip(),
        "unlinked": request.GET.get("unlinked") == "true",
        "full_fields": request.GET.get("full_fields", "false").lower() == "true",
        "max_results": int(request.GET.get("max_results", 5000)),
    }


def session_run_alias(session_name: str, run_name: str) -> str:
    """Stable per-run alias. Session names are unique per user, so this is
    identical across re-submits — which is what lets us detect a run already in
    ENA."""
    return f"{_slug(session_name)}_{_slug(run_name)}"


class StudySubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    hold_until: str | None = None
    public: bool = False


class StudyPrepareRequest(BaseModel):
    export: dict[str, Any]


class PrepareRequest(BaseModel):
    export: dict[str, Any]
    where: str | None = ena_service.DEFAULT_SAMPLE_FILTER


class SampleSubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    checklist: str | None = "ERC000025"
    hold_until: str | None = None
    public: bool = False


class ActionRequest(BaseModel):
    action: str
    accession: str
    test: bool = True
    alias: str | None = None
    hold_until: str | None = None


class FieldsRequest(BaseModel):
    accessions: list[str]
    test: bool = True


class ModifyRecord(BaseModel):
    accession: str
    changes: dict[str, Any]


class ModifyRequest(BaseModel):
    entity: str
    records: list[ModifyRecord]
    test: bool = True


class SuggestRequest(BaseModel):
    groups: list[dict[str, Any]]
    test: bool = True
    max_results: int = 5000


class ReadsPlanRequest(BaseModel):
    runs: list[dict[str, Any]]
    test: bool = True
    session_name: str | None = None
    ledger: dict[str, dict[str, Any]] = {}  # run_name -> the browser's resume ledger row
    force_reupload: bool = False


class ReadsResultRequest(BaseModel):
    name: str
    alias: str | None = None
    stable_alias: str | None = None
    exit_code: int | None = None
    log: str = ""
    sample: str = ""
    study: str = ""
    experiment_accession: str | None = None
    run_accession: str | None = None


def _parse(model, request: HttpRequest):
    return model.model_validate(json.loads(request.body))


def _skip_result(run: dict[str, Any], name: str, alias: str, accs: dict[str, Any], reason: str) -> dict[str, Any]:
    """A result row for a run skipped during resume (already submitted/in ENA)."""
    return {
        "name": name,
        "alias": alias,
        "sample": run.get("SAMPLE", ""),
        "study": run.get("STUDY", ""),
        "exit_code": 0,
        "success": True,
        "skipped": True,
        "reason": reason,
        "experiment_accession": accs.get("experiment_accession", ""),
        "run_accession": accs.get("run_accession", ""),
    }


# ---------------------------------------------------------------------------
# Studies
# ---------------------------------------------------------------------------


def study_submit(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(StudySubmitRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.submit_studies(
                creds, req.records, test=req.test, modify=req.modify, hold_until=req.hold_until, public=req.public
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def study_list(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    return JsonResponse(ena_service.list_records(creds, "studies", **_list_params(request)), safe=False)


def study_prepare(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        req = _parse(StudyPrepareRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        prepared = ena_service.prepare_studies(req.export, dh_dir=views_core.DH_DIR)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    records = ena_service.records_from_container(prepared)
    return JsonResponse({"records": records})


# ---------------------------------------------------------------------------
# Samples
# ---------------------------------------------------------------------------


def sample_prepare(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        req = _parse(PrepareRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        prepared = ena_service.prepare_samples(req.export, where=req.where)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    records = ena_service.records_from_container(prepared)
    return JsonResponse({"prepared": prepared, "records": records, "count": len(records)})


def sample_submit(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(SampleSubmitRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.submit_samples(
                creds,
                req.records,
                test=req.test,
                modify=req.modify,
                checklist=req.checklist,
                hold_until=req.hold_until,
                public=req.public,
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def sample_list(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    return JsonResponse(ena_service.list_records(creds, "samples", **_list_params(request)), safe=False)


# ---------------------------------------------------------------------------
# Records browser + lifecycle actions
# ---------------------------------------------------------------------------


def records_list(request: HttpRequest, entity: str) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        return JsonResponse(ena_service.list_records(creds, entity, **_list_params(request)), safe=False)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def records_action(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(ActionRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        return JsonResponse(
            ena_service.run_action(
                creds, req.action, req.accession, test=req.test, alias=req.alias, hold_until=req.hold_until
            )
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def records_fields(request: HttpRequest, entity: str) -> JsonResponse:
    """Current values of the editable fields for the given accessions."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(FieldsRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    try:
        fields = ena_service.read_editable_fields(creds, entity, req.accessions, test=req.test)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - surface it; a Django 500 page shows the UI nothing
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse({"fields": fields})


def _modify(request: HttpRequest, *, submit: bool) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(ModifyRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    records = [r.model_dump() for r in req.records]
    call = ena_service.modify_records if submit else ena_service.preview_modify_records
    try:
        result = call(creds, req.entity, records, test=req.test, submission_alias=MODIFY_ALIAS)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:  # noqa: BLE001 - surface it; a Django 500 page shows the UI nothing
        return JsonResponse({"detail": f"{type(exc).__name__}: {exc}"}, status=502)
    return JsonResponse(result)


def records_modify_preview(request: HttpRequest) -> JsonResponse:
    return _modify(request, submit=False)


def records_modify(request: HttpRequest) -> JsonResponse:
    # This app exists to submit. Write mode is an explicit per-session opt-in in the UI,
    # and lifecycle actions keep confirming as they already do.
    return _modify(request, submit=True)


# ---------------------------------------------------------------------------
# Reads (browser-bridged): suggest / plan / result
# ---------------------------------------------------------------------------


def reads_suggest(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(SuggestRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    samples = ena_service.list_records(creds, "samples", test=req.test, max_results=req.max_results)
    return JsonResponse({"groups": read_assign.suggest(req.groups, samples), "samples": samples})


def reads_plan(request: HttpRequest) -> JsonResponse:
    """Decide, per run, whether to upload or skip, and build manifest text for
    the runs to upload. Skips (already-in-ENA / cached) are recorded in the
    ledger here; the browser only uploads the runs marked ``action == "submit"``.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    creds, err = webin_creds.from_request(request)
    if err:
        return err
    try:
        req = _parse(ReadsPlanRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    if not req.runs:
        return JsonResponse({"detail": "No runs provided"}, status=422)

    has_session = bool(req.session_name)
    force = req.force_reupload

    def stable_alias(run_name: str) -> str | None:
        if not has_session:
            return None  # one-off: manifest uses a timestamped alias, no ledger
        return session_run_alias(req.session_name, run_name)

    # Pre-compute stable aliases + a single ENA lookup for the batch.
    stable_by_name: dict[str, str | None] = {}
    candidate_aliases: set[str] = set()
    for idx, run in enumerate(req.runs, start=1):
        name = run.get("NAME", f"run{idx}")
        stable = stable_alias(name)
        stable_by_name[name] = stable
        if stable and not (force or run.get("reupload", False)):
            candidate_aliases.add(stable)

    existing: dict[str, dict[str, str]] = {}
    warnings: list[str] = []
    if has_session and not force and candidate_aliases:
        try:
            existing = ena_service.lookup_existing_runs(creds, candidate_aliases, test=req.test)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not check ENA for existing runs ({exc}); proceeding to submit.")

    plan: list[dict[str, Any]] = []
    for idx, run in enumerate(req.runs, start=1):
        name = run.get("NAME", f"run{idx}")
        stable = stable_by_name[name]
        run_forced = force or run.get("reupload", False)

        # Resume short-circuits (only with a session + stable alias). The browser
        # owns the ledger and sends it in ``req.ledger``; skip rows it relays back
        # to its own store. ``already_in_ena`` is detected fresh against ENA here.
        if has_session and stable and not run_forced:
            ledger = req.ledger.get(name)
            if (
                ledger
                and ledger.get("status") in (STATUS_DONE, STATUS_ALREADY_IN_ENA)
                and (ledger.get("run_accession") or ledger.get("experiment_accession"))
            ):
                plan.append({**_skip_result(run, name, stable, ledger, "cached"), "action": "skip"})
                continue
            if stable in existing:
                accs = existing[stable]
                plan.append({**_skip_result(run, name, stable, accs, "already_in_ena"), "action": "skip"})
                continue

        # Manifest alias: stable by default; fresh timestamped one on re-upload
        # (ENA aliases are permanent).
        manifest_alias: str | None = stable
        if stable and run_forced:
            manifest_alias = f"{stable}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            alias, manifest_text = read_assign.build_manifest_text(run, alias=manifest_alias)
        except ValueError as exc:
            plan.append(
                {
                    "name": name,
                    "action": "skip",
                    "success": False,
                    "skipped": True,
                    "reason": "invalid",
                    "messages": str(exc),
                }
            )
            continue

        plan.append(
            {
                "name": name,
                "action": "submit",
                "alias": alias,
                "stable_alias": stable,
                "manifest_filename": f"{alias}.manifest",
                "manifest_text": manifest_text,
                "sample": run.get("SAMPLE", ""),
                "study": run.get("STUDY", ""),
            }
        )

    return JsonResponse({"plan": plan, "warnings": warnings})


def reads_result(request: HttpRequest) -> JsonResponse:
    """Parse the helper-run upload outcome the browser relays back. Stateless:
    the browser persists the ledger row + log itself (see reads.js)."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        req = _parse(ReadsResultRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)

    accs = read_assign.parse_accessions(req.log.splitlines()) if req.log else {}
    if req.experiment_accession:
        accs["experiment_accession"] = req.experiment_accession
    if req.run_accession:
        accs["run_accession"] = req.run_accession

    result = {
        "name": req.name,
        "alias": req.alias,
        "sample": req.sample,
        "study": req.study,
        "exit_code": req.exit_code,
        "success": req.exit_code == 0,
        "skipped": False,
        **accs,
    }
    return JsonResponse({"result": result})
