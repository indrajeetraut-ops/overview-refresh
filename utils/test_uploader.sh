#!/usr/bin/env bash
# Batch uploader, standalone (docs/manual-testing.md §1).
#
#   utils/test_uploader.sh              # automated suite (§1a)
#   utils/test_uploader.sh -k atomicity # extra args pass through to pytest
#   utils/test_uploader.sh serve        # start the real HTTP service on :8080 (§1b)
#
# In `serve` mode it stages into $TALIUM_DATA_DIR/uploader-staging; POST archives per §1b,
# then Ctrl-C to stop.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"

if [[ "${1:-}" == "serve" ]]; then
  echo "[uploader] serving on http://localhost:8080  (staging: $TALIUM_DATA_DIR/uploader-staging)"
  echo "[uploader] health: curl -sS http://localhost:8080/healthz"
  exec python -m server.uploader.cli_serve --config "$ROOT/config/uploader.yaml"
else
  exec python -m pytest "$ROOT/tests/server/uploader" -q "$@"
fi
