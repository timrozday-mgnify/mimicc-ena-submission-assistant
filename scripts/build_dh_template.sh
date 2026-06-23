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
#   DH_BUILDER_DIR=../dh-builder \
#   bash scripts/build_dh_template.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATAHARMONIZER="${DATAHARMONIZER:-$ROOT/../DataHarmonizer}"
ENA_DH="${ENA_DH:-$ROOT/../ena-submission-dataharmonizer}"
DH_BUILDER_DIR="${DH_BUILDER_DIR:-$ROOT/../dh-builder}"
SCHEMA="${ENA_DH_SCHEMA:-$ENA_DH/schemas/mimicc_sample.yaml}"
EXPERIMENT_SCHEMA="${ENA_DH_EXPERIMENT_SCHEMA:-$ENA_DH/schemas/mimicc_experiment.yaml}"
TEMPLATE="mimicc"
DEST="$ROOT/server/static/dh"

# Two separate templates — sample (mimicc_sample.yaml) and experiment
# (mimicc_experiment.yaml) — built into the same bundle, mirroring the
# Dockerfile's dh-builder stage. The experiment template is optional so this
# script doesn't break if that schema file is ever absent.
if [ -f "$EXPERIMENT_SCHEMA" ]; then
  DH_SKIP_BUILD=1 bash "$DH_BUILDER_DIR/scripts/dh_build_steps.sh" "$DATAHARMONIZER" "$SCHEMA" "$TEMPLATE"
  bash "$DH_BUILDER_DIR/scripts/dh_build_steps.sh" "$DATAHARMONIZER" "$EXPERIMENT_SCHEMA" mimicc_experiment
else
  bash "$DH_BUILDER_DIR/scripts/dh_build_steps.sh" "$DATAHARMONIZER" "$SCHEMA" "$TEMPLATE"
fi

echo ">> Copying bundle into $DEST"
rm -rf "$DEST"
mkdir -p "$DEST"
cp -R "$DATAHARMONIZER/web/dist/." "$DEST/"

echo "Done. DataHarmonizer bundle available under server/static/dh/"
