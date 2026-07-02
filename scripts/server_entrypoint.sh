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
fi

# Single-user, local-only: no database, no migrations, no admin bootstrap.
# All session data lives in the browser; the server is stateless.
exec "$@"
