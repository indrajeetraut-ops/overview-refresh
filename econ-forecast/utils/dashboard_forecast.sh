#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.dashboard.cli_dashboard_forecast "$@"
