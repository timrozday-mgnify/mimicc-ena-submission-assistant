"""Studies, samples, the generic records browser/actions, and reads (browser-bridged).

Reads upload goes DIRECT from the user's machine to ENA via the local helper.
The server never touches local read files. It only:
  * suggest  — matches scanned read groups to ENA samples (server-side creds),
  * plan     — decides which runs to upload vs. skip (ledger + ENA lookup) and
               hands the browser the webin-cli manifest text for each upload,
  * result   — records the outcome the browser relays back from the helper.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

import auth
import credentials_store
import ena_service
import read_assign
import session_store
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError


class StudySubmitRequest(BaseModel):
    records: list[dict[str, Any]]
    test: bool = True
    modify: bool = False
    hold_until: str | None = None
    public: bool = False


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


class SuggestRequest(BaseModel):
    groups: list[dict[str, Any]]
    test: bool = True
    max_results: int = 5000


class ReadsPlanRequest(BaseModel):
    runs: list[dict[str, Any]]
    test: bool = True
    session_id: str | None = None
    force_reupload: bool = False


class ReadsResultRequest(BaseModel):
    session_id: str | None = None
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


def _require_session(session_id: str, user) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    session = session_store.get_session(session_id, owner=user)
    if session is None:
        return None, JsonResponse({"detail": "Session not found"}, status=404)
    return session, None


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
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
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
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
    if err:
        return err
    test = request.GET.get("test", "true").lower() != "false"
    status = request.GET.get("status", "all")
    max_results = int(request.GET.get("max_results", 5000))
    return JsonResponse(
        ena_service.list_records(creds, "studies", test=test, status=status, max_results=max_results), safe=False
    )


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
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
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
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
    if err:
        return err
    test = request.GET.get("test", "true").lower() != "false"
    status = request.GET.get("status", "all")
    max_results = int(request.GET.get("max_results", 5000))
    return JsonResponse(
        ena_service.list_records(creds, "samples", test=test, status=status, max_results=max_results), safe=False
    )


# ---------------------------------------------------------------------------
# Records browser + lifecycle actions
# ---------------------------------------------------------------------------


def records_list(request: HttpRequest, entity: str) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
    if err:
        return err
    test = request.GET.get("test", "true").lower() != "false"
    status = request.GET.get("status", "all")
    max_results = int(request.GET.get("max_results", 5000))
    try:
        return JsonResponse(
            ena_service.list_records(creds, entity, test=test, status=status, max_results=max_results), safe=False
        )
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)


def records_action(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
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


# ---------------------------------------------------------------------------
# Reads (browser-bridged): suggest / plan / result
# ---------------------------------------------------------------------------


def reads_suggest(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
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
    user, err = auth.current_user(request)
    if err:
        return err
    creds, err = credentials_store.get_creds(user)
    if err:
        return err
    try:
        req = _parse(ReadsPlanRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    if not req.runs:
        return JsonResponse({"detail": "No runs provided"}, status=422)

    session = None
    if req.session_id:
        session, err = _require_session(req.session_id, user)
        if err:
            return err
    force = req.force_reupload

    def stable_alias(run_name: str) -> str | None:
        if session is None:
            return None  # one-off: manifest uses a timestamped alias, no ledger
        return session_store.session_run_alias(session["name"], run_name)

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
    if session is not None and not force and candidate_aliases:
        try:
            existing = ena_service.lookup_existing_runs(creds, candidate_aliases, test=req.test)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"Could not check ENA for existing runs ({exc}); proceeding to submit.")

    plan: list[dict[str, Any]] = []
    for idx, run in enumerate(req.runs, start=1):
        name = run.get("NAME", f"run{idx}")
        stable = stable_by_name[name]
        run_forced = force or run.get("reupload", False)

        # Resume short-circuits (only with a session + stable alias).
        if session is not None and stable and not run_forced:
            ledger = session_store.get_reads_run(req.session_id, name)
            if (
                ledger
                and ledger["status"] in (session_store.STATUS_DONE, session_store.STATUS_ALREADY_IN_ENA)
                and (ledger.get("run_accession") or ledger.get("experiment_accession"))
            ):
                plan.append({**_skip_result(run, name, stable, ledger, "cached"), "action": "skip"})
                continue
            if stable in existing:
                accs = existing[stable]
                session_store.upsert_reads_run(
                    req.session_id,
                    name,
                    stable,
                    session_store.STATUS_ALREADY_IN_ENA,
                    experiment_accession=accs.get("experiment_accession") or None,
                    run_accession=accs.get("run_accession") or None,
                )
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
    """Record the outcome of a helper-run upload and update the ledger/log."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    user, err = auth.current_user(request)
    if err:
        return err
    try:
        req = _parse(ReadsResultRequest, request)
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)

    session = None
    if req.session_id:
        session, err = _require_session(req.session_id, user)
        if err:
            return err

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

    if session is not None:
        if req.log:
            session_store.append_reads_log(req.session_id, req.log.rstrip("\n"))
        stable = req.stable_alias or session_store.session_run_alias(session["name"], req.name)
        session_store.upsert_reads_run(
            req.session_id,
            req.name,
            stable,
            session_store.STATUS_DONE if req.exit_code == 0 else session_store.STATUS_FAILED,
            experiment_accession=accs.get("experiment_accession"),
            run_accession=accs.get("run_accession"),
            submitted_alias=req.alias,
        )
    return JsonResponse({"result": result})
