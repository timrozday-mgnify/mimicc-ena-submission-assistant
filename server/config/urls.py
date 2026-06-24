from __future__ import annotations

import views_auth
import views_core
import views_credentials
import views_records
import views_schemas
import views_sessions
from django.urls import path, re_path

urlpatterns = [
    path("", views_core.index),
    path("api/health", views_core.health),
    # Auth / accounts
    path("api/auth/login", views_auth.login),
    path("api/auth/logout", views_auth.logout),
    path("api/auth/me", views_auth.me),
    path("api/admin/users", views_auth.users_collection),
    path("api/admin/users/<int:user_id>", views_auth.users_delete),
    path("api/admin/users/<int:user_id>/password", views_auth.users_set_password),
    # Webin credentials
    path("api/credentials", views_credentials.credentials_collection),
    # Sessions
    path("api/sessions", views_sessions.sessions_collection),
    path("api/sessions/<str:session_id>", views_sessions.sessions_detail),
    path("api/sessions/<str:session_id>/state", views_sessions.sessions_state),
    path("api/sessions/<str:session_id>/dh-export/<str:kind>", views_sessions.sessions_dh_export),
    # Studies / samples / records / reads
    path("api/study/submit", views_records.study_submit),
    path("api/study/list", views_records.study_list),
    path("api/sample/prepare", views_records.sample_prepare),
    path("api/sample/submit", views_records.sample_submit),
    path("api/sample/list", views_records.sample_list),
    path("api/records/action", views_records.records_action),
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
