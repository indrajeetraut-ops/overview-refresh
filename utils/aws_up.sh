#!/usr/bin/env bash
# Bring up the AWS team stack (docker/docker-compose.aws.yml). Run this ON the EC2 host -- it is
# plain docker compose, nothing remote about it. Plan: docs/plan/2026-09-23-aws-team-deployment.md.
#
#   ECON_HOST=econ.talium.ai DEMO_PASSWORD='...' utils/aws_up.sh    # first run
#   utils/aws_up.sh                                                  # redeploy (after git pull)
#   DEMO_PASSWORD='...' utils/aws_up.sh --local                     # laptop rehearsal
#
# Real data:  https://$ECON_HOST/            (basic auth)
# Synthetic:  https://$ECON_HOST:$SYNTH_PORT/ (basic auth) -- a different port is a different
#             origin, which is what the SPA's origin-relative /v1 calls need.
#
# Every setting is remembered in the env file (.env.aws, or .env.aws-local for --local), so a
# redeploy needs no arguments; anything set in the environment overrides what is stored.
#
# Environment:
#   ECON_HOST          (required on first run) hostname whose A record points here. A value
#                      starting with http:// serves plain HTTP with no certificate.
#   DEMO_PASSWORD      (required on first run unless DEMO_PASS_HASH) basic-auth password
#   DEMO_PASS_HASH     bcrypt hash instead of the password
#   DEMO_USER          basic-auth username                         (default: demo)
#   SYNTH_PORT         synthetic dashboard port                    (default: 9443)
#   ECON_VIEWER_ID     directory id baked into the real UI (after the directory import)
#   SYNTH_VIEWER_ID    directory id baked into the synthetic UI    (default: github:vp-eng)
#   TALIUM_ORG_ID      must equal talium-uploadd's org             (default: talium)
#   TALIUM_S3_BUCKET   talium-uploadd's bucket                     (default: talium-bucket)
#   TALIUM_S3_KEY_BASE talium-uploadd's TALIUM_S3_PREFIX + "/"     (default: upload/cc/logs/)
#   AWS_REGION                                                     (default: us-west-2)
#   AWS_PROFILE        --local only: the ~/.aws profile the collector reads S3 with
#   COLLECT_INTERVAL_S collector sweep interval                    (default: 30)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

die() { echo "[aws] $*" >&2; exit 1; }

LOCAL=0
[[ "${1:-}" == "--local" ]] && LOCAL=1

if (( LOCAL )); then
  ENV_FILE="$ROOT/.env.aws-local"
  COMPOSE=(docker compose -p talium-aws-local --env-file "$ENV_FILE"
           -f docker/docker-compose.aws.yml -f docker/docker-compose.aws.local.yml)
else
  ENV_FILE="$ROOT/.env.aws"
  COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker/docker-compose.aws.yml)
fi

# The value stored in the env file by a previous run, verbatim (so a doubled-`$` hash stays
# doubled). Read, never `source`d: bash would expand `$$` to its own PID.
prev() { [[ -f "$ENV_FILE" ]] && sed -n "s/^$1=//p" "$ENV_FILE" | head -1 || true; }

command -v docker >/dev/null || die "docker is not installed"
docker compose version >/dev/null 2>&1 || die "the docker compose plugin is not installed"

# --- settings: environment > previous run > default ----------------------------------------
DEMO_USER="${DEMO_USER:-$(prev DEMO_USER)}";                DEMO_USER="${DEMO_USER:-demo}"
TALIUM_ORG_ID="${TALIUM_ORG_ID:-$(prev TALIUM_ORG_ID)}";    TALIUM_ORG_ID="${TALIUM_ORG_ID:-talium}"
TALIUM_S3_BUCKET="${TALIUM_S3_BUCKET:-$(prev TALIUM_S3_BUCKET)}"; TALIUM_S3_BUCKET="${TALIUM_S3_BUCKET:-talium-bucket}"
TALIUM_S3_KEY_BASE="${TALIUM_S3_KEY_BASE:-$(prev TALIUM_S3_KEY_BASE)}"; TALIUM_S3_KEY_BASE="${TALIUM_S3_KEY_BASE:-upload/cc/logs/}"
AWS_REGION="${AWS_REGION:-$(prev AWS_REGION)}";             AWS_REGION="${AWS_REGION:-us-west-2}"
COLLECT_INTERVAL_S="${COLLECT_INTERVAL_S:-$(prev COLLECT_INTERVAL_S)}"; COLLECT_INTERVAL_S="${COLLECT_INTERVAL_S:-30}"
ECON_VIEWER_ID="${ECON_VIEWER_ID:-$(prev ECON_VIEWER_ID)}"
SYNTH_VIEWER_ID="${SYNTH_VIEWER_ID:-$(prev SYNTH_VIEWER_ID)}"; SYNTH_VIEWER_ID="${SYNTH_VIEWER_ID:-github:vp-eng}"

# A key base is a prefix with its trailing slash, or empty for the bucket root.
[[ -z "$TALIUM_S3_KEY_BASE" || "$TALIUM_S3_KEY_BASE" == */ ]] \
  || die "TALIUM_S3_KEY_BASE must end with '/' (got '$TALIUM_S3_KEY_BASE')"

if (( LOCAL )); then
  # Plain HTTP on localhost; Caddy listens on 80 / 9443 inside, published on 8088 / 8089.
  ECON_SITE="http://localhost";  SYNTH_PORT=9443
  SYNTH_SITE="http://localhost:${SYNTH_PORT}"
  HTTP_PORT=8088; HTTPS_PORT=8448; SYNTH_PUBLISH_PORT=8089
  AWS_PROFILE="${AWS_PROFILE:-$(prev AWS_PROFILE)}"; AWS_PROFILE="${AWS_PROFILE:-default}"
  [[ -d "$HOME/.aws" ]] || die "--local reads S3 with your ~/.aws credentials, and there is no ~/.aws"
  ECON_URL="http://localhost:${HTTP_PORT}"; SYNTH_URL="http://localhost:${SYNTH_PUBLISH_PORT}"
else
  ECON_HOST="${ECON_HOST:-$(prev ECON_HOST)}"
  [[ -n "$ECON_HOST" ]] || die "ECON_HOST is required on the first run (e.g. econ.talium.ai)"
  SYNTH_PORT="${SYNTH_PORT:-$(prev SYNTH_PORT)}"; SYNTH_PORT="${SYNTH_PORT:-9443}"
  ECON_SITE="$ECON_HOST"; SYNTH_SITE="${ECON_HOST}:${SYNTH_PORT}"
  HTTP_PORT=80; HTTPS_PORT=443; SYNTH_PUBLISH_PORT="$SYNTH_PORT"
  if [[ "$ECON_HOST" == http://* ]]; then
    ECON_URL="$ECON_HOST"; SYNTH_URL="$SYNTH_SITE"
  else
    ECON_URL="https://$ECON_HOST"; SYNTH_URL="https://$SYNTH_SITE"
  fi
fi

# The password hash. A new password wins; otherwise keep the stored (already-doubled) hash.
if [[ -n "${DEMO_PASS_HASH:-}" ]]; then
  PASS_HASH_LINE="${DEMO_PASS_HASH//\$/\$\$}"
elif [[ -n "${DEMO_PASSWORD:-}" ]]; then
  echo "[aws] hashing the basic-auth password ..."
  DEMO_PASS_HASH="$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$DEMO_PASSWORD")"
  # Doubled for compose's interpolation of env files -- an un-escaped bcrypt hash loses
  # everything after its cost factor and matches no password, which looks like a typo.
  PASS_HASH_LINE="${DEMO_PASS_HASH//\$/\$\$}"
else
  PASS_HASH_LINE="$(prev DEMO_PASS_HASH)"
  [[ -n "$PASS_HASH_LINE" ]] || die "set DEMO_PASSWORD (or DEMO_PASS_HASH) on the first run"
fi

# Generated once and reused: POSTGRES_PASSWORD only takes effect when the data volume is first
# initialised, so changing it later would lock the app out of its own database. Hex, so it is
# URL-safe inside the DSN.
POSTGRES_PASSWORD="$(prev POSTGRES_PASSWORD)"
if [[ -z "$POSTGRES_PASSWORD" ]]; then
  POSTGRES_PASSWORD="$(openssl rand -hex 16)"
  echo "[aws] generated a new database password"
fi

umask 077
cat > "$ENV_FILE" <<EOF
POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
ECON_HOST=${ECON_HOST:-}
ECON_SITE=${ECON_SITE}
SYNTH_SITE=${SYNTH_SITE}
SYNTH_PORT=${SYNTH_PORT}
HTTP_PORT=${HTTP_PORT}
HTTPS_PORT=${HTTPS_PORT}
SYNTH_PUBLISH_PORT=${SYNTH_PUBLISH_PORT}
DEMO_USER=${DEMO_USER}
DEMO_PASS_HASH=${PASS_HASH_LINE}
TALIUM_ORG_ID=${TALIUM_ORG_ID}
TALIUM_S3_BUCKET=${TALIUM_S3_BUCKET}
TALIUM_S3_KEY_BASE=${TALIUM_S3_KEY_BASE}
AWS_REGION=${AWS_REGION}
AWS_PROFILE=${AWS_PROFILE:-}
COLLECT_INTERVAL_S=${COLLECT_INTERVAL_S}
ECON_VIEWER_ID=${ECON_VIEWER_ID}
SYNTH_VIEWER_ID=${SYNTH_VIEWER_ID}
EOF
echo "[aws] wrote ${ENV_FILE#"$ROOT/"}"

echo "[aws] building images ..."
"${COMPOSE[@]}" build

# --- preflight: can a CONTAINER read the bucket? ---------------------------------------------
# Run inside the collector image, not with the host's aws CLI: the classic EC2 failure is an
# instance role that works on the host but not in containers (IMDSv2 hop limit 1). Catching it
# here beats a collector that loops "pass failed" forever.
echo "[aws] checking S3 read access from inside a container: s3://${TALIUM_S3_BUCKET}/${TALIUM_S3_KEY_BASE}uploads/ ..."
if ! "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python collector -c "
import boto3, sys
r = boto3.client('s3', region_name='${AWS_REGION}').list_objects_v2(
    Bucket='${TALIUM_S3_BUCKET}', Prefix='${TALIUM_S3_KEY_BASE}uploads/', Delimiter='/')
eps = [p['Prefix'] for p in r.get('CommonPrefixes', [])]
print(f'[aws]   {len(eps)} endpoint(s): ' + ', '.join(e.rsplit('/', 2)[-2] for e in eps))
"; then
  cat >&2 <<EOF
[aws] the collector cannot list the bucket. On EC2, check in this order:
[aws]   1. the instance has a role with s3:ListBucket + s3:GetObject on ${TALIUM_S3_BUCKET}/${TALIUM_S3_KEY_BASE}uploads/*
[aws]   2. IMDSv2 hop limit is 2 -- containers cannot reach the role at 1:
[aws]        aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required
[aws]   3. AWS_REGION (${AWS_REGION}) is the bucket's region
EOF
  exit 1
fi

echo "[aws] starting the stack ..."
"${COMPOSE[@]}" up -d --wait

TOKEN="$("${COMPOSE[@]}" exec -T ingest cat /data/org_token.txt 2>/dev/null | tr -d '\r\n' || true)"
SYNTH_READY="$("${COMPOSE[@]}" exec -T postgres psql -U postgres -tAc \
  "select 1 from pg_database where datname='econ_synth'" 2>/dev/null | tr -d '\r\n' || true)"

"${COMPOSE[@]}" ps

cat <<EOF

===============================================================================
 Real data   ${ECON_URL}/          (basic auth: ${DEMO_USER})
 Synthetic   ${SYNTH_URL}/     (basic auth: ${DEMO_USER})
 OTLP        ${ECON_URL}/otlp      (org token, NOT basic auth)
 S3 source   s3://${TALIUM_S3_BUCKET}/${TALIUM_S3_KEY_BASE}uploads/  (org ${TALIUM_ORG_ID})
===============================================================================
EOF

[[ -n "$SYNTH_READY" ]] || cat <<EOF

econ_synth is not loaded yet -- the synthetic dashboard errors until it is:
  utils/synth_dump.sh                          # on the laptop, then copy the file here
  utils/aws_synth_load.sh$( (( LOCAL )) && printf ' --local')
EOF

[[ -n "$ECON_VIEWER_ID" ]] || cat <<EOF

ECON_VIEWER_ID is empty, so every manager screen on the real dashboard 401s. Once the
collector has swept everyone, import the roster (linking upload ids to GitHub logins, or the
import forks each person in two), then rebuild with your user id baked in:
  utils/aws_directory_import.sh$( (( LOCAL )) && printf ' --local') --roster <dir> --link <user>=<login> ... --apply
  ECON_VIEWER_ID=<your user id> utils/aws_up.sh$( (( LOCAL )) && printf ' --local')
EOF

if [[ -n "$TOKEN" ]]; then
  cat <<EOF

Optional -- to also send OTel here, add to ~/.claude/settings.json (env) and start a NEW
Claude Code session:
    "OTEL_EXPORTER_OTLP_ENDPOINT": "${ECON_URL}/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer ${TOKEN}"
EOF
fi
echo "==============================================================================="
