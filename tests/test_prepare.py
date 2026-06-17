"""Integration test for the DataHarmonizer -> submission prepare pipeline.

Exercises the real ``linkml_lib`` filter + rename against the actual MIMICC
schema. Skipped automatically when the vendored ena-dh scripts or the heavy
``linkml`` dependency are unavailable.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("linkml")

import ena_service  # noqa: E402

try:
    import _bootstrap  # noqa: E402

    _bootstrap.schema_path()
    import prepare_dh_output  # noqa: F401,E402
    import submit_sample  # noqa: F401,E402
except Exception:  # pragma: no cover - environment without vendored scripts/schema
    pytest.skip("vendored ena-dh scripts/schema not available", allow_module_level=True)


def test_prepare_filters_and_renames():
    export = {
        "Container": {
            "MIMICC_SampleExperiments": [
                {
                    "Sample alias (ENA sample alias)": "MIMICC_A_1",
                    "Sample title": "MIMICC bioreactor A t1",
                    "Collection date": "2026-05-10",
                    "Sample storage temperature": "-80",
                    "LIBRARY_STRATEGY": "AMPLICON",  # experiment-only -> filtered out
                }
            ]
        }
    }
    out = ena_service.prepare_samples(export, where=ena_service.DEFAULT_SAMPLE_FILTER)
    records = ena_service.records_from_container(out)
    assert len(records) == 1
    r = records[0]
    # Title keys renamed to ENA field-name ids
    assert r["alias"] == "MIMICC_A_1"
    assert r["SAMPLE_TITLE"] == "MIMICC bioreactor A t1"
    assert "collection date" in r
    # Experiment-only column dropped by the sample filter
    assert "LIBRARY_STRATEGY" not in r


def _sample_attribute(root: ET.Element, tag: str) -> ET.Element:
    for attr in root.findall(".//SAMPLE_ATTRIBUTE"):
        if attr.findtext("TAG") == tag:
            return attr
    raise AssertionError(f"Missing SAMPLE_ATTRIBUTE {tag!r}")


def test_prepared_records_emit_schema_units_in_sample_xml():
    export = {
        "Container": {
            "MIMICC_SampleExperiments": [
                {
                    "Sample alias (ENA sample alias)": "MIMICC_25Nov5669_A_2",
                    "Sample title": "MIMICC_25Nov5669_A_2",
                    "Sample storage temperature": "-80",
                    "Geographic location (latitude)": "54.3324",
                    "Geographic location (longitude)": "10.1212",
                    "Amount or size of sample collected": "1 mL",
                    "Temperature": "37",
                    "Sample volume or weight for DNA extraction": "200",
                    "Library size": "not applicable",
                    "Taxon ID": "1235509",
                    "Scientific name": "synthetic metagenome",
                }
            ]
        }
    }
    prepared = ena_service.prepare_samples(export, where=ena_service.DEFAULT_SAMPLE_FILTER)
    records = ena_service.records_from_container(prepared)
    records, slot_to_unit = ena_service._normalise_sample_records_for_submission(  # noqa: SLF001
        records,
        ena_service._sample_unit_rules(),  # noqa: SLF001
    )

    root = ET.fromstring(
        submit_sample.build_manifest(
            records,
            checklist_id="ERC000025",
            slot_to_unit=slot_to_unit,
        )
    )
    assert _sample_attribute(root, "sample storage temperature").findtext("UNITS") == "°C"
    assert _sample_attribute(root, "geographic location (latitude)").findtext("UNITS") == "DD"
    assert _sample_attribute(root, "geographic location (longitude)").findtext("UNITS") == "DD"
    amount = _sample_attribute(root, "amount or size of sample collected")
    assert amount.findtext("VALUE") == "0.001"
    assert amount.findtext("UNITS") == "L"
    assert _sample_attribute(root, "temperature").findtext("UNITS") == "ºC"
    extraction = _sample_attribute(root, "sample volume or weight for DNA extraction")
    assert extraction.findtext("VALUE") == "200"
    assert extraction.findtext("UNITS") == "mg"
    assert "library size" not in [attr.findtext("TAG") for attr in root.findall(".//SAMPLE_ATTRIBUTE")]
