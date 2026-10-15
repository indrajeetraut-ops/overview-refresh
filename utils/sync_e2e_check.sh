#!/usr/bin/env bash
# Check the local sync stack end to end: MinIO manifests + objects, byte identity against the
# local session files, and what folded into Postgres. See utils/sync_e2e_check.py.
#
#   utils/sync_e2e_check.sh [--source DIR] [--no-bytes]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=utils/_sync_env.sh
source "$ROOT/utils/_sync_env.sh"
sync_env_load "$ROOT" || true   # only for TALIUM_ORG_ID; the probe needs no secrets

PY="$ROOT/venv/bin/python"
[[ -x "$PY" ]] || PY=python
PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}" exec "$PY" "$ROOT/utils/sync_e2e_check.py" "$@"
