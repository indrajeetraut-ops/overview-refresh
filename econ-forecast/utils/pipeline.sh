#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_forecast_env.sh
source "$SCRIPT_DIR/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.pipeline.cli_pipeline "$@"
