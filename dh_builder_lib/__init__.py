"""DataHarmonizer (DH) bundle builder Docker executor library.

Runs the mimicc-dh-builder image as a Docker container and streams its log
output. Has no assumptions about Docker-in-Docker or host-path mounting —
callers are responsible for path validation and creating the output
directory.

Mirrors the read-helper's webin-cli sibling-container pattern so the
on-demand DH rebuild path follows the same approach.
"""

from __future__ import annotations

import subprocess
from collections.abc import Generator

_DH_BUILDER_IMAGE = "mimicc-dh-builder"


def iter_dh_builder_logs(
    *,
    schema_dir: str,
    output_dir: str,
) -> Generator[str, None, int]:
    """Rebuild the DH bundle in Docker and yield decoded log lines.

    Args:
        schema_dir: Absolute path on the Docker host to a directory
            containing the LinkML schema as ``mimicc.yaml``.
        output_dir: Absolute path on the Docker host to write the built
            bundle to (typically the host path backing server/static/dh/).

    Yields:
        Decoded log lines (without trailing newline).

    Returns:
        Process exit code via StopIteration.value.

    Raises:
        FileNotFoundError: If the docker binary is not on PATH.
    """
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{schema_dir}:/schema:ro",
        "-v",
        f"{output_dir}:/output",
        _DH_BUILDER_IMAGE,
    ]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    for line in proc.stdout:
        yield line.rstrip("\n")
    proc.wait()
    return proc.returncode


def run_dh_builder(*, print_fn=print, **kwargs) -> int:
    """Stream dh-builder logs via print_fn and return the exit code.

    Accepts the same keyword arguments as iter_dh_builder_logs.
    """
    gen = iter_dh_builder_logs(**kwargs)
    try:
        while True:
            print_fn(next(gen))
    except StopIteration as si:
        return si.value
