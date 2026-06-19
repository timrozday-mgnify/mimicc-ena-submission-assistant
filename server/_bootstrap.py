"""Locate and put the vendored sibling code on sys.path.

This app reuses code from two sibling repositories:

  * ``ena-api-client``                 -> the ``ena_api`` package
  * ``ena-submission-dataharmonizer``  -> ``ena_common``, ``submit_sample``,
    ``submit_study`` and ``prepare_dh_output``
  * ``linkml-lib``                     -> the ``linkml_lib`` package.

``scripts/vendor.sh`` copies these into ``./vendor`` for the Docker image. For
local development we fall back to the sibling checkouts next to this repo.

Importing this module extends ``sys.path`` best-effort — it never raises, so the
server (and mock-based tests) can import even when the vendored code or its
heavy dependencies are absent. The resolved schema/XSD paths are exposed via
``schema_path()`` / ``xsd_dir()`` which raise only when actually needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_VENDOR = Path(os.environ.get("ENA_DH_VENDOR", _REPO_ROOT / "vendor"))
_SIBLINGS = _REPO_ROOT.parent


def _first_existing(*candidates: Path | None) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val) if val else None


SCRIPTS_DIR = _first_existing(
    _env_path("ENA_DH_SCRIPTS"),
    _VENDOR / "scripts",
    _SIBLINGS / "ena-submission-dataharmonizer" / "scripts",
)

LINKML_LIB_ROOT = _first_existing(
    _env_path("LINKML_LIB_ROOT"),
    _VENDOR if (_VENDOR / "linkml_lib").exists() else None,
    _SIBLINGS / "linkml-lib" / "src" if (_SIBLINGS / "linkml-lib" / "src" / "linkml_lib").exists() else None,
)

_ENA_API_ROOT = _first_existing(
    _env_path("ENA_API_ROOT"),
    _VENDOR if (_VENDOR / "ena_api").exists() else None,
    _SIBLINGS / "ena-api-client" if (_SIBLINGS / "ena-api-client" / "ena_api").exists() else None,
)

for _p in (_ENA_API_ROOT, SCRIPTS_DIR, LINKML_LIB_ROOT):
    if _p is not None and str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def schema_path() -> Path:
    """The schema behind the Samples tab's DataHarmonizer grid and the Prepare step's
    field-name mapping (``ena_service.prepare_samples``) — ``mimicc_sample.yaml``, not
    the combined ``mimicc_sample_experiment.yaml`` it was filtered from."""
    found = _first_existing(
        _env_path("ENA_DH_SCHEMA"),
        _VENDOR / "schemas" / "mimicc_sample.yaml",
        _SIBLINGS / "ena-submission-dataharmonizer" / "schemas" / "mimicc_sample.yaml",
    )
    if found is None:
        raise RuntimeError("Could not locate mimicc_sample.yaml. Run scripts/vendor.sh or set ENA_DH_SCHEMA.")
    return found


def xsd_dir() -> Path:
    found = _first_existing(
        _env_path("ENA_DH_XSD"),
        _VENDOR / "assets" / "ena_schema",
        _SIBLINGS / "ena-submission-dataharmonizer" / "assets" / "ena_schema",
    )
    if found is None:
        raise RuntimeError("Could not locate the ENA XSD directory. Run scripts/vendor.sh or set ENA_DH_XSD.")
    return found
