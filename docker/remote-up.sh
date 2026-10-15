#!/usr/bin/env bash
# The half of the deploy that runs ON the app instance. utils/deploy_up.sh ships this tree
# to /opt/talium and then invokes this; nothing here is meant to be run from a laptop.
#
# Its whole job is the two things that can only be done from inside the instance profile:
# reading the database DSN out of Parameter Store, and keeping the ingest org token stable
# across a rebuild of this disposable host.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose --env-file .env -f docker/docker-compose.demo.yml)
PROJECT="talium-demo"
DATA_VOLUME="${PROJECT}_demo_data"

# Written by deploy_up.sh: everything that is not a secret this host must fetch itself.
if [[ ! -f .env.base ]]; then
  echo "[remote] .env.base missing -- run utils/deploy_up.sh rather than this script" >&2
  exit 1
fi

# Read, deliberately not `source`. DEMO_PASS_HASH is stored with its `$` doubled for
# compose's interpolation, and sourcing the file would have bash expand `$$` to its own PID
# before compose ever saw it. Only these two keys are needed here, and neither contains a `$`.
env_base() { sed -n "s/^$1=//p" .env.base | head -1; }

AWS_REGION="$(env_base AWS_REGION)"
SSM_PREFIX="$(env_base SSM_PREFIX)"
: "${AWS_REGION:?missing from .env.base}" "${SSM_PREFIX:?missing from .env.base}"

aws() { command aws --region "$AWS_REGION" "$@"; }

ssm_get() { # $1 = parameter name; prints the value, or nothing if it does not exist
  aws ssm get-parameter --name "$1" --with-decryption \
      --query Parameter.Value --output text 2>/dev/null || true
}

echo "[remote] reading the database DSN from ${SSM_PREFIX}/db_dsn ..."
DB_DSN="$(ssm_get "${SSM_PREFIX}/db_dsn")"
if [[ -z "$DB_DSN" ]]; then
  echo "[remote] ${SSM_PREFIX}/db_dsn is empty or unreadable." >&2
  echo "[remote] It is written by talium-infra's live/dev/postgres root -- apply that first." >&2
  exit 1
fi

# .env is what compose reads. Assembled here, never committed, never transferred.
umask 077
cp .env.base .env
printf 'TALIUM_DB_DSN=%s\n' "$DB_DSN" >> .env

echo "[remote] building images ..."
"${COMPOSE[@]}" build

# --- org token continuity -----------------------------------------------------------------
# /data lives on this instance's root volume, which is disposable, but the token in it is
# pasted into every developer's ~/.claude/settings.json. Seeding the volume from Parameter
# Store *before* init runs makes docker/init-stack.sh take its "already issued" branch, so a
# rebuilt instance keeps the token its fleet is already configured with.
docker volume create "$DATA_VOLUME" >/dev/null

SAVED_TOKEN="$(ssm_get "${SSM_PREFIX}/org_token")"
if [[ -n "$SAVED_TOKEN" ]]; then
  echo "[remote] restoring the saved org token into ${DATA_VOLUME} ..."
  printf '%s\n' "$SAVED_TOKEN" \
    | docker run --rm -i -v "${DATA_VOLUME}:/data" alpine \
        sh -c 'cat > /data/org_token.txt'
fi

echo "[remote] starting the stack ..."
"${COMPOSE[@]}" up -d --wait

TOKEN="$(docker run --rm -v "${DATA_VOLUME}:/data" alpine cat /data/org_token.txt 2>/dev/null || true)"
if [[ -z "$TOKEN" ]]; then
  echo "[remote] no org token found after init -- check 'docker compose logs init'" >&2
  exit 1
fi

if [[ -z "$SAVED_TOKEN" ]]; then
  echo "[remote] persisting the newly minted org token to ${SSM_PREFIX}/org_token ..."
  aws ssm put-parameter --name "${SSM_PREFIX}/org_token" --type SecureString \
      --value "$TOKEN" --overwrite >/dev/null
fi

"${COMPOSE[@]}" ps
printf '\nORG_TOKEN=%s\n' "$TOKEN"
