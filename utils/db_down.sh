#!/usr/bin/env bash
# Stop the local dev/test Postgres. Pass -v (or --wipe) to also delete the data volume.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/docker/docker-compose.yml")
if [[ "${1:-}" == "-v" || "${1:-}" == "--wipe" ]]; then
  "${COMPOSE[@]}" down -v
  echo "postgres stopped; data volume removed."
else
  "${COMPOSE[@]}" down
  echo "postgres stopped (data volume kept; run 'utils/db_down.sh -v' to wipe)."
fi
