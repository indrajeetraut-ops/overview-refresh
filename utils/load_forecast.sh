#!/usr/bin/env bash
# Wrapper for the dashboard-forecast loader (docs/plan/2026-10-05-forecast-dashboard-integration.md T5).
#
#   utils/load_forecast.sh --export econ-forecast/artifacts/org-demo/forecast/forecast_export.json \
#     --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth
#
# Replaces the loaded forecast run in one transaction. The guard refuses any database whose
# name does not end with `_synth` -- never point this at `econ`.
set -euo pipefail
exec python -m server.storage.cli_load_forecast "$@"
