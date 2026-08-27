"""Service layer over ena-api-client and ena-submission-toolkit.

Wraps the existing reusable functions so the views stay thin:

  * studies / samples   -> ``submit_study.submit_batch`` / ``submit_sample.submit_batch``
  * DH export -> records -> ``linkml_lib.dh_data.filter_columns`` + ``prepare_dh_output.prepare``
  * account records      -> ``WebinClient.reports.list_*``
  * lifecycle actions    -> ``WebinClient.submit.{cancel,suppress,release,hold,kill}``

Credentials are passed explicitly (held in server memory by ``main.py``) and
turned into a per-call ``WebinClient`` — nothing is read from or written to the
environment or disk.
"""

from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import _bootstrap  # schema/XSD asset paths — see _bootstrap.py

if TYPE_CHECKING:  # pragma: no cover
    from ena_api import WebinClient

# Heavy / optional dependencies (linkml, lxml, pendulum, typer, ena_api) are
# imported lazily inside the functions that use them, so that ``import main``
# succeeds for mock-based tests without the full scientific stack installed.

# Default column filter for MIMICC sample preparation (from
# shell/submit_mimicc_samples.sh): keep only sample/study-relevant slots.
DEFAULT_SAMPLE_FILTER = "source IN ('ERC000025', 'MIMICC.custom', 'ENA.sample', 'ENA.project')"

# Reports API entity -> ReportsProxy method.
_REPORT_METHODS = {
    "studies": "list_projects",
    "projects": "list_projects",
    "samples": "list_samples",
    "runs": "list_runs",
    "experiments": "list_experiments",
    "analyses": "list_analyses",
    "files": "list_files",
}

_ACTIONS = {"cancel", "suppress", "release", "hold", "kill"}


def _ena_api():
    from ena_api import WebinClient, WebinConfig  # type: ignore

    return WebinClient, WebinConfig


def _common():
    from ena_submission_toolkit import common  # type: ignore

    return common


def _prefixed(level: str, message: str) -> str:
    stripped = message.strip()
    if stripped.startswith(("INFO:", "WARNING:", "ERROR:")):
        return stripped
    return f"{level}: {message}"


def _study_project_summaries(xml_bytes: bytes) -> list[str]:
    """Return concise native ENA PROJECT identity details for diagnostics."""
    root = ET.fromstring(xml_bytes)
    summaries: list[str] = []
    for idx, project in enumerate(root.findall(".//PROJECT"), start=1):
        alias = project.attrib.get("alias", "")
        title = project.findtext("TITLE") or ""
        name = project.findtext("NAME")
        description = project.findtext("DESCRIPTION")
        summaries.append(
            f"PROJECT[{idx}]: alias={alias!r}, TITLE={title!r}, "
            f"NAME_present={name is not None}, DESCRIPTION_present={description is not None}"
        )
    return summaries


@contextmanager
def _capture_ena_logs() -> Iterator[Callable[[], list[str]]]:
    """Capture logs emitted by the vendored ENA submit helpers."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    handler.setLevel(logging.INFO)

    logger = logging.getLogger("ena_submit")
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        yield lambda: [line for line in stream.getvalue().splitlines() if line.strip()]
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@dataclass(frozen=True)
class Credentials:
    username: str
    password: str


@contextmanager
def webin_client(creds: Credentials, test: bool) -> Iterator[WebinClient]:
    """Build an authenticated WebinClient for the duration of the block."""
    WebinClient, WebinConfig = _ena_api()
    client = WebinClient(config=WebinConfig(webin_id=creds.username, password=creds.password, test=test))
    try:
        yield client
    finally:
        client.close()


def validate_credentials(creds: Credentials, *, test: bool) -> None:
    """Validate Webin credentials with a lightweight authenticated reports call."""
    with webin_client(creds, test) as client:
        client.reports.list_projects(max_results=1)


# ---------------------------------------------------------------------------
# Records browser
# ---------------------------------------------------------------------------


def _filter_by_status(rows: list[dict[str, Any]], status: str) -> list[dict[str, Any]]:
    if status.lower() == "all":
        return rows
    target = status.upper()
    return [r for r in rows if (r.get("status") or "").upper() == target]


def list_records(
    creds: Credentials,
    entity: str,
    *,
    test: bool,
    status: str = "all",
    max_results: int = 5000,
) -> list[dict[str, Any]]:
    """List account records for one entity type via the Webin Reports API."""
    method = _REPORT_METHODS.get(entity)
    if method is None:
        raise ValueError(f"Unknown entity {entity!r}; expected one of {', '.join(_REPORT_METHODS)}")
    with webin_client(creds, test) as client:
        # list_runs() already joins against list_experiments() to fill in
        # study_accession/sample_accession when the run's own report omits them.
        rows = [r.model_dump() for r in getattr(client.reports, method)(max_results=max_results)]
    if entity != "files":
        rows = _filter_by_status(rows, status)
    return rows


def lookup_existing_runs(
    creds: Credentials,
    aliases: set[str],
    *,
    test: bool,
    max_results: int = 5000,
) -> dict[str, dict[str, str]]:
    """Find runs already in ENA by their experiment alias (thin re-export — see
    ``ReportsProxy.find_runs_by_experiment_alias`` for the actual lookup,
    used by reads-submission resumability to detect "is this run already
    submitted?" on a resume)."""
    if not aliases:
        return {}
    with webin_client(creds, test) as client:
        return client.reports.find_runs_by_experiment_alias(aliases, max_results=max_results)


# ---------------------------------------------------------------------------
# Studies / samples submission
# ---------------------------------------------------------------------------


def _release_all(client: WebinClient, accessions: list[dict[str, Any]], key: str = "accession") -> None:
    for record in accessions:
        acc = record.get(key)
        if not acc:
            continue
        try:
            receipt = client.submit.release(acc)
            record["release_status"] = (
                "released" if receipt.success else "failed: " + "; ".join(receipt.messages + receipt.errors)
            )
        except Exception as exc:  # noqa: BLE001 - report, don't crash the batch
            record["release_status"] = f"failed: {exc}"


def submit_studies(
    creds: Credentials,
    records: list[dict[str, Any]],
    *,
    test: bool,
    modify: bool = False,
    hold_until: str | None = None,
    public: bool = False,
    max_results: int = 5000,
) -> dict[str, Any]:
    """Create (ADD) or modify (MODIFY) studies. Returns {success, accessions}."""
    from ena_submission_toolkit import submit_study  # type: ignore

    common = _common()
    with _capture_ena_logs() as get_logs:
        workflow_logs: list[str] = []

        def log(message: str) -> None:
            workflow_logs.append(f"INFO: {message}")

        def warn(message: str) -> None:
            workflow_logs.append(f"WARNING: {message}")

        def error_log(message: str) -> None:
            workflow_logs.append(f"ERROR: {message}")

        def logs_with_toolkit() -> list[str]:
            return workflow_logs + get_logs()

        def log_record_summary(batch: list[dict[str, Any]]) -> None:
            log("Prepared study record summary:")
            for idx, record in enumerate(batch, start=1):
                alias = record.get("alias") or "<missing alias>"
                title = record.get("TITLE") or "<missing TITLE>"
                desc = record.get("DESCRIPTION") or ""
                name = record.get("NAME") or "<not set>"
                missing = [field for field in ("TITLE",) if not str(record.get(field) or "").strip()]
                log(f"  record {idx}: alias={alias!r}, title={title!r}, name={name!r}, description_chars={len(desc)}")
                if missing:
                    warn(f"  record {idx}: missing recommended/required field(s): {', '.join(missing)}")

        try:
            env_label = "TEST" if test else "PRODUCTION"
            log(
                "Starting study submission: "
                f"records={len(records)}, action={'MODIFY' if modify else 'ADD'}, env={env_label}, "
                f"hold_until={hold_until or 'none'}, release_public={public}"
            )
            if hold_until:
                log(f"Validating hold-until date: {hold_until}")
                common.validate_hold_until(hold_until)
                log("Hold-until date validation passed")
            else:
                log("No hold-until date supplied; skipping hold-date validation")

            xsd = _bootstrap.xsd_dir()
            log(f"Using ENA XSD directory: {xsd}")

            log("Opening authenticated ENA Webin client")
            with webin_client(creds, test) as client:
                log("ENA Webin client opened")
                if modify:
                    log(f"Modify mode: fetching up to {max_results} existing studies from ENA reports")
                    account = [r.model_dump() for r in client.reports.list_projects(max_results=max_results)]
                    log(f"Modify mode: fetched {len(account)} existing study record(s)")
                    log("Modify mode: matching prepared records to existing studies by alias/title")
                    dups = common.find_duplicates_by_alias_title(
                        records, account, title_field="TITLE", entity_label="studies"
                    )
                    _, to_submit, _ = common.classify_duplicates(records, dups, title_field="TITLE", force=True)
                    log(f"Modify mode: {len(to_submit)} prepared record(s) matched existing studies")
                    if not to_submit:
                        error = "No matching existing studies to modify"
                        return {
                            "success": False,
                            "accessions": [],
                            "error": error,
                            "logs": logs_with_toolkit() + [f"ERROR: {error}"],
                        }
                    action, batch = "MODIFY", to_submit
                else:
                    action, batch = "ADD", records
                    log(f"Add mode: submitting all {len(batch)} prepared study record(s)")

                log_record_summary(batch)
                log(f"Building ENA study XML manifest: action={action}, records={len(batch)}")
                xml_bytes = submit_study.build_manifest(batch, hold_until=hold_until, action=action)
                log(f"Built ENA study XML manifest: bytes={len(xml_bytes)}")
                for summary in _study_project_summaries(xml_bytes):
                    log(f"Built native ENA project identity: {summary}")

                log("Validating study XML manifest against ENA.project.xsd")
                is_valid, xsd_msgs = submit_study.validate_manifest(xml_bytes, xsd)
                if xsd_msgs:
                    log("XSD validation messages:")
                    for msg in xsd_msgs:
                        workflow_logs.append(_prefixed("INFO", f"  {msg}"))
                else:
                    log("XSD validation returned no detail messages")
                log(f"XSD validation result: valid={is_valid}")
                if not is_valid:
                    error = "Study XML failed local XSD validation; it was not submitted to ENA."
                    error_log(error)
                    return {"success": False, "accessions": [], "error": error, "logs": logs_with_toolkit()}

                log(f"Pre-validation passed; submitting study XML to ENA ({env_label})")
                success, accessions, receipt_msgs = submit_study.submit_manifest(xml_bytes, client)
                log(
                    "ENA receipt parsed: "
                    f"success={success}, accession_records={len(accessions or [])}, message_count={len(receipt_msgs or [])}"
                )
                if receipt_msgs:
                    log("ENA receipt messages:")
                    receipt_level = "ERROR" if not success else "INFO"
                    for msg in receipt_msgs:
                        stripped = msg.strip()
                        for prefix in ("INFO:", "WARNING:", "ERROR:"):
                            if stripped.startswith(prefix):
                                level, text = prefix[:-1], stripped[len(prefix) :].strip()
                                break
                        else:
                            level, text = receipt_level, stripped
                        workflow_logs.append(_prefixed(level, f"Receipt: {text}"))
                else:
                    warn("ENA receipt contained no messages or errors")
                if not success:
                    error_log("ENA receipt reported failure for the study submission")
                if success and public:
                    log(f"Release-public requested: releasing {len(accessions)} submitted study accession(s)")
                    _release_all(client, accessions)
                    for record in accessions:
                        log(
                            "  release result: "
                            f"alias={record.get('alias', '')!r}, accession={record.get('accession', '')!r}, "
                            f"status={record.get('release_status', '<not reported>')!r}"
                        )
                    log("Release-public step finished")
                if success:
                    log(f"Study submission succeeded: accession_records={len(accessions or [])}")

            diagnostic = next(
                (m for m in reversed(receipt_msgs) if m.upper().startswith(("WARNING:", "ERROR:"))),
                None,
            )
            error = (
                None
                if success
                else (diagnostic or "ENA rejected the study submission; see receipt messages in the log.")
            )
            result = {"success": success, "accessions": accessions, "logs": logs_with_toolkit()}
            if error:
                result["error"] = error
            return result
        except Exception as exc:  # noqa: BLE001 - return full diagnostics to the UI
            logs = logs_with_toolkit()
            logs.append(f"ERROR: {exc}")
            return {"success": False, "accessions": [], "error": str(exc), "logs": logs}


def submit_samples(
    creds: Credentials,
    records: list[dict[str, Any]],
    *,
    test: bool,
    modify: bool = False,
    checklist: str | None = None,
    hold_until: str | None = None,
    public: bool = False,
    max_results: int = 5000,
) -> dict[str, Any]:
    """Create (ADD) or modify (MODIFY) samples. Returns {success, accessions}."""
    from ena_submission_toolkit import submit_sample  # type: ignore
    from linkml_lib import io as linkml_io  # type: ignore
    from linkml_lib import schema as linkml_schema  # type: ignore

    common = _common()
    with _capture_ena_logs() as get_logs:
        try:
            if hold_until:
                common.validate_hold_until(hold_until)
            env_label = "TEST" if test else "PRODUCTION"
            xsd = _bootstrap.xsd_dir()
            unit_rules = linkml_schema.unit_rules(linkml_io.load_yaml(_bootstrap.schema_path()))

            with webin_client(creds, test) as client:
                if modify:
                    account = [r.model_dump() for r in client.reports.list_samples(max_results=max_results)]
                    dups = common.find_duplicates_by_alias_title(
                        records, account, title_field="SAMPLE_TITLE", entity_label="samples"
                    )
                    _, to_submit, _ = common.classify_duplicates(records, dups, title_field="SAMPLE_TITLE", force=True)
                    if not to_submit:
                        error = "No matching existing samples to modify"
                        return {
                            "success": False,
                            "accessions": [],
                            "error": error,
                            "logs": get_logs() + [f"ERROR: {error}"],
                        }
                    action, batch = "MODIFY", to_submit
                else:
                    action, batch = "ADD", records

                success, accessions = submit_sample.submit_batch(
                    batch,
                    action,
                    xsd=xsd,
                    hold_until=hold_until,
                    checklist_id=checklist,
                    unit_rules=unit_rules,
                    client=client,
                    env_label=env_label,
                )
                if success and public:
                    _release_all(client, accessions)
            return {"success": success, "accessions": accessions, "logs": get_logs()}
        except Exception as exc:  # noqa: BLE001 - return full diagnostics to the UI
            logs = get_logs()
            logs.append(f"ERROR: {exc}")
            return {"success": False, "accessions": [], "error": str(exc), "logs": logs}


# ---------------------------------------------------------------------------
# DataHarmonizer export -> prepared sample records
# ---------------------------------------------------------------------------


def prepare_samples(dh_export: dict[str, Any], *, where: str | None = DEFAULT_SAMPLE_FILTER) -> dict[str, Any]:
    """Filter a DataHarmonizer export to sample fields and rename to ENA field names.

    Mirrors ``ena_cli.sample_prepare``: ``dh_data.filter_columns`` then
    ``prepare_dh_output.prepare_data`` (the pure in-memory variant — no
    temp-file round trip needed). Returns the full ``Container``-wrapped dict.
    """
    from ena_submission_toolkit import prepare_dh_output  # type: ignore
    from linkml_lib import dh_data  # type: ignore
    from linkml_lib import io as linkml_io  # type: ignore

    schema = linkml_io.load_yaml(_bootstrap.schema_path())

    data = dh_export
    if where:
        data = dh_data.filter_columns(data, schema, where)

    return prepare_dh_output.prepare_data(data, schema)


def prepare_studies(dh_export: dict[str, Any], *, dh_dir: Any) -> dict[str, Any]:
    """Rename DH export columns to ENA study field names using the selected study schema."""
    from pathlib import Path as _Path

    from ena_submission_toolkit import prepare_dh_output  # type: ignore
    from linkml_lib import io as linkml_io  # type: ignore

    schema_yaml = _Path(dh_dir) / "templates" / "study" / "schema.yaml"
    if not schema_yaml.exists():
        raise ValueError("No study schema selected. Use the Studies tab to select a schema first.")
    schema = linkml_io.load_yaml(schema_yaml)
    return prepare_dh_output.prepare_data(dh_export, schema)


def records_from_container(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the record list out of a prepared ``Container`` export (thin
    re-export — see ``ena_common.extract_records_from_json`` for the
    canonical unwrap, shared with ``submit_sample``/``prepare_dh_output``)."""
    return _common().extract_records_from_json(prepared) or []


# ---------------------------------------------------------------------------
# Lifecycle actions
# ---------------------------------------------------------------------------


def run_action(
    creds: Credentials,
    action: str,
    accession: str,
    *,
    test: bool,
    alias: str | None = None,
    hold_until: str | None = None,
) -> dict[str, Any]:
    """Run a single submission action against an accession."""
    if action not in _ACTIONS:
        raise ValueError(f"Unknown action {action!r}; expected one of {', '.join(sorted(_ACTIONS))}")
    if action == "hold":
        if not hold_until:
            raise ValueError("hold requires a hold_until date")
        _common().validate_hold_until(hold_until)

    result: dict[str, Any] = {"accession": accession, "action": action}
    with webin_client(creds, test) as client:
        fn = getattr(client.submit, action)
        kwargs: dict[str, Any] = {"alias": alias} if alias else {}
        args: tuple[Any, ...] = (accession, hold_until) if action == "hold" else (accession,)
        try:
            receipt = fn(*args, **kwargs)
            result["success"] = receipt.success
            result["messages"] = "; ".join(receipt.messages + receipt.errors)
        except Exception as exc:  # noqa: BLE001
            result["success"] = False
            result["messages"] = str(exc)
    return result
