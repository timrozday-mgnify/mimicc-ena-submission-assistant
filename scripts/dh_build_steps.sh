#!/usr/bin/env bash
# Shared DataHarmonizer build steps: stage a LinkML schema as a DH template,
# compile it to schema.json, register it in menu.json, then run the webpack
# production build (yarn build:web).
#
# Used by scripts/build_dh_template.sh (host dev), the Dockerfile dh-builder
# stage (embedded bundle, build time), and Dockerfile.dh-builder's entrypoint
# (on-demand rebuild, run time) — kept in one place so the three don't drift.
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

echo ">> Registering template in menu.json"
python3 - "$DATAHARMONIZER/web/templates/menu.json" "$TPL_DIR/schema.json" "$TEMPLATE" <<'PY'
import json, sys
menu_path, schema_path, folder = sys.argv[1], sys.argv[2], sys.argv[3]
schema_name = json.load(open(schema_path))["name"]
try:
    menu = json.load(open(menu_path))
except FileNotFoundError:
    menu = {}
# Preserve existing group metadata (id/version/other templates); only this
# template's folder + menu entry are (re)registered. The schema's own "name"
# field is used as the menu key — it does NOT have to match the template
# folder name (DataHarmonizer keys templates by schema name, not folder).
group = menu.setdefault("MIMICC", {"folder": folder, "id": "https://mimicc.example.org/", "version": "1.0.0"})
group["folder"] = folder
group.setdefault("templates", {})[schema_name] = {"name": schema_name, "display": True}
json.dump(menu, open(menu_path, "w"), indent=2)
print("menu.json updated")
PY

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

echo "Done. Built bundle at $DATAHARMONIZER/web/dist"
