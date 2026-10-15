#!/usr/bin/env bash
# Synthetic data generator. Example: synt_gen.sh --op user-create --config users.yaml --output artifacts/users
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_forecast_env.sh"
exec "$FORECAST_PYTHON" -m forecast.synthetic.cli_synt_gen "$@"
