#!/usr/bin/env bash
# Fetch the full set of public ENA sample-checklist XML definitions (beyond
# the three already committed at the top level of assets/ena_schema/ --
# ERC000015/22/25, used directly by submit_sample.py) so the schema "import"
# feature (POST /api/schemas/import, schema_service.list_ena_sources) can draw
# on all of them.
#
# ENA does not publish a stable listing endpoint for checklist accessions, so
# this probes the documented per-accession endpoint
# (https://ena-docs.readthedocs.io/en/latest/retrieval/programmatic-access/browser-api.html)
# across the known accession range and keeps only the ones that resolve to a
# real <CHECKLIST> definition.
#
# assets/ena_schema/checklists/ is committed, so files fetched here persist
# across builds — re-run occasionally to pick up new checklist accessions
# ENA adds over time.
#
# Usage: bash scripts/fetch_ena_checklists.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/assets/ena_schema/checklists"
BASE_URL="https://www.ebi.ac.uk/ena/browser/api/xml"
# Observed live checklist accessions top out in the low 60s; padded with
# headroom for accessions ENA adds later.
MAX_N=120

mkdir -p "$DEST"

fetched=0
for n in $(seq -f "%06g" 1 "$MAX_N"); do
  acc="ERC$n"
  # Skip accessions already committed at the top level (used directly by
  # submit_sample.py) so the import list doesn't show duplicates.
  if [ -f "$ROOT/assets/ena_schema/$acc.xml" ]; then
    continue
  fi
  dest_file="$DEST/$acc.xml"
  tmp_file="$dest_file.tmp"
  http_code=$(curl -s -o "$tmp_file" -w "%{http_code}" "$BASE_URL/$acc") || http_code=000
  if [ "$http_code" = "200" ] && grep -q "<CHECKLIST " "$tmp_file" 2>/dev/null; then
    mv "$tmp_file" "$dest_file"
    fetched=$((fetched + 1))
    echo "fetched $acc"
  else
    rm -f "$tmp_file"
  fi
done

echo "Done. Fetched $fetched checklist(s) into $DEST"
