#!/usr/bin/env bash
# Cache-write contract acceptance (design A1/A2): per-model cost-kernel vs request-level USD and
# the dashboard's 5m-at-1h residual, over a synth_org.sh output directory. Example:
#   cache_write_acceptance.sh --org-run econ-forecast/artifacts/org-demo \
#     --rate-sheet econ-forecast/examples/curve-guided/cost/baseline/rate_sheet.json
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.testing.cli_cache_write_acceptance "$@"
