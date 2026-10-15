#!/usr/bin/env bash
# Wrapper for the synthetic org-expand loader (plan §5 P10).
#
#   utils/load_synthetic.sh --dataset econ-forecast/artifacts/org-demo/org \
#     --dsn postgresql+psycopg://<user>@localhost:5433/econ_synth --replace
#
# The guard refuses any database whose name does not end with `_synth` -- never point this at
# `econ`. `--dry-run` prints planned row counts and never connects.
set -euo pipefail
exec python -m server.storage.cli_load_synthetic "$@"
