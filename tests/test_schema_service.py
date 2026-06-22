"""Unit tests for server/schema_service.py.

Exercises the schema library (list/save/delete), the ENA XML/XSD import
pipeline, and the in-process compile that backs schema selection for the
sample/experiment DataHarmonizer grids. Skipped automatically when the heavy
``linkml`` dependency is unavailable.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("linkml")

import schema_service  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_schemas_dir(tmp_path, monkeypatch):
    """Point the schema library at a throwaway directory for every test."""
    monkeypatch.setattr("_bootstrap.schemas_dir", lambda: tmp_path)
    yield tmp_path


def test_list_schemas_seeds_from_bundled_defaults():
    schemas = schema_service.list_schemas()
    ids = {s["id"] for s in schemas}
    assert "mimicc_sample" in ids
    assert "mimicc_experiment" in ids


def test_save_read_delete_round_trip():
    yaml_text = "name: my_schema\nid: https://example.org/my_schema\nclasses: {}\n"
    schema_id = schema_service.save_schema("My Schema!", yaml_text)
    assert schema_id == "my-schema"  # slugified
    assert schema_service.read_schema(schema_id) == yaml_text

    schema_service.delete_schema(schema_id)
    with pytest.raises(ValueError):
        schema_service.read_schema(schema_id)


def test_save_rejects_invalid_yaml():
    with pytest.raises(ValueError):
        schema_service.save_schema("bad", "not: [a, mapping, root: oops")


def test_list_ena_sources_includes_top_level_and_checklists_subdir():
    sources = schema_service.list_ena_sources()
    checklist_ids = {c["id"] for c in sources["checklists"]}
    xsd_ids = {x["id"] for x in sources["xsd"]}
    assert "ERC000025.xml" in checklist_ids  # top-level vendored checklist
    assert "SRA.sample.xsd" in xsd_ids


def test_import_build_merges_checklist_and_xsd_sources():
    yaml_text = schema_service.import_build(source_ids=["ERC000025.xml", "SRA.sample.xsd"])
    assert "ERC000025" in yaml_text or "GSC MIxS" in yaml_text
    # Both inputs contributed slots to the merged schema.
    import yaml as _yaml

    schema = _yaml.safe_load(yaml_text)
    assert schema.get("slots")


def test_import_build_raises_without_any_inputs():
    with pytest.raises(ValueError):
        schema_service.import_build()


def test_import_build_can_include_an_existing_saved_schema():
    schema_service.save_schema("seed", "name: seed\nid: https://example.org/seed\nclasses: {}\nslots: {}\n")
    yaml_text = schema_service.import_build(source_ids=["ERC000025.xml"], schema_ids=["seed"])
    assert yaml_text


def test_select_for_grid_writes_schema_json_and_registry(tmp_path):
    dh_dir = tmp_path / "dh"
    yaml_text = schema_service.read_schema("mimicc_sample")

    template = schema_service.select_for_grid("sample", yaml_text, dh_dir=dh_dir)

    assert template.startswith("mimicc/")
    schema_json_path = dh_dir / "templates" / "mimicc" / "schema.json"
    assert schema_json_path.exists()
    compiled = json.loads(schema_json_path.read_text())
    assert compiled["name"]
    assert (dh_dir / "templates" / "mimicc" / "export.js").exists()

    registry = json.loads((dh_dir / "dh-template-registry.json").read_text())
    assert registry["mimicc"] == compiled["name"]


def test_select_for_grid_preserves_existing_export_js(tmp_path):
    dh_dir = tmp_path / "dh"
    tpl_dir = dh_dir / "templates" / "mimicc"
    tpl_dir.mkdir(parents=True)
    (tpl_dir / "export.js").write_text("export default { custom: true };\n")

    schema_service.select_for_grid("sample", schema_service.read_schema("mimicc_sample"), dh_dir=dh_dir)

    assert "custom" in (tpl_dir / "export.js").read_text()


def test_select_for_grid_rejects_unknown_role(tmp_path):
    with pytest.raises(ValueError):
        schema_service.select_for_grid("bogus", "name: x\n", dh_dir=tmp_path / "dh")


def test_select_for_grid_for_experiment_role_uses_its_own_folder(tmp_path):
    dh_dir = tmp_path / "dh"
    template = schema_service.select_for_grid(
        "experiment", schema_service.read_schema("mimicc_experiment"), dh_dir=dh_dir
    )
    assert template.startswith("mimicc_experiment/")
    assert (dh_dir / "templates" / "mimicc_experiment" / "schema.json").exists()
