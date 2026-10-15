#!/usr/bin/env bash
# Regenerate the front-end's API types from the backend-owned OpenAPI contract
# (MVP plan F0/U1): server/api/schemas -> openapi.json -> src/webui/src/data/api-types.ts.
# api-types.ts is committed; drift is a build failure, not a convention.
#
#   utils/gen_api_types.sh            # rewrite openapi.json + api-types.ts
#   utils/gen_api_types.sh --check    # exit 1 if either is stale (CI drift guard)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCHEMA="$ROOT/src/server/api/openapi.json"
TYPES="$ROOT/src/webui/src/data/api-types.ts"

check=0
[[ "${1:-}" == "--check" ]] && check=1

# 1. backend schema
if [[ "$check" == 1 ]]; then
  "$ROOT/utils/export_openapi.sh" --check
else
  "$ROOT/utils/export_openapi.sh"
fi

# 2. front-end types
[[ -d "$ROOT/src/webui/node_modules" ]] || (cd "$ROOT/src/webui" && npm ci)
gen() { (cd "$ROOT/src/webui" && npx openapi-typescript "$SCHEMA" -o "$1"); }

if [[ "$check" == 1 ]]; then
  tmp="$(mktemp)"; trap 'rm -f "$tmp"' EXIT
  gen "$tmp"
  if ! diff -q "$TYPES" "$tmp" >/dev/null 2>&1; then
    echo "$TYPES is stale -- run: utils/gen_api_types.sh" >&2
    exit 1
  fi
  echo "$TYPES is up to date"
else
  gen "$TYPES"
  echo "wrote $TYPES"
fi
