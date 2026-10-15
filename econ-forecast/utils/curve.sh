#!/usr/bin/env bash
# Shape-curve authoring tool. Example: curve.sh --config curve.yaml --output artifacts/curves --open
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.curve.cli_curve "$@"
