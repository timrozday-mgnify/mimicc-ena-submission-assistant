#!/usr/bin/env bash
# Build a standalone DataHarmonizer bundle carrying the MIMICC template and
# drop it into server/static/dh/ so it can be served in-app (Samples tab).
#
# For local non-Docker development. `docker compose build` does the same
# thing automatically via the Dockerfile's dh-builder stage — you don't need
# to run this script for the Docker workflow.
#
# Requires: node + yarn installed, and a DataHarmonizer checkout.
#
# Usage:
#   DATAHARMONIZER=../DataHarmonizer \
#   ENA_DH=../ena-submission-dataharmonizer \
#   bash scripts/build_dh_template.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATAHARMONIZER="${DATAHARMONIZER:-$ROOT/../DataHarmonizer}"
ENA_DH="${ENA_DH:-$ROOT/../ena-submission-dataharmonizer}"
SCHEMA="${ENA_DH_SCHEMA:-$ENA_DH/schemas/mimicc_sample_experiment.yaml}"
TEMPLATE="mimicc"
DEST="$ROOT/server/static/dh"

bash "$ROOT/scripts/dh_build_steps.sh" "$DATAHARMONIZER" "$SCHEMA" "$TEMPLATE"

echo ">> Copying bundle into $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$DATAHARMONIZER/web/dist/." "$DEST/"

echo "Done. DataHarmonizer bundle available under server/static/dh/"
