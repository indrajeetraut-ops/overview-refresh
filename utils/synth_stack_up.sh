#!/usr/bin/env bash
# Bring up the synthetic-data API + UI beside the real stack (docker/docker-compose.synth.yml).
#
#   utils/synth_stack_up.sh                       serve econ_synth on http://localhost:8091
#   SYNTH_API_PORT=8092 utils/synth_stack_up.sh   ... on another port
#   SYNTH_VIEWER_ID=github:adamb utils/synth_stack_up.sh   ... as a lead instead of the admin
#
# The real stack must already be up (utils/stack_up.sh) -- this shares its Postgres container.
# The synthetic database must already be loaded (utils/load_synthetic.sh), which is what
# creates its schema; this service only reads it.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=("docker" "compose" "-f" "$ROOT/docker/docker-compose.stack.yml" "-f" "$ROOT/docker/docker-compose.synth.yml")

"${COMPOSE[@]}" up -d --build --wait api_synth
echo
cat <<EOF
Synthetic dashboard   http://localhost:${SYNTH_API_PORT:-8091}   (viewer: ${SYNTH_VIEWER_ID:-github:vp-eng})
Real dashboard        http://localhost:${STACK_API_PORT:-8090}

Both read the same Postgres container, different databases. Changing the viewer needs a
rebuild (Vite bakes it in): SYNTH_VIEWER_ID=<id> utils/synth_stack_up.sh
EOF
