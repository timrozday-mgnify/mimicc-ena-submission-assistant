from __future__ import annotations

import views_core
import views_records
import views_schemas
from django.urls import path, re_path

urlpatterns = [
    path("", views_core.index),
    path("api/health", views_core.health),
    # Studies / samples / records / reads
    path("api/study/prepare", views_records.study_prepare),
    path("api/study/submit", views_records.study_submit),
    path("api/study/list", views_records.study_list),
    path("api/sample/prepare", views_records.sample_prepare),
    path("api/sample/submit", views_records.sample_submit),
    path("api/sample/list", views_records.sample_list),
    path("api/records/action", views_records.records_action),
    # Before the <entity> catch-all below, which would otherwise swallow these.
    path("api/records/modify/preview", views_records.records_modify_preview),
    path("api/records/modify", views_records.records_modify),
    path("api/records/<str:entity>/fields", views_records.records_fields),
    path("api/records/<str:entity>", views_records.records_list),
    path("api/reads/suggest", views_records.reads_suggest),
    path("api/reads/plan", views_records.reads_plan),
    path("api/reads/result", views_records.reads_result),
    # Schema library
    path("api/schemas", views_schemas.schemas_collection),
    path("api/schemas/ena-sources", views_schemas.schemas_ena_sources),
    path("api/schemas/import", views_schemas.schemas_import),
    path("api/schemas/import-file", views_schemas.schemas_import_file),
    path("api/schemas/select", views_schemas.schemas_select),
    path("api/schemas/<str:schema_id>/export", views_schemas.schemas_export),
    path("api/schemas/<str:schema_id>", views_schemas.schemas_detail),
    # Static / DataHarmonizer bundle
    re_path(r"^static/(?P<path>.*)$", views_core.static_serve_view, {"document_root": str(views_core.STATIC_DIR)}),
    path("dh/", views_core.serve_dh),
    re_path(r"^dh/(?P<path>.*)$", views_core.serve_dh),
    re_path(
        r"^templates/(?P<path>.*)$",
        views_core.static_serve_view,
        {"document_root": str(views_core.DH_TEMPLATES_DIR)},
    ),
]
