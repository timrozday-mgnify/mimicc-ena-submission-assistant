"""In-process API tests (httpx ASGITransport — no Docker, no network)."""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager

import conftest
import ena_service
import views_records

# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "library_presets" not in body
    assert "samples" in body["editable_columns"]
    assert body["ena_browser_available"] is True


# ---------------------------------------------------------------------------
# Credential gating (creds arrive as headers; absent => 401)
# ---------------------------------------------------------------------------


async def test_records_require_credentials(client):
    r = await client.get("/api/records/studies")
    assert r.status_code == 401


async def test_study_submit_requires_credentials(client):
    r = await client.post("/api/study/submit", json={"records": [{"alias": "x"}]})
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# Records browser + actions (ena_service mocked)
# ---------------------------------------------------------------------------


async def test_records_list(client, with_creds, monkeypatch):
    rows = [{"accession": "ERP1", "alias": "a", "title": "t", "status": "PRIVATE"}]
    monkeypatch.setattr(ena_service, "list_records", lambda *a, **k: rows)
    r = await client.get("/api/records/studies?test=true&status=all")
    assert r.status_code == 200
    assert r.json() == rows


async def test_records_list_runs(client, with_creds, monkeypatch):
    rows = [{"accession": "ERR1", "alias": "r1", "experiment_accession": "ERX1", "status": "PRIVATE"}]
    monkeypatch.setattr(ena_service, "list_records", lambda *a, **k: rows)
    r = await client.get("/api/records/runs?test=true&status=all")
    assert r.status_code == 200
    assert r.json() == rows


async def test_records_list_experiments(client, with_creds, monkeypatch):
    rows = [
        {
            "accession": "ERX1",
            "alias": "e1",
            "title": "Experiment 1",
            "study_accession": "ERP1",
            "sample_accession": "ERS1",
            "status": "PRIVATE",
        },
    ]
    monkeypatch.setattr(ena_service, "list_records", lambda *a, **k: rows)
    r = await client.get("/api/records/experiments?test=true&status=all")
    assert r.status_code == 200
    assert r.json() == rows


async def test_records_list_passes_full_fields_through(client, with_creds, monkeypatch):
    """The Reports API returns five columns; everything else (checklist
    attributes, taxon, library) only arrives when the listing asks for it."""
    seen: list[dict] = []
    monkeypatch.setattr(ena_service, "list_records", lambda *a, **k: seen.append(k) or [])

    await client.get("/api/records/samples?test=true")
    await client.get("/api/records/samples?test=true&full_fields=true")

    assert [k["full_fields"] for k in seen] == [False, True]


async def test_records_list_passes_criteria_through(client, with_creds, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(ena_service, "list_records", lambda *a, **k: seen.append(k) or [])

    await client.get("/api/records/samples?search=foo&linked_to=PRJEB1&unlinked=true")

    assert seen[0]["search"] == "foo"
    assert seen[0]["linked_to"] == "PRJEB1"
    assert seen[0]["unlinked"] is True


async def test_records_fields(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "read_editable_fields",
        lambda *a, **k: {"ERS1": {"alias": "a", "title": "t"}},
    )
    r = await client.post("/api/records/samples/fields", json={"accessions": ["ERS1"], "test": True})
    assert r.status_code == 200
    assert r.json() == {"fields": {"ERS1": {"alias": "a", "title": "t"}}}


async def test_records_modify_preview_does_not_submit(client, with_creds, monkeypatch):
    preview = {"manifests": [{"accession": "ERS1", "xml": "<SAMPLE_SET/>"}]}
    monkeypatch.setattr(ena_service, "preview_modify_records", lambda *a, **k: preview)

    def never(*a, **k):
        raise AssertionError("preview must not submit")

    monkeypatch.setattr(ena_service, "modify_records", never)

    r = await client.post(
        "/api/records/modify/preview",
        json={"entity": "samples", "records": [{"accession": "ERS1", "changes": {"title": "new"}}], "test": True},
    )
    assert r.status_code == 200
    assert r.json() == preview


async def test_records_modify_uses_submission_alias(client, with_creds, monkeypatch):
    seen: list[dict] = []
    monkeypatch.setattr(ena_service, "modify_records", lambda *a, **k: seen.append(k) or {"success": True})

    r = await client.post(
        "/api/records/modify",
        json={"entity": "samples", "records": [{"accession": "ERS1", "changes": {"title": "new"}}], "test": True},
    )
    assert r.status_code == 200
    assert seen[0]["submission_alias"] == views_records.MODIFY_ALIAS


async def test_records_modify_unexpected_error_is_502(client, with_creds, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("ENA said no")

    monkeypatch.setattr(ena_service, "modify_records", boom)
    r = await client.post(
        "/api/records/modify",
        json={"entity": "samples", "records": [{"accession": "ERS1", "changes": {"title": "new"}}], "test": True},
    )
    assert r.status_code == 502
    assert r.json()["detail"] == "RuntimeError: ENA said no"


async def test_records_unknown_entity(client, with_creds, monkeypatch):
    def boom(*a, **k):
        raise ValueError("Unknown entity 'frogs'")

    monkeypatch.setattr(ena_service, "list_records", boom)
    r = await client.get("/api/records/frogs")
    assert r.status_code == 400


async def test_records_action(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "run_action",
        lambda *a, **k: {"accession": "ERS1", "action": "release", "success": True, "messages": ""},
    )
    r = await client.post("/api/records/action", json={"action": "release", "accession": "ERS1", "test": True})
    assert r.status_code == 200
    assert r.json()["success"] is True


# ---------------------------------------------------------------------------
# Sample prepare (ena_service mocked) + study submit
# ---------------------------------------------------------------------------


async def test_sample_prepare(client, monkeypatch):
    container = {"Container": {"MIMICC_SampleExperiments": [{"alias": "s1"}]}}
    monkeypatch.setattr(ena_service, "prepare_samples", lambda export, where=None: container)
    r = await client.post("/api/sample/prepare", json={"export": {"any": "thing"}})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["records"] == [{"alias": "s1"}]


async def test_study_submit(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "submit_studies",
        lambda *a, **k: {
            "success": True,
            "accessions": [{"accession": "ERP9"}],
            "logs": ["INFO: Study submission accepted"],
        },
    )
    r = await client.post("/api/study/submit", json={"records": [{"alias": "x", "TITLE": "t"}], "test": True})
    assert r.status_code == 200
    body = r.json()
    assert body["accessions"][0]["accession"] == "ERP9"
    assert body["logs"] == ["INFO: Study submission accepted"]


async def test_study_submit_includes_failure_logs(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "submit_studies",
        lambda *a, **k: {
            "success": False,
            "accessions": [],
            "error": "receipt rejected",
            "logs": ["INFO: XSD validation passed", "INFO: Receipt: invalid study"],
        },
    )
    r = await client.post("/api/study/submit", json={"records": [{"alias": "x", "TITLE": "t"}], "test": True})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["logs"] == ["INFO: XSD validation passed", "INFO: Receipt: invalid study"]


def test_submit_studies_adds_stage_logs_when_toolkit_returns_failure(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

        @staticmethod
        def find_duplicates_by_alias_title(*_args, **_kwargs):
            return {}

        @staticmethod
        def classify_duplicates(records, *_args, **_kwargs):
            return [], records, []

    toolkit.common = FakeCommon()
    study_xml = b'<WEBIN><PROJECT alias="study-a"><TITLE>Study A</TITLE></PROJECT></WEBIN>'
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: study_xml,
        validate_manifest=lambda *_args, **_kwargs: (
            True,
            ["XML is well-formed", "OK: PROJECT 'study-a' has required elements"],
        ),
        submit_manifest=lambda *_args, **_kwargs: (False, [], ["Study title is not unique"]),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "TITLE": "Study A"}],
        test=True,
    )

    assert result["success"] is False
    assert result["error"] == "ENA rejected the study submission; see receipt messages in the log."
    assert "INFO: Building ENA study XML manifest: action=ADD, records=1" in result["logs"]
    assert f"INFO: Built ENA study XML manifest: bytes={len(study_xml)}" in result["logs"]
    assert (
        "INFO: Built native ENA project identity: "
        "PROJECT[1]: alias='study-a', TITLE='Study A', NAME_present=False, DESCRIPTION_present=False"
    ) in result["logs"]
    assert "INFO: Validating study XML manifest against ENA.project.xsd" in result["logs"]
    assert "INFO:   XML is well-formed" in result["logs"]
    assert "INFO: XSD validation result: valid=True" in result["logs"]
    assert "INFO: Pre-validation passed; submitting study XML to ENA (TEST)" in result["logs"]
    assert "INFO: ENA receipt parsed: success=False, accession_records=0, message_count=1" in result["logs"]
    assert "ERROR: Receipt: Study title is not unique" in result["logs"]
    assert "ERROR: ENA receipt reported failure for the study submission" in result["logs"]


def test_submit_studies_surfaces_warning_only_receipt_rejection(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

        @staticmethod
        def find_duplicates_by_alias_title(*_args, **_kwargs):
            return {}

        @staticmethod
        def classify_duplicates(records, *_args, **_kwargs):
            return [], records, []

    toolkit.common = FakeCommon()
    study_xml = b'<WEBIN><PROJECT alias="study-a"><TITLE>MIMICC</TITLE></PROJECT></WEBIN>'
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: study_xml,
        validate_manifest=lambda *_args, **_kwargs: (True, []),
        submit_manifest=lambda *_args, **_kwargs: (
            False,
            [],
            ["WARNING: Study title 'MIMICC' is not sufficiently unique"],
        ),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "TITLE": "MIMICC"}],
        test=True,
    )

    assert result["success"] is False
    assert "WARNING: Receipt: Study title 'MIMICC' is not sufficiently unique" in result["logs"]
    assert result["error"] == "WARNING: Study title 'MIMICC' is not sufficiently unique"


def test_submit_studies_reports_local_xsd_validation_failure(monkeypatch):
    toolkit = types.ModuleType("ena_submission_toolkit")

    class FakeCommon:
        @staticmethod
        def validate_hold_until(_hold_until):
            return None

    toolkit.common = FakeCommon()
    toolkit.submit_study = types.SimpleNamespace(
        build_manifest=lambda *_args, **_kwargs: b"<WEBIN></WEBIN>",
        validate_manifest=lambda *_args, **_kwargs: (False, ["ERROR: PROJECT 'study-a' missing TITLE"]),
    )
    monkeypatch.setitem(sys.modules, "ena_submission_toolkit", toolkit)

    @contextmanager
    def fake_webin_client(*_args, **_kwargs):
        yield object()

    monkeypatch.setattr(ena_service, "webin_client", fake_webin_client)
    monkeypatch.setattr(ena_service._bootstrap, "xsd_dir", lambda: "/tmp/fake-xsd")

    result = ena_service.submit_studies(
        ena_service.Credentials(username="Webin-test", password="secret"),
        [{"alias": "study-a", "STUDY_TITLE": "Legacy title that should not be used"}],
        test=True,
    )

    assert result["success"] is False
    assert result["error"] == "Study XML failed local XSD validation; it was not submitted to ENA."
    assert "WARNING:   record 1: missing recommended/required field(s): TITLE" in result["logs"]
    assert "ERROR:   ERROR: PROJECT 'study-a' missing TITLE" not in result["logs"]
    assert "ERROR: PROJECT 'study-a' missing TITLE" in result["logs"]
    assert "ERROR: Study XML failed local XSD validation; it was not submitted to ENA." in result["logs"]


async def test_sample_submit_includes_logs(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "submit_samples",
        lambda *a, **k: {
            "success": False,
            "accessions": [],
            "error": "receipt rejected",
            "logs": ["INFO: XSD validation passed", "INFO: Receipt: invalid sample"],
        },
    )
    r = await client.post("/api/sample/submit", json={"records": [{"alias": "s1", "SAMPLE_TITLE": "t"}], "test": True})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is False
    assert body["logs"] == ["INFO: XSD validation passed", "INFO: Receipt: invalid sample"]


# ---------------------------------------------------------------------------
# Reads (browser-bridged): plan / result
# ---------------------------------------------------------------------------

_RUN = {
    "NAME": "MIMICC_A_1",
    "STUDY": "ERP1",
    "SAMPLE": "ERS1",
    "PLATFORM": "ILLUMINA",
    "INSTRUMENT": "Illumina MiSeq",
    "LIBRARY_SOURCE": "METAGENOMIC",
    "LIBRARY_SELECTION": "PCR",
    "LIBRARY_STRATEGY": "AMPLICON",
    "FASTQ1": "MIMICC_A_1_R1.fastq.gz",
    "FASTQ2": "MIMICC_A_1_R2.fastq.gz",
}


async def test_reads_plan_requires_credentials(client):
    r = await client.post("/api/reads/plan", json={"runs": [_RUN]})
    assert r.status_code == 401


async def test_reads_plan_empty(client, with_creds):
    r = await client.post("/api/reads/plan", json={"runs": []})
    assert r.status_code == 422


async def test_reads_plan_builds_manifest_text(client, with_creds):
    # No session => one-off submission with a timestamped alias, no ledger.
    r = await client.post("/api/reads/plan", json={"runs": [_RUN], "test": True})
    assert r.status_code == 200
    plan = r.json()["plan"]
    assert len(plan) == 1
    entry = plan[0]
    assert entry["action"] == "submit"
    assert entry["name"] == "MIMICC_A_1"
    # Manifest text references read files by basename and carries the metadata.
    assert "STUDY\tERP1" in entry["manifest_text"]
    assert "FASTQ\tMIMICC_A_1_R1.fastq.gz" in entry["manifest_text"]
    assert entry["manifest_filename"].endswith(".manifest")


async def test_reads_plan_invalid_run_marked_skip(client, with_creds):
    bad = {"NAME": "broken"}  # missing required fields
    r = await client.post("/api/reads/plan", json={"runs": [bad], "test": True})
    assert r.status_code == 200
    entry = r.json()["plan"][0]
    assert entry["action"] == "skip"
    assert entry["reason"] == "invalid"


async def test_reads_result_parses_accessions(client, with_creds):
    r = await client.post(
        "/api/reads/result",
        json={"name": "MIMICC_A_1", "alias": "MIMICC_A_1_x", "exit_code": 0, "log": conftest.MOCK_READS_LOG},
    )
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["success"] is True
    assert result["run_accession"] == "ERR9000001"
    assert result["experiment_accession"] == "ERX9000001"
