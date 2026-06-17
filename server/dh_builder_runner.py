"""Server adapter for dh_builder_lib.

Adds Docker-in-Docker path validation via the /hostroot mount and creates the
schema/output directories on the host filesystem before invoking the core
library. Mirrors webin_runner.py.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from dh_builder_lib import iter_dh_builder_logs as _iter_logs

_HOSTROOT = Path(os.environ.get("HOSTROOT", "/hostroot"))


def iter_dh_builder_logs(
    *,
    schema_host_dir: str,
    output_host_dir: str,
) -> Generator[str, None, int]:
    """Thin server wrapper around dh_builder_lib.iter_dh_builder_logs.

    Creates the output directory on the host filesystem (via /hostroot)
    before delegating to the library.
    """
    os.makedirs(_HOSTROOT / output_host_dir.lstrip("/"), exist_ok=True)
    return _iter_logs(schema_dir=schema_host_dir, output_dir=output_host_dir)
