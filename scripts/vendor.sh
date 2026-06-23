#!/usr/bin/env bash
# Copy the sibling repos this app reuses into ./vendor/ so they can be added to
# PYTHONPATH (locally) and COPY'd into the Docker build context.
#
# Usage:
#   ENA_API_CLIENT=../ena-api-client \
#   ENA_DH=../ena-submission-dataharmonizer \
#   LINKML_LIB=../linkml-lib \
#   DH_BUILDER=../dh-builder \
#   bash scripts/vendor.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENA_API_CLIENT="${ENA_API_CLIENT:-$ROOT/../ena-api-client}"
ENA_DH="${ENA_DH:-$ROOT/../ena-submission-dataharmonizer}"
LINKML_LIB="${LINKML_LIB:-$ROOT/../linkml-lib}"
DH_BUILDER="${DH_BUILDER:-$ROOT/../dh-builder}"
VENDOR="$ROOT/vendor"

echo "ena-api-client: $ENA_API_CLIENT"
echo "ena-dh:         $ENA_DH"
echo "linkml-lib:     $LINKML_LIB"
echo "dh-builder:     $DH_BUILDER"
echo "vendor:         $VENDOR"

rm -rf "$VENDOR"
mkdir -p "$VENDOR/scripts" "$VENDOR/assets"

# ena_api package (added to sys.path => `import ena_api`)
cp -R "$ENA_API_CLIENT/ena_api" "$VENDOR/ena_api"

# ena-dh submission scripts (added to sys.path => `import ena_common`, etc.)
#   note: submit_reads.py is intentionally NOT copied — reads go via the local read-helper app.
for f in ena_common.py submit_sample.py submit_study.py prepare_dh_output.py; do
    cp "$ENA_DH/scripts/$f" "$VENDOR/scripts/$f"
done

# Shared LinkML utilities. This intentionally comes from the standalone
# linkml-lib package, not from ena-submission-dataharmonizer/scripts/linkml_lib.
cp -R "$LINKML_LIB/src/linkml_lib" "$VENDOR/linkml_lib"

# dh_builder_lib package (added to sys.path => `import dh_builder_lib`), from
# the standalone dh-builder repo.
cp -R "$DH_BUILDER/dh_builder_lib" "$VENDOR/dh_builder_lib"

# Schemas + ENA XSDs used for sample/study build + validation
cp -R "$ENA_DH/schemas" "$VENDOR/schemas"
cp -R "$ENA_DH/assets/ena_schema" "$VENDOR/assets/ena_schema"

# Clean any compiled caches
find "$VENDOR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "Vendored OK."
