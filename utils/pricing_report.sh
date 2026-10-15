#!/usr/bin/env bash
# Before/after pricing report for the model_pricing migration (#68): stored computed costs
# vs the same tokens repriced through the catalog. Read-only. Markdown on stdout, JSON logs on stderr.
#
#   utils/pricing_report.sh --dsn postgresql+psycopg://postgres@localhost:5432/econ_68_synth
#   utils/pricing_report.sh --dsn ... --org acme --version 2026-09-25-d3e5c412
set -euo pipefail
exec python -m server.worker.cli_pricing_report "$@"
