#!/usr/bin/env bash
# One-shot bootstrap for the local stack: build the schema, mint an org token.
#
# Runs to completion before ingest and the worker start (compose waits on it). Both are
# idempotent, because `docker compose up` on an existing stack runs this again:
#   - `cli_db init` reports "exists" and touches nothing when the tables are there;
#   - the token is minted only when the file is absent, because the plaintext exists exactly
#     once and re-minting on every restart would hand the operator a token their fleet is not
#     configured with (arch §18.1 -- replaced, never recovered).
set -euo pipefail

TOKEN_FILE="${TALIUM_DATA_DIR}/org_token.txt"
ORG_ID="${TALIUM_ORG_ID:-local-dev}"

echo "[init] building schema ..."
python -m server.storage.cli_db --config config/storage.yaml init

if [[ -f "$TOKEN_FILE" ]]; then
  echo "[init] org token already issued; leaving it alone"
else
  echo "[init] issuing an org token for '${ORG_ID}' ..."
  python -m server.storage.cli_db --config config/storage.yaml \
      org-token issue --org "$ORG_ID" --descriptor '{"note":"local docker stack"}' \
    | tee "${TOKEN_FILE}.raw"
  # Keep just the key, so the helper scripts and the operator have one thing to read.
  grep -E '^\s*key\s' "${TOKEN_FILE}.raw" | awk '{print $2}' > "$TOKEN_FILE"
  rm -f "${TOKEN_FILE}.raw"
fi

echo
echo "==============================================================================="
echo " Claude Code settings block -- paste into ~/.claude/settings.json (env section)"
echo "==============================================================================="
cat <<EOF
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:${TALIUM_INGEST_PORT:-8443}/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer $(cat "$TOKEN_FILE")"
  }
}
EOF
echo "==============================================================================="
