"""Unit tests for the read-to-sample assignment subsystem."""

from __future__ import annotations

import pytest
import read_assign


def _touch(d, *names):
    for n in names:
        (d / n).write_text("x")


def test_scan_groups_paired_end(tmp_path):
    _touch(tmp_path, "MIMICC_A_1_R1.fastq.gz", "MIMICC_A_1_R2.fastq.gz", "notes.txt")
    groups = read_assign.scan_reads(tmp_path)
    assert len(groups) == 1
    g = groups[0]
    assert g["paired"] is True
    assert g["files_by_mate"] == {"1": "MIMICC_A_1_R1.fastq.gz", "2": "MIMICC_A_1_R2.fastq.gz"}


def test_scan_single_end(tmp_path):
    _touch(tmp_path, "sampleX.fastq.gz")
    groups = read_assign.scan_reads(tmp_path)
    assert len(groups) == 1
    assert groups[0]["paired"] is False


def test_scan_underscore_mate_tokens(tmp_path):
    _touch(tmp_path, "run5_1.fq.gz", "run5_2.fq.gz")
    groups = read_assign.scan_reads(tmp_path)
    assert groups[0]["group"] == "run5"
    assert groups[0]["paired"] is True


def test_scan_missing_dir(tmp_path):
    assert read_assign.scan_reads(tmp_path / "nope") == []


def test_suggest_matches_alias_in_filename():
    groups = [{"group": "MIMICC_A_1_R", "files": ["MIMICC_A_1_R1.fastq.gz"]}]
    samples = [
        {"alias": "MIMICC_A_1", "accession": "ERS111"},
        {"alias": "MIMICC_B_2", "accession": "ERS222"},
    ]
    out = read_assign.suggest(groups, samples)
    assert out[0]["suggested_sample"] == "ERS111"
    assert out[0]["confidence"] == "high"


def test_suggest_no_match():
    groups = [{"group": "unrelated", "files": ["unrelated.fastq.gz"]}]
    out = read_assign.suggest(groups, [{"alias": "MIMICC_A_1", "accession": "ERS111"}])
    assert out[0]["suggested_sample"] == ""
    assert out[0]["confidence"] == "none"


def test_suggest_prefers_longest_alias():
    groups = [{"group": "MIMICC_A_10", "files": []}]
    samples = [
        {"alias": "MIMICC_A_1", "accession": "ERS1"},
        {"alias": "MIMICC_A_10", "accession": "ERS10"},
    ]
    out = read_assign.suggest(groups, samples)
    assert out[0]["suggested_sample"] == "ERS10"


def test_build_manifest_paired(tmp_path):
    record = {
        "NAME": "run1",
        "STUDY": "ERP1",
        "SAMPLE": "ERS1",
        "PLATFORM": "ILLUMINA",
        "INSTRUMENT": "Illumina MiSeq",
        "LIBRARY_SOURCE": "METAGENOMIC",
        "LIBRARY_SELECTION": "PCR",
        "LIBRARY_STRATEGY": "AMPLICON",
        "FASTQ1": "run1_R1.fastq.gz",
        "FASTQ2": "run1_R2.fastq.gz",
    }
    alias, path = read_assign.build_manifest(record, tmp_path)
    assert alias.startswith("run1_")
    text = path.read_text()
    assert "STUDY\tERP1" in text
    assert "SAMPLE\tERS1" in text
    assert text.count("FASTQ\t") == 2
    assert f"NAME\t{alias}" in text


def test_build_manifest_validates_required(tmp_path):
    with pytest.raises(ValueError):
        read_assign.build_manifest({"NAME": "x"}, tmp_path)


def test_validate_record_reports_missing():
    problems = read_assign.validate_record({"NAME": "x", "STUDY": "ERP1"})
    assert any("SAMPLE" in p for p in problems)
    assert any("read file" in p for p in problems)


def test_parse_accessions():
    lines = ["INFO: created", "experiment ERX123 and run ERR456 done"]
    assert read_assign.parse_accessions(lines) == {
        "experiment_accession": "ERX123",
        "run_accession": "ERR456",
    }
