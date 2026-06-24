"""Submission-session persistence + reads-resume tests (in-process, no Docker).

Session isolation between tests is handled by the ``clean_state`` autouse
fixture in conftest (it wipes per-user rows from the throwaway test DB).
"""

from __future__ import annotations

import conftest
import ena_service
import session_store

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


async def test_reads_plan_uses_stable_alias_and_result_records_ledger(client, with_creds, monkeypatch):
    # No existing runs in ENA.
    monkeypatch.setattr(ena_service, "lookup_existing_runs", lambda *a, **k: {})

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    plan = (await client.post("/api/reads/plan", json={"runs": [_RUN], "session_id": sid})).json()["plan"]
    entry = plan[0]
    assert entry["action"] == "submit"
    # Stable, session-scoped alias (no timestamp suffix).
    assert entry["alias"] == "ResumeRun_MIMICC_A_1"

    # Browser relays the helper's webin-cli outcome back to the server.
    res = (
        await client.post(
            "/api/reads/result",
            json={
                "session_id": sid,
                "name": "MIMICC_A_1",
                "alias": entry["alias"],
                "stable_alias": entry["stable_alias"],
                "exit_code": 0,
                "log": conftest.MOCK_READS_LOG,
            },
        )
    ).json()["result"]
    assert res["success"] is True

    # Ledger row recorded for the next resume.
    runs = session_store.list_reads_runs(sid)
    assert runs[0]["status"] == "done"
    assert runs[0]["run_accession"] == "ERR9000001"


async def test_reads_plan_skips_already_in_ena(client, with_creds, monkeypatch):
    monkeypatch.setattr(
        ena_service,
        "lookup_existing_runs",
        lambda *a, **k: {"ResumeRun_MIMICC_A_1": {"experiment_accession": "ERX5", "run_accession": "ERR5"}},
    )

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    plan = (await client.post("/api/reads/plan", json={"runs": [_RUN], "session_id": sid})).json()["plan"]

    entry = plan[0]
    assert entry["action"] == "skip"
    assert entry["reason"] == "already_in_ena"
    assert entry["run_accession"] == "ERR5"
    # Recorded in the ledger so a later resume short-circuits.
    assert session_store.get_reads_run(sid, "MIMICC_A_1")["status"] == "already_in_ena"


async def test_reads_plan_resumes_from_ledger(client, with_creds, monkeypatch):
    """A run already DONE in this session's ledger is skipped without an ENA lookup."""

    def _boom(*a, **k):  # must NOT be called when the ledger already has the run
        raise AssertionError("ENA lookup should not run for a ledger-cached run")

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    session_store.upsert_reads_run(
        sid, "MIMICC_A_1", "ResumeRun_MIMICC_A_1", session_store.STATUS_DONE, run_accession="ERR9"
    )
    monkeypatch.setattr(ena_service, "lookup_existing_runs", _boom)

    plan = (await client.post("/api/reads/plan", json={"runs": [_RUN], "session_id": sid})).json()["plan"]
    assert plan[0]["action"] == "skip"
    assert plan[0]["reason"] == "cached"


async def test_reads_plan_force_reupload_uses_fresh_alias(client, with_creds, monkeypatch):
    # Even though it's "already in ENA", force_reupload must bypass the skip.
    monkeypatch.setattr(
        ena_service,
        "lookup_existing_runs",
        lambda *a, **k: {"ResumeRun_MIMICC_A_1": {"experiment_accession": "ERX5", "run_accession": "ERR5"}},
    )

    sid = (await client.post("/api/sessions", json={"name": "ResumeRun"})).json()["id"]
    plan = (
        await client.post(
            "/api/reads/plan",
            json={"runs": [_RUN], "session_id": sid, "force_reupload": True},
        )
    ).json()["plan"]

    entry = plan[0]
    assert entry["action"] == "submit"
    # Fresh alias = stable alias + timestamp suffix (so ENA won't reject it).
    assert entry["alias"].startswith("ResumeRun_MIMICC_A_1_")
    assert entry["alias"] != "ResumeRun_MIMICC_A_1"
