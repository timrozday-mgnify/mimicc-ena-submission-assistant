"""Submission-session persistence + reads-resume tests (in-process, no Docker)."""

from __future__ import annotations

import json

import conftest
import main as _main
import pytest
import session_store


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    """Point the session store at a throwaway dir + DB for every test."""
    monkeypatch.setattr(session_store, "_SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(session_store, "_DB_PATH", tmp_path / "sessions.db")


# ---------------------------------------------------------------------------
# Session registry + state round-trip
# ---------------------------------------------------------------------------


async def test_session_create_list_get(client):
    r = await client.post("/api/sessions", json={"name": "Run A"})
    assert r.status_code == 200
    sid = r.json()["id"]

    listed = (await client.get("/api/sessions")).json()
    assert [s["name"] for s in listed] == ["Run A"]

    got = (await client.get(f"/api/sessions/{sid}")).json()
    assert got["session"]["name"] == "Run A"
    assert got["state"] is None
    assert got["reads_runs"] == []


async def test_session_duplicate_name_rejected(client):
    await client.post("/api/sessions", json={"name": "dup"})
    r = await client.post("/api/sessions", json={"name": "dup"})
    assert r.status_code == 400


async def test_session_state_round_trip(client):
    sid = (await client.post("/api/sessions", json={"name": "Run B"})).json()["id"]

    state = {"fields": {"studyJson": "[1,2,3]"}, "runRows": [{"NAME": "x"}], "logs": {"readsLog": "hi"}}
    r = await client.put(f"/api/sessions/{sid}/state", json={"state": state, "test_env": False})
    assert r.status_code == 200
    assert r.json()["saved_at"]

    got = (await client.get(f"/api/sessions/{sid}")).json()
    assert got["state"] == state
    assert got["session"]["test_env"] is False


async def test_session_dh_export_round_trip(client):
    sid = (await client.post("/api/sessions", json={"name": "Run C"})).json()["id"]
    export = {"Container": {"MIMICC_SampleExperiments": [{"alias": "s1"}]}}

    r = await client.post(f"/api/sessions/{sid}/dh-export/sample", json={"export": export})
    assert r.status_code == 200 and r.json()["saved_at"]

    got = (await client.get(f"/api/sessions/{sid}/dh-export/sample")).json()
    assert got["export"] == export

    # Also surfaced by the aggregate session GET (used to rehydrate the grid).
    agg = (await client.get(f"/api/sessions/{sid}")).json()
    assert agg["dh_export"] == export


async def test_session_dh_export_kinds_are_independent(client):
    sid = (await client.post("/api/sessions", json={"name": "Run C2"})).json()["id"]
    sample_export = {"Container": {"MIMICC_SampleExperiments": [{"alias": "s1"}]}}
    exp_export = {"Container": {"MIMICC_Experiment": [{"Experiment name": "run1"}]}}

    await client.post(f"/api/sessions/{sid}/dh-export/sample", json={"export": sample_export})
    await client.post(f"/api/sessions/{sid}/dh-export/experiment", json={"export": exp_export})

    sample_got = (await client.get(f"/api/sessions/{sid}/dh-export/sample")).json()
    exp_got = (await client.get(f"/api/sessions/{sid}/dh-export/experiment")).json()
    assert sample_got["export"] == sample_export
    assert exp_got["export"] == exp_export

    agg = (await client.get(f"/api/sessions/{sid}")).json()
    assert agg["dh_export"] == sample_export
    assert agg["exp_dh_export"] == exp_export


async def test_session_dh_export_invalid_kind(client):
    sid = (await client.post("/api/sessions", json={"name": "Run C3"})).json()["id"]
    r = await client.post(f"/api/sessions/{sid}/dh-export/bogus", json={"export": {}})
    assert r.status_code == 400


async def test_session_delete(client):
    sid = (await client.post("/api/sessions", json={"name": "Run D"})).json()["id"]
    assert (await client.delete(f"/api/sessions/{sid}")).status_code == 200
    assert (await client.get(f"/api/sessions/{sid}")).status_code == 404


async def test_state_save_on_missing_session_404(client):
    r = await client.put("/api/sessions/nope/state", json={"state": {}})
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Reads resumability
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


async def _stream(client, job_id):
    events = []
    async with client.stream("GET", f"/api/reads/stream/{job_id}") as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line[5:].strip()))
    return events


async def test_reads_records_ledger_and_uses_stable_alias(client, with_creds, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_READS_CONTAINER_DIR", tmp_path)
    monkeypatch.setattr(_main.webin_runner, "iter_webin_cli_logs", lambda **kw: conftest.mock_logs_success(**kw))
    # No existing runs in ENA.
    monkeypatch.setattr(_main.ena_service, "lookup_existing_runs", lambda *a, **k: {})

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    job = (await client.post("/api/reads/submit", json={"runs": [_RUN], "session_id": sid})).json()
    events = await _stream(client, job["job_id"])

    done = [e for e in events if e.get("done")][0]
    assert done["results"][0]["success"] is True
    assert done["results"][0]["skipped"] is False
    # Stable, session-scoped alias was used (no timestamp suffix).
    assert done["results"][0]["alias"] == "ResumeRun_MIMICC_A_1"

    # Ledger row recorded for the next resume.
    runs = session_store.list_reads_runs(sid)
    assert runs[0]["status"] == "done"
    assert runs[0]["run_accession"] == "ERR9000001"


async def test_reads_skips_already_in_ena(client, with_creds, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_READS_CONTAINER_DIR", tmp_path)

    def _boom(**kw):  # must NOT be called for a skipped run
        raise AssertionError("webin-cli should not run for an already-submitted run")

    monkeypatch.setattr(_main.webin_runner, "iter_webin_cli_logs", _boom)
    monkeypatch.setattr(
        _main.ena_service,
        "lookup_existing_runs",
        lambda *a, **k: {"ResumeRun_MIMICC_A_1": {"experiment_accession": "ERX5", "run_accession": "ERR5"}},
    )

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    job = (await client.post("/api/reads/submit", json={"runs": [_RUN], "session_id": sid})).json()
    events = await _stream(client, job["job_id"])

    res = [e for e in events if e.get("done")][0]["results"][0]
    assert res["skipped"] is True
    assert res["reason"] == "already_in_ena"
    assert res["run_accession"] == "ERR5"
    assert session_store.get_reads_run(sid, "MIMICC_A_1")["status"] == "already_in_ena"


async def test_reads_force_reupload_runs_with_fresh_alias(client, with_creds, tmp_path, monkeypatch):
    monkeypatch.setattr(_main, "_READS_CONTAINER_DIR", tmp_path)
    monkeypatch.setattr(_main.webin_runner, "iter_webin_cli_logs", lambda **kw: conftest.mock_logs_success(**kw))
    # Even though it's "already in ENA", force_reupload must bypass the skip.
    monkeypatch.setattr(
        _main.ena_service,
        "lookup_existing_runs",
        lambda *a, **k: {"ResumeRun_MIMICC_A_1": {"experiment_accession": "ERX5", "run_accession": "ERR5"}},
    )

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    job = (
        await client.post(
            "/api/reads/submit",
            json={"runs": [_RUN], "session_id": sid, "force_reupload": True},
        )
    ).json()
    events = await _stream(client, job["job_id"])

    res = [e for e in events if e.get("done")][0]["results"][0]
    assert res["skipped"] is False
    # Fresh alias = stable alias + timestamp suffix (so ENA won't reject it).
    assert res["alias"].startswith("ResumeRun_MIMICC_A_1_")
    assert res["alias"] != "ResumeRun_MIMICC_A_1"
