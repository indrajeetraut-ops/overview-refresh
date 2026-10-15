#!/usr/bin/env bash
# One-shot users -> roster -> series -> org-expand chain. Example:
#   synth_org.sh --config-dir econ-forecast/config/synthetic/org \
#     --output econ-forecast/artifacts/org-demo --end-date today
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.synthetic.org.cli_synth_org "$@"
