#!/usr/bin/env bash
# Seeds the host-mounted DH bundle/schema dirs from the image-baked defaults
# on first run (both directories are bind-mounted from the host and start
# out empty), then execs the real CMD. This lets the bundle built at image
# time (Dockerfile's dh-builder stage) work out of the box, while leaving
# the same host directory writable by an on-demand rebuild (dh_builder_lib)
# without needing a container restart for the change to take effect.
set -euo pipefail

if [ -d /app/dh-default ] && [ -z "$(ls -A /app/server/static/dh 2>/dev/null)" ]; then
  mkdir -p /app/server/static/dh
  cp -R /app/dh-default/. /app/server/static/dh/
fi

if [ -f /app/dh-schema-default/mimicc.yaml ] && [ -z "$(ls -A /dh-schema 2>/dev/null)" ]; then
  mkdir -p /dh-schema
  cp /app/dh-schema-default/mimicc.yaml /dh-schema/mimicc.yaml
fi

# Apply database migrations before serving. When DATABASE_URL points at Postgres,
# wait for it to accept connections first (the db service may still be starting).
if [ -n "${DATABASE_URL:-}" ]; then
  echo "Waiting for the database…"
  for _ in $(seq 1 60); do
    if python -c "
import os, sys
from urllib.parse import urlparse
import psycopg
u = urlparse(os.environ['DATABASE_URL'])
try:
    psycopg.connect(host=u.hostname, port=u.port or 5432, user=u.username,
                    password=u.password, dbname=u.path.lstrip('/'), connect_timeout=2).close()
except Exception:
    sys.exit(1)
" 2>/dev/null; then
      break
    fi
    sleep 1
  done
fi
echo "Applying database migrations…"
python /app/manage.py migrate --noinput

exec "$@"
