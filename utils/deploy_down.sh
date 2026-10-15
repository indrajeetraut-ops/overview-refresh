#!/usr/bin/env bash
# Stop the AWS demo stack. The counterpart to utils/deploy_up.sh.
#
#   utils/deploy_down.sh            stop the containers; volumes kept
#   utils/deploy_down.sh --wipe     also drop the volumes
#
# What --wipe does NOT touch is the data you care about: the database lives on a separate
# instance (talium-dev-postgres) with its own EBS volume, so stories survive either form of
# this. What it does drop is the TLS certificate and the collector checkpoints -- the next
# start re-enrols with Let's Encrypt and re-sweeps the staging bucket from scratch.
#
# The org token survives even a --wipe, because it is kept in Parameter Store at
# ${SSM_PREFIX}/org_token and restored on the next deploy. Nobody has to re-paste settings.
#
# To decommission the demo entirely, destroy the terraform roots in talium-infra instead.
set -euo pipefail

AWS_REGION="${AWS_REGION:-us-west-2}"
REMOTE_DIR="${REMOTE_DIR:-/opt/talium}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INFRA_DIR="${TALIUM_INFRA_DIR:-$ROOT/../talium-infra}"

die() { echo "[deploy] $*" >&2; exit 1; }

for tool in aws ssh session-manager-plugin; do
  command -v "$tool" >/dev/null || die "missing required tool: $tool"
done

if [[ -z "${INSTANCE_ID:-}" ]]; then
  [[ -d "$INFRA_DIR/terraform/live/dev/app" ]] \
    || die "cannot find talium-infra at $INFRA_DIR -- set INSTANCE_ID or TALIUM_INFRA_DIR"
  INSTANCE_ID="$(terraform -chdir="$INFRA_DIR/terraform/live/dev/app" output -raw instance_id)" \
    || die "terraform output failed -- has live/dev/app been applied?"
fi

DOWN_ARGS=""
if [[ "${1:-}" == "--wipe" ]]; then
  DOWN_ARGS="-v"
fi

ssh -o "ProxyCommand=aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p --region $AWS_REGION" \
    -o StrictHostKeyChecking=accept-new \
    "${REMOTE_USER}@${INSTANCE_ID}" \
    "cd '$REMOTE_DIR' && docker compose --env-file .env -f docker/docker-compose.demo.yml down $DOWN_ARGS"

if [[ -n "$DOWN_ARGS" ]]; then
  echo "[deploy] down, volumes removed -- the next deploy re-issues the TLS cert and re-sweeps the bucket"
else
  echo "[deploy] down; volumes kept (utils/deploy_up.sh brings it back as it was)"
fi
