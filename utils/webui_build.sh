#!/usr/bin/env bash
# Build the dashboard front-end (src/webui) into src/webui/dist, which the FastAPI dashboard
# API serves at / (server/api/app.py, config.webui_dist). MVP plan U0.
#
#   utils/webui_build.sh            # npm ci + vite build
#   utils/webui_build.sh dev        # npm run dev (proxies /v1 -> FastAPI :8090)
#   utils/webui_build.sh typecheck  # tsc --noEmit
#
# Node 22+ (src/webui/.nvmrc). npm is the package manager.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT/src/webui"

cmd="${1:-build}"
case "$cmd" in
  build)      [[ -d node_modules ]] || npm ci ; npm run build ;;
  dev)        [[ -d node_modules ]] || npm ci ; npm run dev ;;
  typecheck)  [[ -d node_modules ]] || npm ci ; npm run typecheck ;;
  *) echo "usage: $0 [build|dev|typecheck]" >&2 ; exit 2 ;;
esac
