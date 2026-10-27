#!/bin/sh
set -eu

# Separate by design: the fast unit suite ignores tests/e2e. The fixture
# supplies synthetic auth values and the entire pytest process runs without
# inherited provider credentials.
exec env -i \
  HOME="$HOME" \
  TMPDIR="${TMPDIR:-/tmp}" \
  PATH="$PATH" \
  LANG="${LANG:-en_US.UTF-8}" \
  LOCAL_DEV=true \
  STORAGE_BACKEND=filesystem \
  GOOGLE_CLIENT_ID=e2e-local-client \
  GOOGLE_CLIENT_SECRET=e2e-local-secret \
  GOOGLE_AUTHORIZED_EMAILS=e2e@localhost \
  JWT_SECRET=e2e-local-jwt-secret-not-for-production \
  CRON_SECRET=e2e-local-cron-secret \
  uv run --extra dev pytest -o addopts='' tests/e2e "$@"
