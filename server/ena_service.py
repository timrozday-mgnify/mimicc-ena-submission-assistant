"""Service layer over ena-api-client and the ena-submission-dataharmonizer scripts.

Wraps the existing reusable functions so the FastAPI endpoints stay thin:

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
import json
import logging
import re
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import TYPE_CHECKING, Any

import _bootstrap  # side effect: extends sys.path to the vendored sibling code

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
_INTEGER_RE = re.compile(r"[+-]?[0-9]+")
_NUMBER_WITH_UNIT_RE = re.compile(
    r"^(?P<number>[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[Ee][+-]?[0-9]+)?)"
    r"(?:\s+(?P<unit>\S.+))?$"
)
_UNIT_CONVERSIONS = {
    ("mL", "L"): Decimal("0.001"),
    ("ml", "L"): Decimal("0.001"),
}


@dataclass(frozen=True)
class UnitRule:
    allowed_units: tuple[str, ...]
    default_unit: str | None = None


def _ena_api():
    from ena_api import WebinClient, WebinConfig  # type: ignore

    return WebinClient, WebinConfig


def _common():
    import ena_common as common  # type: ignore

    return common


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def _units_from_comments(comments: object) -> list[str]:
    result: list[str] = []
    seen_units = False
    for comment in _as_list(comments):
        if comment.startswith("Allowed units:"):
            seen_units = True
            result.extend(_as_list(comment.removeprefix("Allowed units:")))
        elif seen_units and len(comment) <= 30 and "." not in comment:
            result.append(comment)
    return result


def _sample_unit_rules(schema: dict[str, Any] | None = None) -> dict[str, UnitRule]:
    """Return prepared sample field name to allowed/default unit rules."""
    if schema is None:
        from linkml_lib import io as linkml_io  # type: ignore

        schema = linkml_io.load_yaml(_bootstrap.schema_path())

    rules: dict[str, UnitRule] = {}
    for slot_name, slot in (schema.get("slots") or {}).items():
        annotations = slot.get("annotations") or {}
        field_name = annotations.get("id") or slot.get("title") or slot_name
        allowed = _as_list(annotations.get("ena_allowed_units"))
        if not allowed:
            allowed = _units_from_comments(slot.get("comments"))
        default_unit = annotations.get("mimicc_default_unit") or annotations.get("default_unit")
        if allowed or default_unit:
            rules[str(field_name)] = UnitRule(tuple(dict.fromkeys(allowed)), default_unit)
    return rules


def _format_decimal(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _converted_unit_value(number: str, unit: str, allowed_units: tuple[str, ...]) -> tuple[str, str] | None:
    for target_unit in allowed_units:
        factor = _UNIT_CONVERSIONS.get((unit, target_unit))
        if factor is None:
            continue
        try:
            converted = Decimal(number) * factor
        except InvalidOperation as exc:
            raise ValueError(f"Could not parse numeric value {number!r}") from exc
        return _format_decimal(converted), target_unit
    return None


def _normalise_unit_value(field: str, value: str, rule: UnitRule) -> tuple[str, str | None]:
    match = _NUMBER_WITH_UNIT_RE.match(value)
    if match is None:
        return value, None

    number = match.group("number")
    unit = match.group("unit")
    if unit:
        if unit in rule.allowed_units:
            return number, unit
        converted = _converted_unit_value(number, unit, rule.allowed_units)
        if converted is not None:
            return converted
        allowed = ", ".join(rule.allowed_units) or "<none configured>"
        raise ValueError(f"{field!r} uses unsupported unit {unit!r}; allowed units: {allowed}")

    default_unit = rule.default_unit or (rule.allowed_units[0] if len(rule.allowed_units) == 1 else None)
    if default_unit is None:
        allowed = ", ".join(rule.allowed_units)
        raise ValueError(f"{field!r} value {value!r} needs an explicit unit; allowed units: {allowed}")
    if rule.allowed_units and default_unit not in rule.allowed_units:
        allowed = ", ".join(rule.allowed_units)
        raise ValueError(f"{field!r} default unit {default_unit!r} is not in allowed units: {allowed}")
    return value, default_unit


def _normalise_sample_records_for_submission(
    records: list[dict[str, Any]],
    unit_rules: dict[str, UnitRule],
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    """Return records and a field-wide unit map ready for XML submission."""
    logger = logging.getLogger("ena_submit.sample")
    normalised: list[dict[str, Any]] = []
    slot_to_unit: dict[str, str] = {}

    for record in records:
        out: dict[str, Any] = {}
        for field, raw_value in record.items():
            value = str(raw_value).strip() if raw_value is not None else ""
            if field == "library size" and value and _INTEGER_RE.fullmatch(value) is None:
                logger.info("Dropping optional field 'library size' with non-integer value: %s", value)
                continue

            rule = unit_rules.get(field)
            if rule is None or not value:
                out[field] = raw_value
                continue

            normalised_value, unit = _normalise_unit_value(field, value, rule)
            out[field] = normalised_value
            if unit is None:
                continue
            existing = slot_to_unit.get(field)
            if existing is not None and existing != unit:
                raise ValueError(f"{field!r} resolves to conflicting units: {existing!r}, {unit!r}")
            slot_to_unit[field] = unit
        normalised.append(out)

    if slot_to_unit:
        fields = ", ".join(f"{field}={unit}" for field, unit in sorted(slot_to_unit.items()))
        logger.info("Applied schema units to sample attributes: %s", fields)
    return normalised, slot_to_unit


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


def _enrich_runs_with_experiments(
    runs: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Attach experiment-derived study/sample accessions to run report rows."""
    experiments_by_accession = {exp.get("accession"): exp for exp in experiments if exp.get("accession")}
    enriched: list[dict[str, Any]] = []
    for run in runs:
        row = dict(run)
        experiment = experiments_by_accession.get(row.get("experiment_accession"))
        if experiment:
            row["study_accession"] = row.get("study_accession") or experiment.get("study_accession", "")
            row["sample_accession"] = row.get("sample_accession") or experiment.get("sample_accession", "")
        enriched.append(row)
    return enriched


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
        rows = [r.model_dump() for r in getattr(client.reports, method)(max_results=max_results)]
        if entity == "runs":
            experiments = [r.model_dump() for r in client.reports.list_experiments(max_results=max_results)]
            rows = _enrich_runs_with_experiments(rows, experiments)
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
    """Find runs already in ENA by their experiment alias.

    A reads submission registers an experiment (carrying the alias we control)
    plus a run. To detect "is this run already submitted?" on a resume we look
    up the experiment alias and map it to both accessions. Returns
    ``{alias: {"experiment_accession": ..., "run_accession": ...}}`` for the
    aliases that exist. Mirrors the alias-matching approach in
    ``ena_common.find_duplicates_by_alias_title`` used for studies/samples.
    """
    if not aliases:
        return {}
    with webin_client(creds, test) as client:
        experiments = [r.model_dump() for r in client.reports.list_experiments(max_results=max_results)]
        runs = [r.model_dump() for r in client.reports.list_runs(max_results=max_results)]

    runs_by_experiment: dict[str, str] = {}
    for run in runs:
        exp_acc = run.get("experiment_accession")
        run_acc = run.get("accession")
        if exp_acc and run_acc and exp_acc not in runs_by_experiment:
            runs_by_experiment[exp_acc] = run_acc

    found: dict[str, dict[str, str]] = {}
    for exp in experiments:
        alias = exp.get("alias")
        exp_acc = exp.get("accession")
        if alias in aliases and exp_acc:
            found[alias] = {
                "experiment_accession": exp_acc,
                "run_accession": runs_by_experiment.get(exp_acc, ""),
            }
    return found


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
    import submit_study  # type: ignore

    common = _common()
    if hold_until:
        common.validate_hold_until(hold_until)
    env_label = "TEST" if test else "PRODUCTION"
    xsd = _bootstrap.xsd_dir()

    with webin_client(creds, test) as client:
        if modify:
            account = [r.model_dump() for r in client.reports.list_projects(max_results=max_results)]
            dups = common.find_duplicates_by_alias_title(
                records, account, title_field="STUDY_TITLE", entity_label="studies"
            )
            _, to_submit, _ = common.classify_duplicates(records, dups, title_field="STUDY_TITLE", force=True)
            if not to_submit:
                return {"success": False, "accessions": [], "error": "No matching existing studies to modify"}
            action, batch = "MODIFY", to_submit
        else:
            action, batch = "ADD", records

        success, accessions = submit_study.submit_batch(
            batch, action, xsd=xsd, hold_until=hold_until, client=client, env_label=env_label
        )
        if success and public:
            _release_all(client, accessions)
    return {"success": success, "accessions": accessions}


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
    import submit_sample  # type: ignore

    common = _common()
    with _capture_ena_logs() as get_logs:
        try:
            if hold_until:
                common.validate_hold_until(hold_until)
            env_label = "TEST" if test else "PRODUCTION"
            xsd = _bootstrap.xsd_dir()
            unit_rules = _sample_unit_rules()

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

                batch, slot_to_unit = _normalise_sample_records_for_submission(batch, unit_rules)
                success, accessions = submit_sample.submit_batch(
                    batch,
                    action,
                    xsd=xsd,
                    hold_until=hold_until,
                    checklist_id=checklist,
                    slot_to_unit=slot_to_unit,
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
    ``prepare_dh_output.prepare`` (which works on a file path, so we shuttle
    through a temp file). Returns the full ``Container``-wrapped dict.
    """
    import prepare_dh_output  # type: ignore
    from linkml_lib import dh_data  # type: ignore
    from linkml_lib import io as linkml_io  # type: ignore

    schema_file = _bootstrap.schema_path()
    schema = linkml_io.load_yaml(schema_file)

    data = dh_export
    if where:
        data = dh_data.filter_columns(data, schema, where)

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        json.dump(data, tmp)
        tmp_path = Path(tmp.name)
    try:
        return prepare_dh_output.prepare(tmp_path, schema_file)
    finally:
        tmp_path.unlink(missing_ok=True)


def records_from_container(prepared: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull the record list out of a prepared ``Container`` export."""
    container = prepared.get("Container", {})
    for value in container.values():
        if isinstance(value, list):
            return value
    return []


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
