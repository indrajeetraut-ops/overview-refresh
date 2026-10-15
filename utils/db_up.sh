#!/usr/bin/env bash
# Bring up the local dev/test Postgres (docker/docker-compose.yml) and wait until healthy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker compose -f "$ROOT/docker/docker-compose.yml" up -d --wait
port="${POSTGRES_PORT:-5432}"
db="${POSTGRES_DB:-econ_test}"
echo "postgres ready on localhost:${port} (db: ${db})"
echo "  export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:${port}/${db}"
