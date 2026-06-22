"""In-process API tests (httpx ASGITransport — no Docker, no network)."""

from __future__ import annotations

import conftest
import main as _main

# ---------------------------------------------------------------------------
# Health + credentials
# ---------------------------------------------------------------------------


async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["credentials_configured"] is False
    assert "library_presets" not in body


async def test_set_and_clear_credentials(client, monkeypatch):
    seen = {}

    def validate(creds, *, test):
        seen["username"] = creds.username
        seen["password"] = creds.password
        seen["test"] = test

    monkeypatch.setattr(_main.ena_service, "validate_credentials", validate)
    r = await client.post("/api/credentials", json={"username": "Webin-9", "password": "pw"})
    assert r.status_code == 200
    assert r.json()["environment"] == "test"
    assert seen == {"username": "Webin-9", "password": "pw", "test": True}
    assert (await client.get("/api/health")).json()["credentials_configured"] is True

    r = await client.delete("/api/credentials")
    assert r.status_code == 200
    assert (await client.get("/api/health")).json()["credentials_configured"] is False


async def test_set_credentials_rejects_invalid_webin_login(client, monkeypatch):
    def reject(*a, **k):
        raise PermissionError("bad login")

    monkeypatch.setattr(_main.ena_service, "validate_credentials", reject)
    r = await client.post("/api/credentials", json={"username": "Webin-9", "password": "wrong", "test": False})
    assert r.status_code == 401
    assert "production" in r.json()["detail"]
    assert (await client.get("/api/health")).json()["credentials_configured"] is False


async def test_credentials_require_fields(client):
    r = await client.post("/api/credentials", json={"username": "", "password": "pw"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Auth gating
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
    monkeypatch.setattr(_main.ena_service, "list_records", lambda *a, **k: rows)
    r = await client.get("/api/records/studies?test=true&status=all")
    assert r.status_code == 200
    assert r.json() == rows


async def test_records_list_runs(client, with_creds, monkeypatch):
    rows = [{"accession": "ERR1", "alias": "r1", "experiment_accession": "ERX1", "status": "PRIVATE"}]
    monkeypatch.setattr(_main.ena_service, "list_records", lambda *a, **k: rows)
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
    monkeypatch.setattr(_main.ena_service, "list_records", lambda *a, **k: rows)
    r = await client.get("/api/records/experiments?test=true&status=all")
    assert r.status_code == 200
    assert r.json() == rows


async def test_records_unknown_entity(client, with_creds, monkeypatch):
    def boom(*a, **k):
        raise ValueError("Unknown entity 'frogs'")

    monkeypatch.setattr(_main.ena_service, "list_records", boom)
    r = await client.get("/api/records/frogs")
    assert r.status_code == 400


async def test_records_action(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        _main.ena_service,
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
    monkeypatch.setattr(_main.ena_service, "prepare_samples", lambda export, where=None: container)
    r = await client.post("/api/sample/prepare", json={"export": {"any": "thing"}})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["records"] == [{"alias": "s1"}]


async def test_study_submit(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        _main.ena_service, "submit_studies", lambda *a, **k: {"success": True, "accessions": [{"accession": "ERP9"}]}
    )
    r = await client.post("/api/study/submit", json={"records": [{"alias": "x", "STUDY_TITLE": "t"}], "test": True})
    assert r.status_code == 200
    assert r.json()["accessions"][0]["accession"] == "ERP9"


async def test_sample_submit_includes_logs(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        _main.ena_service,
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
