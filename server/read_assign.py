"""Assign sequencing read files to ENA samples and build webin-cli manifests.

The genuinely new subsystem of this app. Flow:

  1. ``scan_reads``  -> discover FASTQ/BAM/CRAM files in the reads workspace and
     group paired-end mates.
  2. ``suggest``     -> match each read group to a sample by filename.
  3. ``build_manifest`` -> write a webin-cli "reads" manifest for one run.

Manifests are written *into the reads workspace* (a directory mounted
read-write into this container and by host path into the webin-cli sibling
container) so that the manifest and its FASTQ files share one ``-inputDir``.
FASTQ values are stored as basenames, resolved by webin-cli inside ``/data``.

The manifest field set + alias timestamping is ported from
``ena-submission-dataharmonizer/scripts/submit_reads.py:build_manifest`` (which
we do not import, to avoid its mgnify-toolkit/JAR dependency).
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Final

# Recognised read-file extensions (lower-cased).
_READ_SUFFIXES: Final = (".fastq.gz", ".fq.gz", ".fastq", ".fq", ".bam", ".cram")

# Mate tokens for paired-end grouping, tried in order.
_MATE_RE: Final = re.compile(r"(.+?)[._](?:R)?([12])$")

_REQUIRED_FIELDS: Final = (
    "STUDY",
    "SAMPLE",
    "NAME",
    "PLATFORM",
    "INSTRUMENT",
    "LIBRARY_SOURCE",
    "LIBRARY_SELECTION",
    "LIBRARY_STRATEGY",
)
_OPTIONAL_FIELDS: Final = ("INSERT_SIZE", "LIBRARY_NAME", "DESCRIPTION")

# MIMICC library presets (per mimicc/reference.md) for bulk-filling run metadata.
LIBRARY_PRESETS: Final = {
    "illumina_amplicon_ssu": {
        "label": "Illumina SSU rRNA amplicon (MiSeq V3-V4)",
        "PLATFORM": "ILLUMINA",
        "INSTRUMENT": "Illumina MiSeq",
        "LIBRARY_SOURCE": "METAGENOMIC",
        "LIBRARY_SELECTION": "PCR",
        "LIBRARY_STRATEGY": "AMPLICON",
    },
    "ont_amplicon_ssu": {
        "label": "ONT SSU rRNA amplicon (MinION)",
        "PLATFORM": "OXFORD_NANOPORE",
        "INSTRUMENT": "MinION",
        "LIBRARY_SOURCE": "METAGENOMIC",
        "LIBRARY_SELECTION": "PCR",
        "LIBRARY_STRATEGY": "AMPLICON",
    },
    "illumina_wgs": {
        "label": "Illumina shotgun metagenomics (WGS)",
        "PLATFORM": "ILLUMINA",
        "INSTRUMENT": "Illumina NovaSeq 6000",
        "LIBRARY_SOURCE": "METAGENOMIC",
        "LIBRARY_SELECTION": "RANDOM",
        "LIBRARY_STRATEGY": "WGS",
    },
    "illumina_rnaseq": {
        "label": "Illumina metatranscriptomics (RNA-Seq)",
        "PLATFORM": "ILLUMINA",
        "INSTRUMENT": "Illumina NovaSeq 6000",
        "LIBRARY_SOURCE": "METATRANSCRIPTOMIC",
        "LIBRARY_SELECTION": "cDNA",
        "LIBRARY_STRATEGY": "RNA-Seq",
    },
    "pacbio_hifi_wgs": {
        "label": "PacBio HiFi WGS (genome closing)",
        "PLATFORM": "PACBIO_SMRT",
        "INSTRUMENT": "Sequel II",
        "LIBRARY_SOURCE": "GENOMIC",
        "LIBRARY_SELECTION": "RANDOM",
        "LIBRARY_STRATEGY": "WGS",
    },
}


# ---------------------------------------------------------------------------
# Scanning / grouping
# ---------------------------------------------------------------------------


def _read_suffix(name: str) -> str | None:
    lower = name.lower()
    for suffix in _READ_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return None


def _stem_and_mate(name: str, suffix: str) -> tuple[str, str | None]:
    """Return (group_stem, mate) for a read filename; mate is '1', '2' or None."""
    base = name[: -len(suffix)]
    m = _MATE_RE.match(base)
    if m:
        return m.group(1), m.group(2)
    return base, None


def scan_reads(reads_dir: Path) -> list[dict[str, Any]]:
    """Discover read files under ``reads_dir`` and group paired-end mates.

    Returns a list of read groups, each::

        {"group": <stem>, "paired": bool, "files": [<basename>, ...],
         "files_by_mate": {"1": ..., "2": ...} | {}}

    File names are basenames relative to ``reads_dir`` (the webin-cli ``/data``).
    """
    groups: dict[str, dict[str, Any]] = {}
    if not reads_dir.is_dir():
        return []

    for path in sorted(reads_dir.iterdir()):
        if not path.is_file():
            continue
        suffix = _read_suffix(path.name)
        if suffix is None:
            continue
        stem, mate = _stem_and_mate(path.name, suffix)
        group = groups.setdefault(stem, {"group": stem, "files": [], "files_by_mate": {}})
        group["files"].append(path.name)
        if mate:
            group["files_by_mate"][mate] = path.name

    result = []
    for group in groups.values():
        group["files"].sort()
        group["paired"] = set(group["files_by_mate"]) >= {"1", "2"}
        result.append(group)
    result.sort(key=lambda g: g["group"])
    return result


# ---------------------------------------------------------------------------
# Auto-suggest sample <- read group
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def suggest(groups: list[dict[str, Any]], samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Suggest a sample for each read group by matching the alias in the filename.

    ``samples`` items expose ``alias`` and ``accession``. Returns the groups
    annotated with ``suggested_sample`` (accession or "") and ``suggested_alias``
    and a ``confidence`` of "high" (alias token found in stem) or "none".
    """
    indexed = [(s, _normalise(s.get("alias") or "")) for s in samples if (s.get("alias") or "").strip()]
    out = []
    for group in groups:
        stem_norm = _normalise(group["group"])
        match = None
        # Prefer the longest alias that appears in the stem (most specific).
        for sample, alias_norm in sorted(indexed, key=lambda x: len(x[1]), reverse=True):
            if alias_norm and alias_norm in stem_norm:
                match = sample
                break
        annotated = dict(group)
        annotated["suggested_sample"] = (match or {}).get("accession", "")
        annotated["suggested_alias"] = (match or {}).get("alias", "")
        annotated["confidence"] = "high" if match else "none"
        out.append(annotated)
    return out


# ---------------------------------------------------------------------------
# Manifest building
# ---------------------------------------------------------------------------


def validate_record(record: dict[str, Any]) -> list[str]:
    """Return a list of validation problems for a run record (empty == valid)."""
    problems = [f"missing {f}" for f in _REQUIRED_FIELDS if not str(record.get(f, "")).strip()]
    files = (
        record.get("FASTQ") or record.get("FASTQ1") or record.get("FASTQ2") or record.get("BAM") or record.get("CRAM")
    )
    if not files:
        problems.append("no read file(s): need FASTQ, FASTQ1+FASTQ2, BAM, or CRAM")
    return problems


def build_manifest(record: dict[str, Any], workdir: Path, *, alias: str | None = None) -> tuple[str, Path]:
    """Write a tab-delimited webin-cli "reads" manifest for one run into ``workdir``.

    If ``alias`` is given it is used verbatim as the run's NAME/alias (the
    session-aware caller passes a stable, account-unique alias so the run can
    be detected in ENA on a later resume). If ``alias`` is None, a timestamp is
    appended to ``NAME`` so re-submitting the same run yields a distinct alias
    (the original behaviour; avoids ENA duplicate-alias rejections). Returns
    (alias, manifest_path). Raises ValueError if the record is invalid.
    """
    problems = validate_record(record)
    if problems:
        raise ValueError(f"Run {record.get('NAME', '<unknown>')!r}: " + "; ".join(problems))

    if alias is None:
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        alias = f"{record['NAME']}_{timestamp}"

    fields: list[tuple[str, str]] = [
        ("STUDY", record["STUDY"]),
        ("SAMPLE", record["SAMPLE"]),
        ("NAME", alias),
        ("PLATFORM", record["PLATFORM"]),
        ("INSTRUMENT", record["INSTRUMENT"]),
        ("LIBRARY_SOURCE", record["LIBRARY_SOURCE"]),
        ("LIBRARY_SELECTION", record["LIBRARY_SELECTION"]),
        ("LIBRARY_STRATEGY", record["LIBRARY_STRATEGY"]),
    ]
    for key in _OPTIONAL_FIELDS:
        if record.get(key):
            fields.append((key, str(record[key])))
    for key in ("FASTQ1", "FASTQ2", "FASTQ"):
        if record.get(key):
            fields.append(("FASTQ", record[key]))
    for key in ("BAM", "CRAM"):
        if record.get(key):
            fields.append((key, record[key]))

    workdir.mkdir(parents=True, exist_ok=True)
    manifest_path = workdir / f"{alias}.manifest"
    with open(manifest_path, "w") as fh:
        for key, value in fields:
            fh.write(f"{key}\t{value}\n")
    return alias, manifest_path


# ---------------------------------------------------------------------------
# Parse webin-cli output for accessions
# ---------------------------------------------------------------------------


def parse_accessions(log_lines: list[str]) -> dict[str, str]:
    """Best-effort extraction of experiment/run accessions from webin-cli output."""
    text = "\n".join(log_lines)
    result: dict[str, str] = {}
    if exp := re.search(r"\bERX\d+\b", text):
        result["experiment_accession"] = exp.group(0)
    if run := re.search(r"\bERR\d+\b", text):
        result["run_accession"] = run.group(0)
    return result
