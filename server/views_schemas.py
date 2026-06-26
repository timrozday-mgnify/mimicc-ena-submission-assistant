"""Schema library: list/save/delete, import/merge from ENA XML/XSD/YAML
sources, and select a schema for the sample/experiment DataHarmonizer grids.
"""

from __future__ import annotations

import json
import logging
import os
import pathlib
import shutil
import tempfile

import schema_service
import views_core
from django.http import HttpRequest, HttpResponse, HttpResponseNotAllowed, JsonResponse
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class SchemaSaveRequest(BaseModel):
    name: str
    yaml: str


class SchemaSelectRequest(BaseModel):
    role: str  # "sample" | "experiment"
    schema_id: str | None = None
    yaml: str | None = None


def schemas_collection(request: HttpRequest) -> JsonResponse:
    if request.method == "GET":
        return JsonResponse(schema_service.list_schemas(), safe=False)
    if request.method == "POST":
        try:
            req = SchemaSaveRequest.model_validate(json.loads(request.body))
        except (ValidationError, json.JSONDecodeError) as exc:
            return JsonResponse({"detail": str(exc)}, status=422)
        try:
            schema_id = schema_service.save_schema(req.name, req.yaml)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"id": schema_id})
    return HttpResponseNotAllowed(["GET", "POST"])


def schemas_ena_sources(request: HttpRequest) -> JsonResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    return JsonResponse(schema_service.list_ena_sources())


def schemas_detail(request: HttpRequest, schema_id: str) -> HttpResponse:
    if request.method == "GET":
        try:
            return JsonResponse({"yaml": schema_service.read_schema(schema_id)})
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
    if request.method == "DELETE":
        try:
            schema_service.delete_schema(schema_id)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
        return JsonResponse({"status": "deleted"})
    return HttpResponseNotAllowed(["GET", "DELETE"])


def schemas_export(request: HttpRequest, schema_id: str) -> HttpResponse:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])
    try:
        yaml_text = schema_service.read_schema(schema_id)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=404)
    response = HttpResponse(yaml_text, content_type="application/yaml")
    response["Content-Disposition"] = f'attachment; filename="{schema_id}.yaml"'
    return response


def schemas_import(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source_ids = request.POST.getlist("source_ids")
    schema_ids = request.POST.getlist("schema_ids")
    name = request.POST.get("name") or None
    title = request.POST.get("title") or None
    include = request.POST.getlist("include") or None
    exclude = request.POST.getlist("exclude") or None
    files = [f for f in request.FILES.getlist("files") if f.name]

    tmpdir: pathlib.Path | None = None
    upload_paths: list[pathlib.Path] = []
    try:
        if files:
            tmpdir = pathlib.Path(tempfile.mkdtemp(prefix="mimicc-schema-import-"))
            for f in files:
                dest = tmpdir / f.name
                dest.write_bytes(f.read())
                upload_paths.append(dest)
        try:
            yaml_text = schema_service.import_build(
                source_ids=source_ids or None,
                schema_ids=schema_ids or None,
                upload_paths=upload_paths or None,
                name=name,
                title=title,
                include=include,
                exclude=exclude,
            )
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"yaml": yaml_text})
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)


def schemas_import_file(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    file = request.FILES.get("file")
    if file is None:
        return JsonResponse({"detail": "A file is required"}, status=422)
    suffix = pathlib.Path(file.name or "").suffix or ".yaml"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        tmp_path.write_bytes(file.read())
        try:
            yaml_text = schema_service.import_build(upload_paths=[tmp_path])
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=400)
        return JsonResponse({"yaml": yaml_text})
    finally:
        tmp_path.unlink(missing_ok=True)


def schemas_select(request: HttpRequest) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    try:
        req = SchemaSelectRequest.model_validate(json.loads(request.body))
    except (ValidationError, json.JSONDecodeError) as exc:
        return JsonResponse({"detail": str(exc)}, status=422)
    if req.schema_id is None and req.yaml is None:
        return JsonResponse({"detail": "Provide either schema_id or yaml"}, status=422)
    yaml_text = req.yaml
    if yaml_text is None:
        try:
            yaml_text = schema_service.read_schema(req.schema_id)
        except ValueError as exc:
            return JsonResponse({"detail": str(exc)}, status=404)
    try:
        result = schema_service.select_for_grid_result(
            req.role,
            yaml_text,
            dh_dir=views_core.DH_DIR,
            require_existing_template=True,
        )
    except ValueError as exc:
        logger.exception("Failed to select schema for role=%s schema_id=%s", req.role, req.schema_id)
        return JsonResponse({"detail": str(exc)}, status=400)
    except Exception as exc:
        logger.exception("Unexpected schema selection failure for role=%s schema_id=%s", req.role, req.schema_id)
        return JsonResponse({"detail": f"Failed to compile/install schema: {exc}"}, status=500)
    return JsonResponse(result)
