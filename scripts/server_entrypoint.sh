#!/usr/bin/env bash
# Seeds the host-mounted DH bundle dir from the image-baked default on first
# run (it's bind-mounted from the host and starts out empty), then execs the
# real CMD. This lets the bundle built at image time (Dockerfile's dh-builder
# stage) work out of the box while staying writable in place for later image
# rebuilds.
set -euo pipefail

if [ -d /app/dh-default ] && [ -z "$(ls -A /app/server/static/dh 2>/dev/null)" ]; then
  mkdir -p /app/server/static/dh
  cp -R /app/dh-default/. /app/server/static/dh/
elif [ -d /app/dh-default ]; then
  mkdir -p /app/server/static/dh
  # Keep the bind-mounted bundle's runtime-selected schemas, but refresh the
  # built application assets (main.js, CSS, index.html, etc.) from the image.
  (cd /app/dh-default && tar --exclude='./templates' --exclude='./dh-template-registry.json' -cf - .) |
    (cd /app/server/static/dh && tar -xf -)
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
echo "Bootstrapping admin account…"
python /app/manage.py bootstrap_admin

exec "$@"
