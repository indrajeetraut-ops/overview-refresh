#!/usr/bin/env bash
# Bring up the local stack and print the Claude Code settings block.
#
#   utils/stack_up.sh            postgres + ingest + worker   (OTel only, zero-install tier)
#   utils/stack_up.sh --jsonl    ... plus uploader + collector (hybrid, the request_id join)
#   utils/stack_up.sh --sync     ... plus the Go uploader (talium-uploadd + tusd + MinIO) and
#                                a collector reading it (docker/docker-compose.sync.yml)
#
# --jsonl and --sync are the two JSONL paths and are mutually exclusive: their collectors share
# one checkpoint file. --sync stops the jsonl collector if it is running.
#
# --sync env (optional): TALIUM_S3_PREFIX (default cc/logs, the AWS demo's; set it empty for
# the bucket root), STACK_SYNC_PORT (8444), STACK_MINIO_PORT (9000), TALIUM_UPLOAD_SRC
# (../../upload, relative to docker/).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STACK_FILE="$ROOT/docker/docker-compose.stack.yml"
COMPOSE=("docker" "compose" "-f" "$STACK_FILE")

JSONL=0
SYNC=0
for arg in "$@"; do
  case "$arg" in
    --jsonl) JSONL=1 ;;
    --sync) SYNC=1 ;;
    *) echo "usage: $0 [--jsonl | --sync]" >&2; exit 2 ;;
  esac
done
if (( JSONL && SYNC )); then
  echo "[stack] --jsonl and --sync are mutually exclusive (their collectors share a checkpoint file)" >&2
  exit 2
fi

if (( JSONL )); then
  echo "[stack] including the JSONL path (uploader + collector)"
  COMPOSE+=(--profile jsonl)
fi

if (( SYNC )); then
  # shellcheck source=utils/_sync_env.sh
  source "$ROOT/utils/_sync_env.sh"
  sync_env_init "$ROOT"
  echo "[stack] including the Go uploader path (talium-uploadd + tusd + MinIO + collector-sync)," \
       "base prefix '${TALIUM_S3_PREFIX}'"
  # The jsonl collector must not sweep alongside collector-sync: same checkpoint file.
  if [[ -n "$(docker ps -q --filter name=^talium_stack_collector$)" ]]; then
    echo "[stack] stopping the jsonl collector (utils/stack_up.sh --jsonl brings it back)"
    docker compose -f "$STACK_FILE" --profile jsonl stop collector
  fi
  COMPOSE+=(-f "$ROOT/docker/docker-compose.sync.yml" --profile sync)
fi

"${COMPOSE[@]}" up -d --build --wait
echo

# The init container printed the settings block on its first run; on a restart it exits
# before you can read it, so re-print from the token the volume holds either way.
"${COMPOSE[@]}" run --rm --no-deps --entrypoint bash init -lc '
  token=$(cat "$TALIUM_DATA_DIR/org_token.txt")
  cat <<EOF
Dashboard   http://localhost:'"${STACK_API_PORT:-8090}"'   (manager screens need a directory
                                                    import first: utils/directory_import.sh --apply,
                                                    then rebuild with STACK_VIEWER_ID=<your id> set
                                                    to see them already authorized)
Ingest      http://localhost:'"${STACK_INGEST_PORT:-8443}"'   (OTLP at /otlp/v1/{metrics,logs})
Uploader    http://localhost:'"${STACK_UPLOADER_PORT:-8080}"'   (--jsonl only)
Sync        http://localhost:'"${STACK_SYNC_PORT:-8444}"'   (--sync only: talium-sync base_url; utils/sync_local.sh)
MinIO       http://localhost:'"${STACK_MINIO_CONSOLE_PORT:-9001}"'   (--sync only: console, minioadmin / minioadmin)
Postgres    postgresql://postgres@localhost:'"${STACK_PG_PORT:-5433}"'/econ

Add to ~/.claude/settings.json, then start a NEW Claude Code session:

{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:'"${STACK_INGEST_PORT:-8443}"'/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer ${token}"
  }
}
EOF'
