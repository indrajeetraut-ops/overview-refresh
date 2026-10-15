#!/usr/bin/env bash
# Stop the local stack. `--wipe` also drops its volumes -- the database AND the org token,
# so the next `stack_up.sh` mints a new one and Claude Code has to be re-pointed at it.
# (With --sync ever used, --wipe also empties MinIO; var/stack-sync.env is kept, so the local
# talium-sync enrollment stays valid.)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE=("docker" "compose" "-f" "$ROOT/docker/docker-compose.stack.yml" "--profile" "jsonl")

# Include the sync overlay once it has ever been brought up (its env file exists), so its
# containers and the MinIO volume are part of the same `down`.
# shellcheck source=utils/_sync_env.sh
source "$ROOT/utils/_sync_env.sh"
if sync_env_load "$ROOT"; then
  COMPOSE+=("-f" "$ROOT/docker/docker-compose.sync.yml" "--profile" "sync")
fi

# --remove-orphans: overlays not listed above (docker-compose.synth.yml's api_synth) put
# containers in this same project. Left behind, one of them keeps `stack_data` mounted, and a
# volume in use is silently NOT removed by `down -v` -- the database is wiped but the old
# collector checkpoints and org_token.txt survive, so the fresh stack skips every session as
# already collected and never mints an org token into the new database.
if [[ "${1:-}" == "--wipe" ]]; then
  "${COMPOSE[@]}" down -v --remove-orphans
  left="$(docker volume ls -q --filter label=com.docker.compose.project=talium-stack)"
  if [[ -n "$left" ]]; then
    echo "[stack] WIPE INCOMPLETE -- these volumes survived (a container outside this project" >&2
    echo "        still uses them; see: docker ps -a --filter volume=<name>):" >&2
    echo "$left" | sed 's/^/          /' >&2
    exit 1
  fi
  echo "[stack] down, volumes removed -- next start issues a NEW org token"
else
  "${COMPOSE[@]}" down --remove-orphans
  echo "[stack] down; data kept (utils/stack_up.sh brings it back with the same token)"
fi
