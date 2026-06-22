"""In-process API tests for the /api/schemas endpoints (httpx ASGITransport).

Skipped automatically when the heavy ``linkml`` dependency is unavailable.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("linkml")

import main as _main  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_schemas_dir(tmp_path, monkeypatch):
    """Point the schema library at a throwaway directory for every test."""
    monkeypatch.setattr("_bootstrap.schemas_dir", lambda: tmp_path)
    yield tmp_path


async def test_list_schemas_seeds_and_returns_bundled_defaults(client):
    r = await client.get("/api/schemas")
    assert r.status_code == 200
    ids = {s["id"] for s in r.json()}
    assert "mimicc_sample" in ids


async def test_ena_sources_lists_checklists_and_xsd(client):
    r = await client.get("/api/schemas/ena-sources")
    assert r.status_code == 200
    body = r.json()
    assert any(c["id"] == "ERC000025.xml" for c in body["checklists"])
    assert any(x["id"] == "SRA.sample.xsd" for x in body["xsd"])


async def test_save_get_export_delete_round_trip(client):
    yaml_text = "name: api_test_schema\nid: https://example.org/api_test_schema\nclasses: {}\n"
    r = await client.post("/api/schemas", json={"name": "API Test Schema", "yaml": yaml_text})
    assert r.status_code == 200
    schema_id = r.json()["id"]
    assert schema_id == "api-test-schema"

    r = await client.get(f"/api/schemas/{schema_id}")
    assert r.status_code == 200
    assert r.json()["yaml"] == yaml_text

    r = await client.get(f"/api/schemas/{schema_id}/export")
    assert r.status_code == 200
    assert r.headers["content-disposition"] == f'attachment; filename="{schema_id}.yaml"'
    assert r.text == yaml_text

    r = await client.delete(f"/api/schemas/{schema_id}")
    assert r.status_code == 200
    r = await client.get(f"/api/schemas/{schema_id}")
    assert r.status_code == 404


async def test_get_unknown_schema_404s(client):
    r = await client.get("/api/schemas/does-not-exist")
    assert r.status_code == 404


async def test_save_rejects_invalid_yaml(client):
    r = await client.post("/api/schemas", json={"name": "bad", "yaml": "not: [valid: yaml: root"})
    assert r.status_code == 400


async def test_import_merges_bundled_sources(client):
    r = await client.post(
        "/api/schemas/import",
        data={"source_ids": ["ERC000025.xml", "SRA.sample.xsd"]},
    )
    assert r.status_code == 200
    assert r.json()["yaml"]


async def test_import_without_sources_400s(client):
    r = await client.post("/api/schemas/import", data={})
    assert r.status_code == 400


async def test_import_file_accepts_an_uploaded_yaml(client):
    yaml_bytes = b"name: uploaded\nid: https://example.org/uploaded\nclasses: {}\n"
    r = await client.post(
        "/api/schemas/import-file",
        files={"file": ("uploaded.yaml", yaml_bytes, "application/x-yaml")},
    )
    assert r.status_code == 200
    assert "uploaded" in r.json()["yaml"]


async def test_select_compiles_schema_into_the_grid_folder(client, tmp_path, monkeypatch):
    dh_dir = tmp_path / "dh"
    monkeypatch.setattr(_main, "_DH_DIR", dh_dir)

    r = await client.post("/api/schemas/select", json={"role": "sample", "schema_id": "mimicc_sample"})
    assert r.status_code == 200
    template = r.json()["template"]
    assert template.startswith("mimicc/")

    schema_json = json.loads((dh_dir / "templates" / "mimicc" / "schema.json").read_text())
    assert schema_json["name"]
    registry = json.loads((dh_dir / "dh-template-registry.json").read_text())
    assert registry["mimicc"] == schema_json["name"]


async def test_select_with_inline_yaml(client, tmp_path, monkeypatch):
    dh_dir = tmp_path / "dh"
    monkeypatch.setattr(_main, "_DH_DIR", dh_dir)
    yaml_text = "name: inline_schema\nid: https://example.org/inline_schema\nclasses: {}\n"

    r = await client.post("/api/schemas/select", json={"role": "experiment", "yaml": yaml_text})
    assert r.status_code == 200
    assert r.json()["template"] == "mimicc_experiment/inline_schema"


async def test_select_requires_schema_id_or_yaml(client):
    r = await client.post("/api/schemas/select", json={"role": "sample"})
    assert r.status_code == 422


async def test_select_rejects_unknown_role(client):
    r = await client.post("/api/schemas/select", json={"role": "bogus", "yaml": "name: x\nclasses: {}\n"})
    assert r.status_code == 400
