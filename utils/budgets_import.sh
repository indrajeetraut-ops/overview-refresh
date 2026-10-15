#!/usr/bin/env bash
# Import team/org/repo budgets from a YAML file (MVP plan F1). Dry-run by
# default -- pass --apply to write.
#
#   utils/budgets_import.sh --source config/budgets.yaml
#   utils/budgets_import.sh --source config/budgets.yaml --apply
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"
exec python -m server.api.manager.cli_budgets "$@"
