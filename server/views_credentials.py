"""Webin (ENA) credentials — per-user, cache-backed only, never persisted to DB."""

from __future__ import annotations

import json

import auth
import credentials_store
import ena_service
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError


class CredentialsRequest(BaseModel):
    username: str
    password: str
    test: bool = True


def credentials_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "POST":
        return set_credentials(request)
    if request.method == "DELETE":
        return clear_credentials(request)
    return HttpResponseNotAllowed(["POST", "DELETE"])


def set_credentials(request: HttpRequest) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    try:
        req = CredentialsRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    username = req.username.strip()
    if not username or not req.password:
        return JsonResponse({"detail": "Username and password are required"}, status=422)
    creds = ena_service.Credentials(username=username, password=req.password)
    env_name = "test" if req.test else "production"
    try:
        ena_service.validate_credentials(creds, test=req.test)
    except PermissionError:
        return JsonResponse({"detail": f"Invalid Webin credentials for the ENA {env_name} service"}, status=401)
    except Exception as exc:  # noqa: BLE001
        return JsonResponse(
            {"detail": f"Could not validate Webin credentials against the ENA {env_name} service: {exc}"},
            status=502,
        )
    credentials_store.set_creds(user.id, username, req.password)
    return JsonResponse({"status": "ok", "username": username, "environment": env_name})


def clear_credentials(request: HttpRequest) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    credentials_store.clear_creds(user.id)
    return JsonResponse({"status": "cleared"})
