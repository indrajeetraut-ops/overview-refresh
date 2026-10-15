#!/usr/bin/env bash
# Regenerate the committed OpenAPI contract (src/server/api/openapi.json) from the FastAPI
# app's response models (MVP plan F0). Run after any change to server/api/schemas/ or a
# route's response_model.
#
#   utils/export_openapi.sh            # rewrite openapi.json
#   utils/export_openapi.sh --check    # exit 1 if it is stale (CI drift guard)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/_test_env.sh"
exec python -m server.api.cli_export_openapi "$@"
