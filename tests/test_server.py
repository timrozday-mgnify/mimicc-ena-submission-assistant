"""In-process API tests (httpx ASGITransport — no Docker, no network)."""

from __future__ import annotations

import json

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
    assert "illumina_amplicon_ssu" in body["library_presets"]


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


async def test_dh_export_round_trip(client, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_DH_DRAFT_CONTAINER_DIR", tmp_path)
    monkeypatch.setattr(_main, "_DH_DRAFT_FILE", tmp_path / "export.json")

    export = {"Container": {"MIMICC_SampleExperiments": [{"alias": "s1"}]}}
    r = await client.post("/api/sample/dh-export", json={"export": export})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["saved_at"]

    r = await client.get("/api/sample/dh-export")
    assert r.status_code == 200
    body = r.json()
    assert body["export"] == export
    assert body["saved_at"]


async def test_dh_export_none_saved_yet(client, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_DH_DRAFT_FILE", tmp_path / "export.json")
    r = await client.get("/api/sample/dh-export")
    assert r.status_code == 200
    assert r.json() == {"export": None, "saved_at": None}


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
# Reads: scan / submit / stream
# ---------------------------------------------------------------------------


async def test_reads_scan(client, tmp_path, monkeypatch):
    (tmp_path / "MIMICC_A_1_R1.fastq.gz").write_text("x")
    (tmp_path / "MIMICC_A_1_R2.fastq.gz").write_text("x")
    monkeypatch.setattr(_main, "_READS_CONTAINER_DIR", tmp_path)
    r = await client.post("/api/reads/scan", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    assert body["groups"][0]["paired"] is True


async def test_reads_submit_empty(client, with_creds):
    r = await client.post("/api/reads/submit", json={"runs": []})
    assert r.status_code == 422


async def test_reads_submit_requires_credentials(client):
    r = await client.post("/api/reads/submit", json={"runs": [{"NAME": "x"}]})
    assert r.status_code == 401


async def test_reads_stream(client, with_creds, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_READS_CONTAINER_DIR", tmp_path)
    monkeypatch.setattr(_main.webin_runner, "iter_webin_cli_logs", lambda **kw: conftest.mock_logs_success(**kw))

    run = {
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
    job = (await client.post("/api/reads/submit", json={"runs": [run], "test": True, "submit": True})).json()
    job_id = job["job_id"]

    events = []
    async with client.stream("GET", f"/api/reads/stream/{job_id}") as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))

    done = [e for e in events if e.get("done")]
    assert done, f"no done event in {events}"
    results = done[0]["results"]
    assert results[0]["success"] is True
    assert results[0]["run_accession"] == "ERR9000001"


async def test_reads_stream_unknown_job(client):
    r = await client.get("/api/reads/stream/nope")
    assert r.status_code == 404
