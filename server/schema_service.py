"""Schema library: list/save/delete LinkML schemas, import/merge ENA XML/XSD/
YAML sources into a new schema, and install a chosen schema into a
DataHarmonizer grid's served template folder.

Selecting a schema for a grid recompiles the LinkML in-process
(``dataharmonizer_compile.compile_schema_json``) and overwrites that grid's
fixed template folder's ``schema.json`` directly. DataHarmonizer fetches that
file over HTTP at runtime (``lib/utils/templates.js: fetchSchema``), so this
takes effect on the next iframe reload — no ``yarn build`` rebuild needed.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import _bootstrap
import yaml
from linkml_lib import dataharmonizer_compile, pipeline
from linkml_lib import io as linkml_io

# Fixed DataHarmonizer template folders the two grids are pointed at
# (server/static/app.js: initDhFrames). Selecting a schema for a role
# overwrites that folder's schema.json rather than registering a new folder.
ROLE_FOLDERS = {"sample": "mimicc", "experiment": "mimicc_experiment"}

_SLUG_RE = re.compile(r"[^a-z0-9_-]+")


def _slugify(name: str) -> str:
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "schema"


def _ensure_seeded() -> None:
    """Seed the writable schema library from the bundled defaults on first use."""
    target = _bootstrap.schemas_dir()
    if any(target.glob("*.yaml")):
        return
    try:
        source_dir = _bootstrap.vendor_schemas_dir()
    except RuntimeError:
        return
    for src in source_dir.glob("*.yaml"):
        (target / src.name).write_text(src.read_text(encoding="utf-8"), encoding="utf-8")


def _schema_path(schema_id: str) -> Path:
    safe = _slugify(schema_id)
    return _bootstrap.schemas_dir() / f"{safe}.yaml"


def list_schemas() -> list[dict[str, Any]]:
    """Schemas in the writable library (seeded from bundled defaults on first use)."""
    _ensure_seeded()
    out: list[dict[str, Any]] = []
    for path in sorted(_bootstrap.schemas_dir().glob("*.yaml")):
        try:
            schema = linkml_io.load_yaml(path)
        except Exception:
            continue
        if not isinstance(schema, dict):
            continue
        out.append(
            {
                "id": path.stem,
                "name": schema.get("name") or path.stem,
                "title": schema.get("title") or schema.get("name") or path.stem,
                "description": schema.get("description"),
            }
        )
    return out


def read_schema(schema_id: str) -> str:
    _ensure_seeded()
    path = _schema_path(schema_id)
    if not path.exists():
        raise ValueError(f"Schema not found: {schema_id}")
    return path.read_text(encoding="utf-8")


def save_schema(name: str, yaml_text: str) -> str:
    """Validate and save LinkML YAML text under an id slugified from `name`
    (or the schema's own `name` field if `name` is blank). Returns the id."""
    try:
        schema = linkml_io.load_yaml_text(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    schema_id = _slugify(name or schema.get("name") or "schema")
    _schema_path(schema_id).write_text(yaml_text, encoding="utf-8")
    return schema_id


def delete_schema(schema_id: str) -> None:
    path = _schema_path(schema_id)
    if not path.exists():
        raise ValueError(f"Schema not found: {schema_id}")
    path.unlink()


def list_ena_sources() -> dict[str, list[dict[str, str]]]:
    """Bundled ENA checklist XML / SRA+project XSD files importable as schema sources."""
    base = _bootstrap.xsd_dir()
    checklists: list[dict[str, str]] = []
    for sub in (base, base / "checklists"):
        if not sub.exists():
            continue
        for p in sorted(sub.glob("*.xml")):
            checklists.append({"id": str(p.relative_to(base)), "filename": p.name, "kind": "checklist"})
    xsds = [{"id": p.name, "filename": p.name, "kind": "xsd"} for p in sorted(base.glob("*.xsd"))]
    return {"checklists": checklists, "xsd": xsds}


def _resolve_source_path(source_id: str) -> Path:
    base = _bootstrap.xsd_dir().resolve()
    candidate = (base / source_id).resolve()
    if candidate != base and base not in candidate.parents:
        raise ValueError(f"Invalid source id: {source_id}")
    if not candidate.exists():
        raise ValueError(f"Source not found: {source_id}")
    return candidate


def import_build(
    *,
    source_ids: list[str] | None = None,
    schema_ids: list[str] | None = None,
    upload_paths: list[Path] | None = None,
    name: str | None = None,
    title: str | None = None,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
) -> str:
    """Convert+merge bundled ENA XML/XSD sources, existing saved schemas, and
    uploaded files into one LinkML schema (generic "import" for building a new
    schema). Priority = the order given (earlier inputs win on conflicts).
    Returns the merged schema as LinkML YAML text (not saved).
    """
    paths: list[Path] = []
    for sid in source_ids or []:
        paths.append(_resolve_source_path(sid))
    for sid in schema_ids or []:
        paths.append(_schema_path(sid))
    for p in upload_paths or []:
        paths.append(Path(p))
    if not paths:
        raise ValueError("No input sources given")

    schema = pipeline.build(paths, name=name, title=title, include=include, exclude=exclude)
    _normalise_slot_source_annotations(schema)
    return linkml_io.dump_yaml(schema)


def _normalise_slot_source_annotations(schema: dict) -> None:
    """Move legacy generated slot source annotations to top-level provenance."""
    for slot in (schema.get("slots") or {}).values():
        annotations = slot.get("annotations")
        if not isinstance(annotations, dict) or "source" not in annotations:
            continue
        slot.setdefault("source", annotations.pop("source"))
        if not annotations:
            slot.pop("annotations", None)


def _template_class_name(schema: dict[str, Any]) -> str:
    """Return the class name DataHarmonizer should render for this schema."""
    classes = schema.get("classes") or {}
    if not isinstance(classes, dict):
        raise ValueError("Schema has no renderable classes")

    schema_name = schema.get("name")
    if schema_name in classes and schema_name not in {"Container", "dh_interface"}:
        return schema_name

    dh_classes = [
        class_name
        for class_name, class_def in classes.items()
        if class_name not in {"Container", "dh_interface"}
        and isinstance(class_def, dict)
        and class_def.get("is_a") == "dh_interface"
    ]
    if dh_classes:
        return dh_classes[0]

    renderable_classes = [class_name for class_name in classes if class_name not in {"Container", "dh_interface"}]
    if renderable_classes:
        return renderable_classes[0]

    raise ValueError("Schema has no renderable classes")


def select_for_grid(role: str, yaml_text: str, *, dh_dir: Path) -> str:
    """Compile `yaml_text` and install it as the served schema.json for the
    role's fixed DataHarmonizer template folder. Returns the
    `<folder>/<schema name>` path the frontend points the iframe's
    `?template=` at.
    """
    folder = ROLE_FOLDERS.get(role)
    if folder is None:
        raise ValueError(f"Unknown role: {role}. Expected one of {sorted(ROLE_FOLDERS)}")

    try:
        schema = linkml_io.load_yaml_text(yaml_text)
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML: {exc}") from exc
    template_name = _template_class_name(schema)
    compiled = dataharmonizer_compile.compile_schema_json(schema)

    tpl_dir = dh_dir / "templates" / folder
    tpl_dir.mkdir(parents=True, exist_ok=True)
    (tpl_dir / "schema.json").write_text(json.dumps(compiled, indent=2), encoding="utf-8")
    export_js = tpl_dir / "export.js"
    if not export_js.exists():
        export_js.write_text("export default {};\n", encoding="utf-8")

    registry_path = dh_dir / "dh-template-registry.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        registry = {}
    registry[folder] = template_name
    registry_path.write_text(json.dumps(registry, indent=2), encoding="utf-8")

    return f"{folder}/{template_name}"
