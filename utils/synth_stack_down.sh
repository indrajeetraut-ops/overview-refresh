#!/usr/bin/env bash
# Remove the synthetic-data API + UI, leaving the real stack running.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec docker compose -f "$ROOT/docker/docker-compose.stack.yml" -f "$ROOT/docker/docker-compose.synth.yml" rm -sf api_synth
