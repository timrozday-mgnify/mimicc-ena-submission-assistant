"""Persistent submission sessions, backed by the Django ORM (Postgres).

A *session* groups everything about one ENA submission so it can be reopened
later with all UI state restored. Where the single-user app kept a SQLite
registry plus per-session files on disk, this stores everything in the database,
owned per user:

  * ``SubmissionSession`` — the registry row plus the full UI snapshot
    (``state``), the DataHarmonizer exports (``dh_export_sample`` /
    ``dh_export_experiment``) and the reads log (``reads_log``).
  * ``ReadsRun`` — the per-run reads submission ledger (resumability).

Webin credentials are never stored here — they stay in server memory only.

Access control is by ``owner``: ``create_session`` / ``list_sessions`` /
``get_session`` take a user and scope to it. The state/dh/reads helpers are
keyed by ``session_id`` only and assume the caller has already authorised
access via ``get_session(session_id, owner=user)``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

import dbsetup

dbsetup.ensure()

from django.db import IntegrityError  # noqa: E402
from django.utils import timezone  # noqa: E402
from orm import models  # noqa: E402

# reads ledger status values (re-exported from the model for call-site compat).
STATUS_PENDING = models.STATUS_PENDING
STATUS_DONE = models.STATUS_DONE
STATUS_ALREADY_IN_ENA = models.STATUS_ALREADY_IN_ENA
STATUS_FAILED = models.STATUS_FAILED

_VALID_DH_KINDS = ("sample", "experiment")
_DH_FIELDS = {
    "sample": ("dh_export_sample", "dh_export_sample_saved_at"),
    "experiment": ("dh_export_experiment", "dh_export_experiment_saved_at"),
}


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _now() -> datetime:
    return timezone.now()


# ---------------------------------------------------------------------------
# Stable, account-unique alias for a run within a session
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    """Filesystem/alias-safe slug: keep word chars, collapse the rest to '-'."""
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", str(text).strip()).strip("-")
    return s or "x"


def session_run_alias(session_name: str, run_name: str) -> str:
    """Stable per-run alias. Session names are unique per user, so this is
    unique per account and identical across re-submits — which is what lets us
    detect a run that is already in ENA."""
    return f"{_slug(session_name)}_{_slug(run_name)}"


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------


def _session_to_dict(obj: models.SubmissionSession) -> dict[str, Any]:
    return {
        "id": obj.id,
        "name": obj.name,
        "created_at": _iso(obj.created_at),
        "updated_at": _iso(obj.updated_at),
        "test_env": bool(obj.test_env),
    }


def create_session(name: str, owner, *, test_env: bool = True) -> dict[str, Any]:
    name = (name or "").strip()
    if not name:
        raise ValueError("Session name is required")
    obj = models.SubmissionSession(owner=owner, name=name, test_env=test_env)
    try:
        obj.save()
    except IntegrityError as exc:
        raise ValueError(f"A session named {name!r} already exists") from exc
    return _session_to_dict(obj)


def list_sessions(owner) -> list[dict[str, Any]]:
    qs = models.SubmissionSession.objects.filter(owner=owner).order_by("-updated_at")
    return [_session_to_dict(o) for o in qs]


def get_session(session_id: str, owner=None) -> dict[str, Any] | None:
    obj = _get_obj(session_id, owner)
    return _session_to_dict(obj) if obj else None


def _get_obj(session_id: str, owner=None) -> models.SubmissionSession | None:
    qs = models.SubmissionSession.objects.filter(pk=session_id)
    if owner is not None:
        qs = qs.filter(owner=owner)
    return qs.first()


def touch_session(session_id: str) -> None:
    models.SubmissionSession.objects.filter(pk=session_id).update(updated_at=_now())


def set_test_env(session_id: str, test_env: bool) -> None:
    models.SubmissionSession.objects.filter(pk=session_id).update(test_env=bool(test_env), updated_at=_now())


def delete_session(session_id: str) -> None:
    # ReadsRun rows cascade via the FK on_delete=CASCADE.
    models.SubmissionSession.objects.filter(pk=session_id).delete()


# ---------------------------------------------------------------------------
# Per-session state / DataHarmonizer export / reads log
# ---------------------------------------------------------------------------


def save_state(session_id: str, state: Any) -> str:
    now = _now()
    models.SubmissionSession.objects.filter(pk=session_id).update(state=state, state_saved_at=now, updated_at=now)
    return now.isoformat()


def load_state(session_id: str) -> Any | None:
    obj = _get_obj(session_id)
    return obj.state if obj else None


def save_dh_export(session_id: str, export: Any, kind: str = "sample") -> str:
    if kind not in _VALID_DH_KINDS:
        raise ValueError(f"Unknown DataHarmonizer export kind {kind!r}; expected one of {_VALID_DH_KINDS}")
    field, saved_field = _DH_FIELDS[kind]
    now = _now()
    models.SubmissionSession.objects.filter(pk=session_id).update(
        **{field: export, saved_field: now, "updated_at": now}
    )
    return now.isoformat()


def load_dh_export(session_id: str, kind: str = "sample") -> tuple[Any | None, str | None]:
    if kind not in _VALID_DH_KINDS:
        raise ValueError(f"Unknown DataHarmonizer export kind {kind!r}; expected one of {_VALID_DH_KINDS}")
    field, saved_field = _DH_FIELDS[kind]
    obj = _get_obj(session_id)
    if obj is None:
        return None, None
    return getattr(obj, field), _iso(getattr(obj, saved_field))


def append_reads_log(session_id: str, text: str) -> None:
    obj = _get_obj(session_id)
    if obj is None:
        return
    obj.reads_log = (obj.reads_log or "") + text + "\n"
    obj.save(update_fields=["reads_log", "updated_at"])


def set_reads_log(session_id: str, text: str) -> None:
    models.SubmissionSession.objects.filter(pk=session_id).update(reads_log=text, updated_at=_now())


def read_reads_log(session_id: str) -> str:
    obj = _get_obj(session_id)
    return (obj.reads_log if obj else "") or ""


# ---------------------------------------------------------------------------
# reads_runs ledger (resumability)
# ---------------------------------------------------------------------------


def _reads_run_to_dict(obj: models.ReadsRun) -> dict[str, Any]:
    return {
        "run_name": obj.run_name,
        "stable_alias": obj.stable_alias,
        "status": obj.status,
        "experiment_accession": obj.experiment_accession,
        "run_accession": obj.run_accession,
        "submitted_alias": obj.submitted_alias,
        "submitted_at": _iso(obj.submitted_at),
    }


def upsert_reads_run(
    session_id: str,
    run_name: str,
    stable_alias: str,
    status: str,
    *,
    experiment_accession: str | None = None,
    run_accession: str | None = None,
    submitted_alias: str | None = None,
) -> None:
    models.ReadsRun.objects.update_or_create(
        session_id=session_id,
        run_name=run_name,
        defaults={
            "stable_alias": stable_alias,
            "status": status,
            "experiment_accession": experiment_accession,
            "run_accession": run_accession,
            "submitted_alias": submitted_alias,
            "submitted_at": _now(),
        },
    )


def get_reads_run(session_id: str, run_name: str) -> dict[str, Any] | None:
    obj = models.ReadsRun.objects.filter(session_id=session_id, run_name=run_name).first()
    return _reads_run_to_dict(obj) if obj else None


def list_reads_runs(session_id: str) -> list[dict[str, Any]]:
    qs = models.ReadsRun.objects.filter(session_id=session_id).order_by("run_name")
    return [_reads_run_to_dict(o) for o in qs]
