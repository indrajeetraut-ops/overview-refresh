#!/usr/bin/env bash
# Normalizer, standalone -- no uploader, engine, or network (docs/manual-testing.md §2).
#
#   utils/test_normalizer.sh                       # automated suite (§2a)
#   utils/test_normalizer.sh -k tool_pairing       # extra args pass through to pytest
#   utils/test_normalizer.sh path/to/session.jsonl [out.json]   # normalize a real file (§2b)
#
# The manual form auto-discovers a sibling <session>/subagents/ tree and writes an
# event-by-event JSON dump you can read directly.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"

if [[ "${1:-}" == *.jsonl ]]; then
  session="$1"
  out="${2:-$TALIUM_DATA_DIR/normalized.json}"
  echo "[normalizer] normalizing $session -> $out"
  python -m collector.cli_normalize "$session" --out "$out"
  echo "[normalizer] wrote $out"
else
  exec python -m pytest "$ROOT/tests/collector/normalizer" -q "$@"
fi
