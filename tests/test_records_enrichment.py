"""Tests for Records API report enrichment."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import ena_service


class _ReportRow(dict):
    def model_dump(self):
        return dict(self)


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
