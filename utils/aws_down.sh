#!/usr/bin/env bash
# Stop the AWS team stack. The counterpart to utils/aws_up.sh; run it where that ran.
#
#   utils/aws_down.sh                 stop the containers; volumes (both databases) kept
#   utils/aws_down.sh --wipe          ALSO DROP THE VOLUMES -- see below
#   utils/aws_down.sh --local [...]   the laptop rehearsal (project talium-aws-local)
#
# --wipe drops aws_pgdata, which holds BOTH databases. econ is rebuildable -- the next
# aws_up.sh re-sweeps S3 from scratch and re-folds everything -- and econ_synth is rebuilt with
# utils/aws_synth_load.sh from its dump. It also drops the TLS certificate (Let's Encrypt
# re-issues it, against its rate limit) and mints a NEW org token, so any OTel settings pasted
# from the old one stop working.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

LOCAL=0; WIPE=0
for arg in "$@"; do
  case "$arg" in
    --local) LOCAL=1 ;;
    --wipe)  WIPE=1 ;;
    *) echo "usage: $0 [--local] [--wipe]" >&2; exit 2 ;;
  esac
done

if (( LOCAL )); then
  ENV_FILE="$ROOT/.env.aws-local"
  COMPOSE=(docker compose -p talium-aws-local --env-file "$ENV_FILE"
           -f docker/docker-compose.aws.yml -f docker/docker-compose.aws.local.yml)
else
  ENV_FILE="$ROOT/.env.aws"
  COMPOSE=(docker compose --env-file "$ENV_FILE" -f docker/docker-compose.aws.yml)
fi
[[ -f "$ENV_FILE" ]] || { echo "[aws] ${ENV_FILE#"$ROOT/"} not found -- nothing was brought up here" >&2; exit 1; }

if (( WIPE )); then
  "${COMPOSE[@]}" down -v
  echo "[aws] down, volumes removed: both databases, the certificate and the org token are gone"
else
  "${COMPOSE[@]}" down
  echo "[aws] down; volumes kept (utils/aws_up.sh brings it back as it was)"
fi
