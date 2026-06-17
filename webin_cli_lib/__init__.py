"""webin-cli Docker executor library.

Runs enasequence/webin-cli as a Docker container and streams its log output.
Has no assumptions about Docker-in-Docker or host-path mounting — callers are
responsible for path validation and creating the output directory.

Vendored verbatim from webin-cli-browser-assistant so the reads-submission path
is identical between the two apps.
"""

from __future__ import annotations

import subprocess
from collections.abc import Generator
from pathlib import Path

_WEBIN_CLI_IMAGE = "enasequence/webin-cli"


def iter_webin_cli_logs(
    *,
    context: str,
    manifest_path: str,
    input_dir: str,
    output_dir: str,
    username: str,
    password: str,
    submit: bool = False,
    test: bool = True,
) -> Generator[str, None, int]:
    """Run webin-cli in Docker and yield decoded log lines.

    Args:
        context: Submission context (e.g. "reads", "genome").
        manifest_path: Absolute path to the manifest file on the Docker host.
        input_dir: Absolute path to the directory containing reads/data files.
        output_dir: Absolute path to write webin-cli output on the Docker host.
        username: ENA Webin username (e.g. "Webin-12345").
        password: ENA Webin password.
        submit: If True, submit to ENA; if False, validate only.
        test: If True, use the ENA test service.

    Yields:
        Decoded log lines (without trailing newline).

    Returns:
        Process exit code via StopIteration.value.

    Raises:
        FileNotFoundError: If the docker binary is not on PATH.
    """
    manifest_filename = Path(manifest_path).name
    cmd = [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{input_dir}:/data:ro",
        "-v",
        f"{output_dir}:/output",
        "-e",
        f"ENA_WEBIN_PASSWORD={password}",
        _WEBIN_CLI_IMAGE,
        f"-context={context}",
        f"-manifest=/data/{manifest_filename}",
        f"-userName={username}",
        "-passwordEnv=ENA_WEBIN_PASSWORD",
        "-inputDir=/data",
        "-outputDir=/output",
        "-submit" if submit else "-validate",
    ]
    if test:
        cmd.append("-test")

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


def run_webin_cli(*, print_fn=print, **kwargs) -> int:
    """Stream webin-cli logs via print_fn and return the exit code.

    Accepts the same keyword arguments as iter_webin_cli_logs.
    """
    gen = iter_webin_cli_logs(**kwargs)
    try:
        while True:
            print_fn(next(gen))
    except StopIteration as si:
        return si.value
