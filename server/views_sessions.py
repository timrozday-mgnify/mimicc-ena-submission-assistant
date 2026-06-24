"""Submission sessions (named, persisted in the database, owned per user)."""

from __future__ import annotations

import json
from typing import Any

import auth
import session_store
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError


class SessionCreateRequest(BaseModel):
    name: str
    test_env: bool = True


class SessionStateRequest(BaseModel):
    state: dict[str, Any]
    test_env: bool | None = None


class DhExportRequest(BaseModel):
    export: dict[str, Any]


def _require_session(session_id: str, user) -> tuple[dict[str, Any] | None, JsonResponse | None]:
    session = session_store.get_session(session_id, owner=user)
    if session is None:
        return None, JsonResponse({"detail": "Session not found"}, status=404)
    return session, None


def sessions_collection(request: HttpRequest) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    if request.method == "GET":
        return JsonResponse(session_store.list_sessions(user), safe=False)
    if request.method == "POST":
        try:
            req = SessionCreateRequest.model_validate(json.loads(request.body))
        except (ValidationError, json.JSONDecodeError) as exc:
            return JsonResponse({"detail": str(exc)}, status=422)
        try:
            return JsonResponse(session_store.create_session(req.name, user, test_env=req.test_env))
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
    return HttpResponseNotAllowed(["GET", "POST"])


def sessions_detail(request: HttpRequest, session_id: str) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    if request.method == "GET":
        session, err = _require_session(session_id, user)
        if err:
            return err
        export, dh_saved_at = session_store.load_dh_export(session_id, "sample")
        exp_export, exp_dh_saved_at = session_store.load_dh_export(session_id, "experiment")
        return JsonResponse(
            {
                "session": session,
                "state": session_store.load_state(session_id),
                "dh_export": export,
                "dh_saved_at": dh_saved_at,
                "exp_dh_export": exp_export,
                "exp_dh_saved_at": exp_dh_saved_at,
                "reads_log": session_store.read_reads_log(session_id),
                "reads_runs": session_store.list_reads_runs(session_id),
            }
        )
    if request.method == "DELETE":
        _session, err = _require_session(session_id, user)
        if err:
            return err
        session_store.delete_session(session_id)
        return JsonResponse({"status": "deleted"})
    return HttpResponseNotAllowed(["GET", "DELETE"])


def sessions_state(request: HttpRequest, session_id: str) -> JsonResponse:
    if request.method != "PUT":
        return HttpResponseNotAllowed(["PUT"])
    user, err = auth.current_user(request)
    if err:
        return err
    _session, err = _require_session(session_id, user)
    if err:
        return err
    try:
        req = SessionStateRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    if req.test_env is not None:
        session_store.set_test_env(session_id, req.test_env)
    saved_at = session_store.save_state(session_id, req.state)
    return JsonResponse({"status": "ok", "saved_at": saved_at})


def sessions_dh_export(request: HttpRequest, session_id: str, kind: str) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    _session, err = _require_session(session_id, user)
    if err:
        return err
    if request.method == "POST":
        try:
            req = DhExportRequest.model_validate(json.loads(request.body))
        except (ValidationError, json.JSONDecodeError) as exc:
            return JsonResponse({"detail": str(exc)}, status=422)
        try:
            saved_at = session_store.save_dh_export(session_id, req.export, kind)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"status": "ok", "saved_at": saved_at})
    if request.method == "GET":
        try:
            export, saved_at = session_store.load_dh_export(session_id, kind)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"export": export, "saved_at": saved_at})
    return HttpResponseNotAllowed(["GET", "POST"])
