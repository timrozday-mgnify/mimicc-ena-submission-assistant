"""Server adapter for webin_cli_lib.

Adds Docker-in-Docker path validation via the /hostroot mount and creates the
output directory on the host filesystem before invoking the core library.

Vendored from webin-cli-browser-assistant.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from webin_cli_lib import iter_webin_cli_logs as _iter_logs

_HOSTROOT = Path(os.environ.get("HOSTROOT", "/hostroot"))


def _host_path_exists(host_path: str) -> bool:
    """Check that a host path exists (visible via /hostroot mount)."""
    return (_HOSTROOT / host_path.lstrip("/")).exists()


def host_to_local(host_path: str) -> Path:
    """Map a host path to the path visible inside the container via /hostroot."""
    return _HOSTROOT / host_path.lstrip("/")


def iter_webin_cli_logs(
    *,
    context: str,
    manifest_host_path: str,
    input_host_dir: str,
    output_host_dir: str,
    username: str,
    password: str,
    submit: bool,
    test: bool = True,
) -> Generator[str, None, int]:
    """Thin server wrapper around webin_cli_lib.iter_webin_cli_logs.

    Creates the output directory on the host filesystem (via /hostroot) before
    delegating to the library. Keyword argument names match main.py call sites.
    """
    os.makedirs(_HOSTROOT / output_host_dir.lstrip("/"), exist_ok=True)
    return _iter_logs(
        context=context,
        manifest_path=manifest_host_path,
        input_dir=input_host_dir,
        output_dir=output_host_dir,
        username=username,
        password=password,
        submit=submit,
        test=test,
    )
