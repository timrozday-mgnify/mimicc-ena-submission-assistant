"""Tests for Records API report enrichment."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import ena_service
from ena_api.models import ExperimentReport, RunReport
from ena_api.reports import _coerce


class _ReportRow(dict):
    def model_dump(self):
        return dict(self)


# ---------------------------------------------------------------------------
# _coerce: raw Reports API field-name handling
#
# The Webin Reports API has no published schema, so reports.py's alias table
# is necessarily a best guess. These tests pin down (a) the alias variants we
# accept and (b) that any field _coerce doesn't recognise still survives into
# model_dump() — that passthrough is what makes a real, unrecognised key
# visible (e.g. in the Records tab's debug log) instead of silently dropping
# the data that the linking columns need.
# ---------------------------------------------------------------------------


def test_coerce_run_accepts_accession_style_keys():
    run = _coerce(
        {"runAccession": "ERR111", "experimentAccession": "ERX111", "releaseStatus": "PRIVATE"},
        RunReport,
    )
    assert run.accession == "ERR111"
    assert run.experiment_accession == "ERX111"
    assert run.status == "PRIVATE"


def test_coerce_run_accepts_id_style_keys():
    """Some Reports API fields may use "...Id" rather than "...Accession"."""
    run = _coerce(
        {"accession": "ERR111", "experimentId": "ERX111", "studyId": "ERP111", "sampleId": "ERS111"},
        RunReport,
    )
    assert run.experiment_accession == "ERX111"
    assert run.study_accession == "ERP111"
    assert run.sample_accession == "ERS111"


def test_coerce_experiment_accepts_accession_style_keys():
    exp = _coerce(
        {"accession": "ERX111", "studyAccession": "ERP111", "sampleAccession": "ERS111"},
        ExperimentReport,
    )
    assert exp.study_accession == "ERP111"
    assert exp.sample_accession == "ERS111"


def test_coerce_preserves_unrecognised_fields_for_debugging():
    """Any raw key _coerce doesn't map to a known field must still appear in
    model_dump() — otherwise a real-world key-name mismatch (the Reports API
    using some field name reports.py doesn't anticipate) is impossible to
    diagnose from the running app."""
    run = _coerce({"accession": "ERR111", "totallyUnexpectedKey": "mystery-value"}, RunReport)
    assert run.model_dump()["totallyUnexpectedKey"] == "mystery-value"


def test_run_records_are_enriched_with_experiment_relationships(monkeypatch):
    reports = SimpleNamespace(
        list_runs=lambda max_results: [
            _ReportRow(
                {
                    "accession": "ERR111",
                    "alias": "runA",
                    "experiment_accession": "ERX111",
                    "status": "PRIVATE",
                }
            )
        ],
        list_experiments=lambda max_results: [
            _ReportRow(
                {
                    "accession": "ERX111",
                    "alias": "expA",
                    "study_accession": "ERP111",
                    "sample_accession": "ERS111",
                    "status": "PRIVATE",
                }
            )
        ],
    )

    @contextmanager
    def fake_webin_client(creds, test):
        yield SimpleNamespace(reports=reports)

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)

    rows = ena_service.list_records(
        ena_service.Credentials(username="Webin-test", password="secret"),
        "runs",
        test=True,
    )

    assert rows == [
        {
            "accession": "ERR111",
            "alias": "runA",
            "experiment_accession": "ERX111",
            "study_accession": "ERP111",
            "sample_accession": "ERS111",
            "status": "PRIVATE",
        }
    ]


def test_run_record_direct_relationships_are_not_overwritten(monkeypatch):
    reports = SimpleNamespace(
        list_runs=lambda max_results: [
            _ReportRow(
                {
                    "accession": "ERR111",
                    "alias": "runA",
                    "experiment_accession": "ERX111",
                    "study_accession": "ERPdirect",
                    "sample_accession": "ERSdirect",
                    "status": "PRIVATE",
                }
            )
        ],
        list_experiments=lambda max_results: [
            _ReportRow(
                {
                    "accession": "ERX111",
                    "study_accession": "ERP111",
                    "sample_accession": "ERS111",
                }
            )
        ],
    )

    @contextmanager
    def fake_webin_client(creds, test):
        yield SimpleNamespace(reports=reports)

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)

    rows = ena_service.list_records(
        ena_service.Credentials(username="Webin-test", password="secret"),
        "runs",
        test=True,
    )

    assert rows[0]["study_accession"] == "ERPdirect"
    assert rows[0]["sample_accession"] == "ERSdirect"
