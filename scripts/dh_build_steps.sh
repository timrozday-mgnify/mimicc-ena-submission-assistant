#!/usr/bin/env bash
# Shared DataHarmonizer build steps: stage a LinkML schema as a DH template,
# compile it to schema.json, register it in menu.json, then run the webpack
# production build (yarn build:web).
#
# Used by scripts/build_dh_template.sh (host dev), the Dockerfile dh-builder
# stage (embedded bundle, build time), and Dockerfile.dh-builder's entrypoint
# (on-demand rebuild, run time) — kept in one place so the three don't drift.
#
# To stage MULTIPLE templates into one build (e.g. a sample schema + a
# separate experiment schema), call this once per schema with
# DH_SKIP_BUILD=1 for all but the last call — staging is cheap and
# idempotent; only the last call should run the (slow) yarn build:
#   DH_SKIP_BUILD=1 bash dh_build_steps.sh "$DH" sample.yaml mimicc
#   bash dh_build_steps.sh "$DH" experiment.yaml mimicc_experiment   # builds both
#
# Usage: dh_build_steps.sh <dataharmonizer_dir> <schema_yaml> [template_name]
set -euo pipefail

DATAHARMONIZER="$1"
SCHEMA="$2"
TEMPLATE="${3:-mimicc}"

[ -d "$DATAHARMONIZER" ] || { echo "DataHarmonizer not found at $DATAHARMONIZER" >&2; exit 1; }
[ -f "$SCHEMA" ]         || { echo "Schema not found at $SCHEMA" >&2; exit 1; }

TPL_DIR="$DATAHARMONIZER/web/templates/$TEMPLATE"
echo ">> Staging template at $TPL_DIR"
mkdir -p "$TPL_DIR/source"
cp "$SCHEMA" "$TPL_DIR/source/$TEMPLATE.yaml"

# Minimal export.js if the template doesn't ship one (DataHarmonizer requires it).
if [ ! -f "$TPL_DIR/export.js" ]; then
  printf 'export default {};\n' > "$TPL_DIR/export.js"
fi

echo ">> Compiling LinkML -> schema.json"
( cd "$TPL_DIR" && python3 "$DATAHARMONIZER/script/linkml.py" --input "source/$TEMPLATE.yaml" )

REGISTRY="$DATAHARMONIZER/.dh_template_registry.json"
echo ">> Registering template in menu.json"
python3 - "$DATAHARMONIZER/web/templates/menu.json" "$TPL_DIR/schema.json" "$TEMPLATE" "$REGISTRY" <<'PY'
import json, sys
menu_path, schema_path, folder, registry_path = sys.argv[1:5]
schema_name = json.load(open(schema_path))["name"]
try:
    menu = json.load(open(menu_path))
except FileNotFoundError:
    menu = {}
# One menu group per folder (NOT a single shared "MIMICC" key) — staging two
# different folders under one shared group previously clobbered the group's
# "folder" field with whichever was staged last, breaking lookup for the
# other. group_key is derived from folder so this is stable across runs and
# unchanged for the existing single-template case (folder "mimicc" -> key
# "MIMICC", matching today's behaviour exactly).
group_key = folder.upper()
group = menu.setdefault(group_key, {"folder": folder, "id": f"https://mimicc.example.org/{folder}", "version": "1.0.0"})
group["folder"] = folder
group.setdefault("templates", {})[schema_name] = {"name": schema_name, "display": True}
json.dump(menu, open(menu_path, "w"), indent=2)
print("menu.json updated")

# Small registry (folder -> schema name) so embedding pages can construct
# `?template=<folder>/<name>` without parsing DataHarmonizer's own menu.json
# shape. Accumulates across staging calls; copied into the build output once
# the build actually runs (see below).
try:
    registry = json.load(open(registry_path))
except FileNotFoundError:
    registry = {}
registry[folder] = schema_name
json.dump(registry, open(registry_path, "w"), indent=2)
PY

if [ "${DH_SKIP_BUILD:-}" = "1" ]; then
  echo "Staged $TEMPLATE (build deferred — DH_SKIP_BUILD=1)."
  exit 0
fi

echo ">> Building DataHarmonizer web bundle (yarn build:web)"
( cd "$DATAHARMONIZER" && yarn install --frozen-lockfile && yarn build:web )

# Workaround: `yarn build:web`'s final step (build:schemas -> clean:schemas)
# deletes web/dist/templates/ (including the schema.json files the earlier
# webpack.config.js step had just copied there) and only restores
# *.pdf/schema.yaml/exampleInput, not schema.json. But the app fetches
# /templates/<folder>/schema.json directly over HTTP when self-hosted
# (lib/utils/templates.js: fetchSchema), so without this every template's
# panel is left blank. Re-copy schema.json (incl. locale variants) back in.
echo ">> Restoring schema.json files into web/dist/templates (clean:schemas workaround)"
( cd "$DATAHARMONIZER/web/templates" && find . -name 'schema.json' -print0 ) |
  while IFS= read -r -d '' f; do
    dest="$DATAHARMONIZER/web/dist/templates/${f#./}"
    mkdir -p "$(dirname "$dest")"
    cp "$DATAHARMONIZER/web/templates/$f" "$dest"
  done

# Publish the folder->schema-name registry alongside the bundle so it ships
# wherever web/dist ships (e.g. into this app's server/static/dh/).
if [ -f "$REGISTRY" ]; then
  cp "$REGISTRY" "$DATAHARMONIZER/web/dist/dh-template-registry.json"
fi

echo "Done. Built bundle at $DATAHARMONIZER/web/dist"
