"""Authentication (login/logout/me) + account management (admin)."""

from __future__ import annotations

import json

import auth
from django.contrib.auth import login as dj_login
from django.contrib.auth import logout as dj_logout
from django.http import HttpRequest, HttpResponseNotAllowed, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from pydantic import BaseModel, ValidationError


class LoginRequest(BaseModel):
    username: str
    password: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    is_admin: bool = False


class PasswordRequest(BaseModel):
    password: str


def _bad_request(exc: ValidationError) -> JsonResponse:
    return JsonResponse({"detail": str(exc)}, status=422)


@csrf_exempt  # no login cookie exists yet for this first request
def login(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        req = LoginRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return _bad_request(exc)
    user = auth.authenticate(req.username, req.password)
    if user is None:
        return JsonResponse({"detail": "Invalid username or password"}, status=401)
    dj_login(request, user)
    return JsonResponse({"status": "ok", "user": auth.user_to_dict(user)})


def logout(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    dj_logout(request)
    return JsonResponse({"status": "ok"})


def me(request: HttpRequest) -> JsonResponse:
    user, err = auth.current_user(request)
    if err:
        return err
    return JsonResponse({"user": auth.user_to_dict(user), "deployment_mode": auth.deployment_mode()})


def users_collection(request: HttpRequest) -> JsonResponse:
    _admin, err = auth.require_admin(request)
    if err:
        return err
    if request.method == "GET":
        return JsonResponse(auth.list_users(), safe=False)
    if request.method == "POST":
        try:
            req = UserCreateRequest.model_validate(json.loads(request.body))
        except (ValidationError, json.JSONDecodeError) as exc:
            return _bad_request(exc)
        try:
            return JsonResponse(auth.create_user(req.username, req.password, is_admin=req.is_admin))
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
    return HttpResponseNotAllowed(["GET", "POST"])


def users_delete(request: HttpRequest, user_id: int) -> JsonResponse:
    if request.method != "DELETE":
        return HttpResponseNotAllowed(["DELETE"])
    _admin, err = auth.require_admin(request)
    if err:
        return err
    try:
        auth.delete_user(user_id)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({"status": "deleted"})


def users_set_password(request: HttpRequest, user_id: int) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    _admin, err = auth.require_admin(request)
    if err:
        return err
    try:
        req = PasswordRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return _bad_request(exc)
    try:
        auth.set_password(user_id, req.password)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=400)
    return JsonResponse({"status": "ok"})
