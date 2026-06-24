"""ORM schema for the multi-user hosted app.

Accounts reuse Django's built-in ``auth.User`` (username/password hashing,
``is_superuser`` for the admin role); web logins use
``django.contrib.sessions``. Everything else is owned per-user:

  * ``SubmissionSession`` — one named ENA submission; folds the former
    ``state.json`` / ``dh_export*.json`` / ``reads.log`` files into columns.
  * ``ReadsRun`` — the per-run reads submission ledger (resumability).
"""

from __future__ import annotations

import uuid

from django.contrib.auth.models import User
from django.db import models

# ReadsRun.status values (mirrors the former session_store constants).
STATUS_PENDING = "pending"
STATUS_DONE = "done"
STATUS_ALREADY_IN_ENA = "already_in_ena"
STATUS_FAILED = "failed"


def _new_session_id() -> str:
    return uuid.uuid4().hex[:12]


class SubmissionSession(models.Model):
    """A named ENA submission session, owned by a user.

    ``state`` is the full UI snapshot; ``dh_export_sample`` / ``dh_export_experiment``
    are the DataHarmonizer grid exports; ``reads_log`` is the accumulated
    reads-submission log — all previously per-session files on disk.
    """

    id = models.CharField(max_length=12, primary_key=True, default=_new_session_id, editable=False)
    owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name="submission_sessions")
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    test_env = models.BooleanField(default=True)

    state = models.JSONField(null=True, blank=True)
    state_saved_at = models.DateTimeField(null=True, blank=True)
    dh_export_sample = models.JSONField(null=True, blank=True)
    dh_export_sample_saved_at = models.DateTimeField(null=True, blank=True)
    dh_export_experiment = models.JSONField(null=True, blank=True)
    dh_export_experiment_saved_at = models.DateTimeField(null=True, blank=True)
    reads_log = models.TextField(default="", blank=True)

    class Meta:
        # Names are unique per user (was globally unique in the single-user app).
        constraints = [models.UniqueConstraint(fields=["owner", "name"], name="uniq_session_name_per_owner")]
        ordering = ["-updated_at"]


class ReadsRun(models.Model):
    """Per-run reads submission ledger row (the source of truth for resume)."""

    session = models.ForeignKey(SubmissionSession, on_delete=models.CASCADE, related_name="reads_runs")
    run_name = models.CharField(max_length=255)
    stable_alias = models.CharField(max_length=512)
    status = models.CharField(max_length=32)
    experiment_accession = models.CharField(max_length=64, null=True, blank=True)
    run_accession = models.CharField(max_length=64, null=True, blank=True)
    submitted_alias = models.CharField(max_length=512, null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["session", "run_name"], name="uniq_run_per_session")]
        ordering = ["run_name"]
