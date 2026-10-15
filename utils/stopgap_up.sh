#!/usr/bin/env bash
# Bring up the stop-gap stack on whatever host you are sitting on. Run this ON the target
# machine (not from your laptop) -- it is plain docker compose, nothing remote about it.
#
#   DEMO_HOST=:80 DEMO_PASSWORD='pick-one' utils/stopgap_up.sh          # plain HTTP
#   DEMO_HOST=econ.example.com DEMO_PASSWORD='pick-one' utils/stopgap_up.sh   # auto TLS
#
# DEMO_HOST is Caddy's site address as well as its certificate name:
#   ":80"              serve HTTP only, no certificate. Basic-auth credentials cross the
#                      network base64-encoded and readable -- acceptable behind a VPN or for
#                      a quick look, not for anything you would mind a stranger seeing.
#   "econ.example.com" automatic Let's Encrypt. The A record must already resolve to this
#                      host or the ACME challenge fails.
#
# Environment:
#   DEMO_HOST          (required) see above
#   DEMO_PASSWORD      (required unless DEMO_PASS_HASH is set) basic-auth password
#   DEMO_PASS_HASH     a bcrypt hash, if you would rather not put the password in the env
#   DEMO_USER          basic-auth username                    (default: demo)
#   TALIUM_ORG_ID      org the ingest token is issued for      (default: talium-demo)
#   HTTP_PORT/HTTPS_PORT  published ports                      (default: 80 / 443)
#   COLLECT_INTERVAL_S collector sweep interval                (default: 30)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DEMO_USER="${DEMO_USER:-demo}"
TALIUM_ORG_ID="${TALIUM_ORG_ID:-talium-demo}"
HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${HTTPS_PORT:-443}"
COLLECT_INTERVAL_S="${COLLECT_INTERVAL_S:-30}"
ENV_FILE="$ROOT/.env"

COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker/docker-compose.stopgap.yml)

die() { echo "[stopgap] $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"
[[ -n "${DEMO_HOST:-}" ]] || die "DEMO_HOST is required (use ':80' for plain HTTP)"

if [[ -z "${DEMO_PASS_HASH:-}" ]]; then
  [[ -n "${DEMO_PASSWORD:-}" ]] || die "set DEMO_PASSWORD (or DEMO_PASS_HASH)"
  echo "[stopgap] hashing the basic-auth password ..."
  DEMO_PASS_HASH="$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$DEMO_PASSWORD")"
fi

# Generated once and then reused, so a re-run does not change the password out from under an
# already-initialised database -- POSTGRES_PASSWORD only takes effect on first init, and a
# mismatch would lock the app containers out of their own data.
if [[ -f "$ENV_FILE" ]] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
  POSTGRES_PASSWORD="$(sed -n 's/^POSTGRES_PASSWORD=//p' "$ENV_FILE" | head -1)"
  echo "[stopgap] reusing the existing database password from .env"
else
  # Hex, so it is alphanumeric by construction: this goes into a URL-style DSN, where a
  # stray '@' or '/' would be read as structure rather than as password.
  #
  # openssl rather than `tr -dc ... </dev/urandom | head -c 32`: head closes the pipe as soon
  # as it has its bytes, tr dies of SIGPIPE, and under `set -o pipefail` that takes the whole
  # script down -- silently, before it has printed anything worth reading.
  POSTGRES_PASSWORD="$(openssl rand -hex 16)"
  echo "[stopgap] generated a new database password"
fi

if [[ -f deploy/seed/econ-seed.sql.gz ]]; then
  echo "[stopgap] seed present: deploy/seed/econ-seed.sql.gz ($(du -h deploy/seed/econ-seed.sql.gz | cut -f1))"
  echo "[stopgap]   (it loads only when the database volume is created -- see the note at the end)"
else
  echo "[stopgap] no seed file; starting with an empty database"
fi

umask 077
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
DEMO_HOST=${DEMO_HOST}
DEMO_USER=${DEMO_USER}
DEMO_PASS_HASH=${DEMO_PASS_HASH//\$/\$\$}
TALIUM_ORG_ID=${TALIUM_ORG_ID}
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
COLLECT_INTERVAL_S=${COLLECT_INTERVAL_S}
EOF

echo "[stopgap] building and starting ..."
"${COMPOSE[@]}" up -d --build --wait

TOKEN="$("${COMPOSE[@]}" exec -T ingest cat /data/org_token.txt 2>/dev/null | tr -d '\r\n' || true)"

"${COMPOSE[@]}" ps

# ":80" is a site address, not a hostname -- turn it into something clickable.
if [[ "$DEMO_HOST" == :* ]]; then
  BASE="http://<this-host>:${HTTP_PORT}"
else
  BASE="https://${DEMO_HOST}"
fi

cat <<EOF

===============================================================================
 Dashboard   ${BASE}/?user=\$(whoami on the machine that produced the data)
 Uploads     ${BASE}/upload      (basic auth: ${DEMO_USER})
 OTLP        ${BASE}/otlp        (org token, NOT basic auth)
===============================================================================

The dashboard defaults to user id 'u1'. If your data was uploaded under a different
user, append ?user=<id> -- otherwise you get an empty screen that looks like a
broken deploy. To see which ids exist:

  ${COMPOSE[*]} exec postgres psql -U postgres -d econ -c 'select id from users;'

EOF

if [[ -n "$TOKEN" ]]; then
  cat <<EOF
To send OTel here, add to ~/.claude/settings.json and start a NEW Claude Code session:

{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "${BASE}/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer ${TOKEN}"
  }
}

EOF
fi

cat <<EOF
To push more session files from a laptop:

  TALIUM_UPLOAD_URL=${BASE}/upload TALIUM_UPLOAD_AUTH=${DEMO_USER}:<password> \\
    utils/upload_claude_sessions.sh ${TALIUM_ORG_ID} \$(whoami)

Note on re-seeding: the dump loads only when the database volume is first created.
To reload it you must drop the volume, which destroys everything already there:

  ${COMPOSE[*]} down -v && DEMO_HOST=${DEMO_HOST} DEMO_PASSWORD=... utils/stopgap_up.sh
===============================================================================
EOF
