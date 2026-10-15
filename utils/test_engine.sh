#!/usr/bin/env bash
# Collector engine / adapter, standalone (docs/manual-testing.md §3).
#
#   utils/test_engine.sh                     # automated suite (§3a: store, checkpoint,
#                                            #   source_reader, engine, dump_transport)
#   utils/test_engine.sh -k checkpoint       # extra args pass through to pytest
#   utils/test_engine.sh collect [out.jsonl] # run the engine over already-staged data (§3),
#                                            #   dumping wire Envelopes instead of shipping
#
# `collect` needs data already staged (run `utils/test_uploader.sh serve` + POST first, §3).
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"

if [[ "${1:-}" == "collect" ]]; then
  out="${2:-$TALIUM_DATA_DIR/shipped.jsonl}"
  echo "[engine] collecting staged sources -> $out (dump mode, no live Ingest)"
  python -m collector.cli_collect --config "$ROOT/config/collector.yaml" --dump-to "$out"
  echo "[engine] wrote $out ($(wc -l < "$out" | tr -d ' ') envelopes)"
else
  exec python -m pytest "$ROOT/tests/collector" --ignore="$ROOT/tests/collector/normalizer" -q "$@"
fi
