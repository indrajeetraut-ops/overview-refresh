#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_forecast_env.sh"
export MPLCONFIGDIR="${TMPDIR:-/tmp}/talium-matplotlib-${UID:-user}"
mkdir -p "$MPLCONFIGDIR"
exec "$FORECAST_PYTHON" -m forecast.forecasting.cli_forecast "$@"
