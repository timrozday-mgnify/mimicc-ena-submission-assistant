# Pinned refs for sibling repos this image pulls at build time. Bump these
# (and the matching git+https pins in pyproject.toml) when a sibling repo
# cuts a new tag — see README "Pinned dependency versions".
ARG DATAHARMONIZER_REF=v2.1.0-mimicc
ARG DH_BUILDER_REF=v0.1.0

# Pinned sibling-repo sources, replacing the old additional_contexts/vendor.sh
# local-checkout mechanism. Each is a shallow clone at a fixed tag.
FROM alpine/git:latest AS dataharmonizer-src
ARG DATAHARMONIZER_REF
RUN git clone --branch "${DATAHARMONIZER_REF}" --depth 1 \
      https://github.com/timrozday-mgnify/DataHarmonizer.git /src

FROM alpine/git:latest AS dh-builder-src
ARG DH_BUILDER_REF
RUN git clone --branch "${DH_BUILDER_REF}" --depth 1 \
      https://github.com/timrozday-mgnify/dh-builder.git /src

# Builds the embedded DataHarmonizer (DH) bundle from the pinned DataHarmonizer
# checkout above. Mirrors scripts/build_dh_template.sh. dh_build_steps.sh comes
# from the pinned dh-builder checkout — see
# https://github.com/timrozday-mgnify/dh-builder.
FROM node:20-slim AS dh-builder
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*

COPY --from=dataharmonizer-src /src /dh-src
COPY scripts/patch_dataharmonizer_toolbar.py /tmp/patch_dataharmonizer_toolbar.py
RUN python3 /tmp/patch_dataharmonizer_toolbar.py /dh-src
RUN pip install --no-cache-dir --break-system-packages -r /dh-src/requirements.txt

# The MIMICC LinkML schema(s) are committed in this repo's schemas/ —
# copy the whole directory (not a single named file) so this step doesn't
# fail if mimicc_experiment.yaml is ever absent.
COPY schemas/ /tmp/schemas/
COPY --from=dh-builder-src /src/scripts/dh_build_steps.sh /tmp/dh_build_steps.sh
# Sample (mimicc_sample.yaml) and experiment (mimicc_experiment.yaml) are two
# separate templates — see README "Experiment metadata schema". The
# experiment template builds alongside the sample one if its schema file is
# present, so the image build never breaks if it's ever missing.
RUN if [ -f /tmp/schemas/mimicc_experiment.yaml ]; then \
      DH_SKIP_BUILD=1 bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/mimicc_sample.yaml mimicc && \
      bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/mimicc_experiment.yaml mimicc_experiment; \
    else \
      bash /tmp/dh_build_steps.sh /dh-src /tmp/schemas/mimicc_sample.yaml mimicc; \
    fi

FROM python:3.11-slim

# docker CLI is required so the server can spawn the enasequence/webin-cli
# sibling container via the mounted docker socket (reads submission).
RUN apt-get update && apt-get install -y docker.io curl && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# ena_api, linkml_lib, dh_builder_lib and ena_submission_toolkit are pinned
# pip dependencies (see pyproject.toml) — no local build context or
# vendor.sh copy needed for them anymore.
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Schemas + ENA XSDs used for sample/study build + validation. These aren't
# Python packages — they're committed directly in this repo (schemas/,
# assets/ena_schema/), copied straight from the local build context.
COPY schemas/ schemas/
COPY assets/ena_schema/ assets/ena_schema/
# App code
COPY server/ server/
# Django ORM management entrypoint (migrations).
COPY manage.py manage.py
# Built DataHarmonizer bundle (see dh-builder stage above), staged separately
# from server/static/dh/ — that's a host bind mount (see docker-compose.yml)
# seeded from this default on first run by scripts/server_entrypoint.sh.
COPY --from=dh-builder /dh-src/web/dist/. dh-default/
COPY scripts/server_entrypoint.sh /usr/local/bin/server_entrypoint.sh
RUN chmod +x /usr/local/bin/server_entrypoint.sh

ENV PYTHONPATH=/app/server:/app

WORKDIR /app/server
ENTRYPOINT ["/usr/local/bin/server_entrypoint.sh"]
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:9000", "--workers", "2"]
