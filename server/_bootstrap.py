"""Locate the schema/XSD assets this app needs at runtime.

``ena_api``, ``linkml_lib``, and ``ena_submission_toolkit``
(``common``, ``submit_sample``, ``submit_study``, ``prepare_dh_output``) are
pinned pip dependencies (see ``pyproject.toml``) — plain ``import``s work
without any ``sys.path`` setup.

What's left is locating the non-Python assets that ship alongside them: the
MIMICC LinkML schemas (``schemas/``) and the ENA/SRA XSDs/checklists
(``assets/ena_schema/``) — both committed directly in this repo. Override
with ``ENA_DH_SCHEMA`` / ``ENA_DH_XSD`` / ``ENA_DH_SCHEMAS_DIR`` if needed.

The resolved paths are exposed via ``schema_path()`` / ``xsd_dir()`` /
``vendor_schemas_dir()``, which raise only when actually needed.
"""

from __future__ import annotations

import os
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _first_existing(*candidates: Path | None) -> Path | None:
    for c in candidates:
        if c and c.exists():
            return c
    return None


def _env_path(name: str) -> Path | None:
    val = os.environ.get(name)
    return Path(val) if val else None


def schema_path() -> Path:
    """The schema behind the Samples tab's DataHarmonizer grid and the Prepare step's
    field-name mapping (``ena_service.prepare_samples``) — ``mimicc_sample.yaml``, not
    the combined ``mimicc_sample_experiment.yaml`` it was filtered from."""
    found = _first_existing(
        _env_path("ENA_DH_SCHEMA"),
        _REPO_ROOT / "schemas" / "mimicc_sample.yaml",
    )
    if found is None:
        raise RuntimeError("Could not locate mimicc_sample.yaml. Set ENA_DH_SCHEMA to override.")
    return found


def xsd_dir() -> Path:
    found = _first_existing(
        _env_path("ENA_DH_XSD"),
        _REPO_ROOT / "assets" / "ena_schema",
    )
    if found is None:
        raise RuntimeError("Could not locate the ENA XSD directory. Set ENA_DH_XSD to override.")
    return found


def vendor_schemas_dir() -> Path:
    """The bundled LinkML schemas shipped with the app (read-only) — used to
    seed the writable schema library on first run."""
    found = _first_existing(
        _env_path("ENA_DH_SCHEMAS_DIR"),
        _REPO_ROOT / "schemas",
    )
    if found is None:
        raise RuntimeError("Could not locate the bundled schemas directory.")
    return found


def schemas_dir() -> Path:
    """Writable directory holding the user's saved/imported LinkML schemas
    (the schema "library"). Persisted via a Docker volume in production
    (``/schemas``, see docker-compose.yml); falls back to a repo-local
    directory for non-Docker development."""
    configured = _env_path("SCHEMAS_CONTAINER_DIR")
    if configured is not None:
        configured.mkdir(parents=True, exist_ok=True)
        return configured
    default = _REPO_ROOT / ".local" / "schemas"
    default.mkdir(parents=True, exist_ok=True)
    return default
