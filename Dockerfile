# Builds the embedded DataHarmonizer (DH) bundle from a local DataHarmonizer
# checkout, supplied as the `dataharmonizer-src` additional build context (see
# docker-compose.yml). Mirrors scripts/build_dh_template.sh.
FROM node:20-slim AS dh-builder
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

COPY --from=dataharmonizer-src . /dh-src
RUN pip install --no-cache-dir --break-system-packages -r /dh-src/requirements.txt

# The MIMICC LinkML schema is already vendored by scripts/vendor.sh.
COPY vendor/schemas/mimicc_sample_experiment.yaml /tmp/mimicc.yaml
COPY scripts/dh_build_steps.sh /tmp/dh_build_steps.sh
RUN bash /tmp/dh_build_steps.sh /dh-src /tmp/mimicc.yaml mimicc

FROM python:3.11-slim

# docker CLI is required so the server can spawn the enasequence/webin-cli
# sibling container via the mounted docker socket (reads submission).
RUN apt-get update && apt-get install -y docker.io curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Vendored sibling code (populated by scripts/vendor.sh before building).
COPY vendor/ vendor/
# App code
COPY webin_cli_lib/ webin_cli_lib/
COPY dh_builder_lib/ dh_builder_lib/
COPY server/ server/
# Built DataHarmonizer bundle (see dh-builder stage above) and the schema it
# was built from, staged separately from server/static/dh/ and /dh-schema —
# those are host bind mounts (see docker-compose.yml) seeded from these
# defaults on first run by scripts/server_entrypoint.sh, so an on-demand
# rebuild (dh_builder_lib) can update them without an image rebuild.
COPY --from=dh-builder /dh-src/web/dist/. dh-default/
COPY vendor/schemas/mimicc_sample_experiment.yaml dh-schema-default/mimicc.yaml
COPY scripts/server_entrypoint.sh /usr/local/bin/server_entrypoint.sh
RUN chmod +x /usr/local/bin/server_entrypoint.sh

ENV PYTHONPATH=/app/server:/app
ENV ENA_DH_VENDOR=/app/vendor

WORKDIR /app/server
ENTRYPOINT ["/usr/local/bin/server_entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
