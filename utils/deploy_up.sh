#!/usr/bin/env bash
# TODO(retire-python-uploader-demo): superseded by the team stack (docker-compose.aws.yml,
# utils/aws_up.sh; docs/deployment.md §0). Remove this file, utils/deploy_up.sh,
# utils/deploy_down.sh, docker/remote-up.sh and docker/config/{collector,uploader}.demo.yaml
# once the aws stack has run for the team.
#
# Deploy this working tree to the AWS demo instance and bring the stack up.
#
#   DEMO_HOST=econ.example.com DEMO_PASSWORD=... utils/deploy_up.sh
#
# Infrastructure comes from talium-infra (terraform/live/dev/{postgres,app}); this script
# only does the app layer. It ships the tree to the instance and runs docker/remote-up.sh
# there -- see that file for the parts that need the instance profile.
#
# There is no SSH ingress on the instance and port 22 is closed to the internet. The transfer
# tunnels through SSM Session Manager, which the agent dials outbound, so this works from any
# network without touching a security group.
#
# Environment:
#   DEMO_HOST              (required) the DNS name whose A record points at the instance EIP
#   DEMO_PASSWORD          (required unless DEMO_PASS_HASH is set) basic-auth password
#   DEMO_PASS_HASH         bcrypt hash, if you would rather not put the password in the env
#   DEMO_USER              basic-auth username                      (default: demo)
#   INSTANCE_ID            skips the terraform output lookup
#   TALIUM_ORG_ID          org the ingest token is issued for       (default: talium-demo)
#   TALIUM_UPLOAD_BUCKET   JSONL staging bucket                     (default: talium-bucket)
#   AWS_REGION                                                      (default: us-west-2)
#   COLLECT_INTERVAL_S     collector sweep interval                 (default: 30)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

AWS_REGION="${AWS_REGION:-us-west-2}"
SSM_PREFIX="${SSM_PREFIX:-/talium/dev/app}"
REMOTE_DIR="${REMOTE_DIR:-/opt/talium}"
REMOTE_USER="${REMOTE_USER:-ubuntu}"
DEMO_USER="${DEMO_USER:-demo}"
TALIUM_ORG_ID="${TALIUM_ORG_ID:-talium-demo}"
TALIUM_UPLOAD_BUCKET="${TALIUM_UPLOAD_BUCKET:-talium-bucket}"
COLLECT_INTERVAL_S="${COLLECT_INTERVAL_S:-30}"
INFRA_DIR="${TALIUM_INFRA_DIR:-$ROOT/../talium-infra}"

die() { echo "[deploy] $*" >&2; exit 1; }

# --- preflight ----------------------------------------------------------------------------
for tool in aws ssh tar docker session-manager-plugin; do
  command -v "$tool" >/dev/null || die "missing required tool: $tool"
done

[[ -n "${DEMO_HOST:-}" ]] || die "DEMO_HOST is required (the name the TLS certificate is for)"

if [[ -z "${DEMO_PASS_HASH:-}" ]]; then
  [[ -n "${DEMO_PASSWORD:-}" ]] || die "set DEMO_PASSWORD (or DEMO_PASS_HASH) -- the dashboard and uploader are behind basic auth"
  echo "[deploy] hashing the basic-auth password ..."
  DEMO_PASS_HASH="$(docker run --rm caddy:2-alpine caddy hash-password --plaintext "$DEMO_PASSWORD")"
fi

if [[ -z "${INSTANCE_ID:-}" ]]; then
  [[ -d "$INFRA_DIR/terraform/live/dev/app" ]] \
    || die "cannot find talium-infra at $INFRA_DIR -- set INSTANCE_ID or TALIUM_INFRA_DIR"
  echo "[deploy] resolving the instance id from terraform ..."
  INSTANCE_ID="$(terraform -chdir="$INFRA_DIR/terraform/live/dev/app" output -raw instance_id)" \
    || die "terraform output failed -- has live/dev/app been applied?"
fi

echo "[deploy] target ${INSTANCE_ID} (${DEMO_HOST}) in ${AWS_REGION}"

# The tunnel. %h is the instance id, which is what Session Manager targets -- no hostname or
# IP is involved, and nothing listens on 22 from the internet.
#
# It lives in a wrapper script rather than an inline option so the ProxyCommand -- which is
# full of spaces -- survives one definition reused by every ssh invocation below.
SSH_WRAP="$(mktemp)"
TMPFILES=("$SSH_WRAP")
trap 'rm -f "${TMPFILES[@]}"' EXIT

cat > "$SSH_WRAP" <<EOF
#!/usr/bin/env bash
exec ssh \\
  -o "ProxyCommand=aws ssm start-session --target %h --document-name AWS-StartSSHSession --parameters portNumber=%p --region ${AWS_REGION}" \\
  -o StrictHostKeyChecking=accept-new \\
  -o ServerAliveInterval=30 \\
  "\$@"
EOF
chmod +x "$SSH_WRAP"

ssh_remote() { "$SSH_WRAP" "${REMOTE_USER}@${INSTANCE_ID}" "$@"; }

# --- push ---------------------------------------------------------------------------------
echo "[deploy] preparing ${REMOTE_DIR} ..."
ssh_remote "sudo mkdir -p '$REMOTE_DIR' && sudo chown ${REMOTE_USER}:${REMOTE_USER} '$REMOTE_DIR'"

echo "[deploy] syncing the tree ..."
# tar over the tunnel rather than rsync, for two reasons: macOS now ships openrsync, whose
# interop with the far side is not something a deploy should rest on, and the instance image
# has no rsync at all (cloud-init installs curl, gnupg, git and unzip only). The payload is a
# few MB, so incremental transfer buys nothing worth that risk.
#
# Only what the stack actually needs is sent: the Dockerfile copies requirements.txt, src/ and
# config/, and compose bind-mounts things from docker/ and deploy/.
#
# The code directories are removed first so that deleting a file locally deletes it remotely
# too -- the property --delete was providing. .env and .env.base sit at the root of
# REMOTE_DIR, outside all of them, and survive untouched.
COPYFILE_DISABLE=1 tar czf - -C "$ROOT" \
    --exclude '__pycache__' \
    --exclude '.pytest_cache' \
    --exclude '.DS_Store' \
    --exclude '*.pyc' \
    requirements.txt src config docker deploy \
  | ssh_remote "set -e
      cd '$REMOTE_DIR'
      rm -rf src config docker deploy
      tar xzf - -C '$REMOTE_DIR'" \
  || die "transferring the tree failed"

# Everything the far side needs that is not a secret it can fetch itself. The DSN is
# deliberately absent -- remote-up.sh reads that from Parameter Store using the instance
# profile, so it never travels over this connection or sits in a local file.
#
# DEMO_PASS_HASH goes over with every `$` DOUBLED. Compose interpolates `$VAR` inside env
# files, and an un-escaped bcrypt hash loses everything after its cost factor -- producing a
# hash that no password matches, which looks exactly like a wrong password.
echo "[deploy] writing .env.base ..."
ssh_remote "umask 077 && cat > '${REMOTE_DIR}/.env.base'" <<EOF
AWS_REGION=${AWS_REGION}
SSM_PREFIX=${SSM_PREFIX}
TALIUM_ORG_ID=${TALIUM_ORG_ID}
TALIUM_UPLOAD_BUCKET=${TALIUM_UPLOAD_BUCKET}
COLLECT_INTERVAL_S=${COLLECT_INTERVAL_S}
DEMO_HOST=${DEMO_HOST}
DEMO_USER=${DEMO_USER}
DEMO_PASS_HASH=${DEMO_PASS_HASH//\$/\$\$}
EOF

# --- bring it up --------------------------------------------------------------------------
OUT="$(mktemp)"
TMPFILES+=("$OUT")
ssh_remote "bash '${REMOTE_DIR}/docker/remote-up.sh'" 2>&1 | tee "$OUT"

TOKEN="$(sed -n 's/^ORG_TOKEN=//p' "$OUT" | tail -1)"
[[ -n "$TOKEN" ]] || die "the stack came up but no org token was reported -- check 'docker compose logs init' on the instance"

cat <<EOF

===============================================================================
 Dashboard   https://${DEMO_HOST}/          (basic auth: ${DEMO_USER})
 OTLP        https://${DEMO_HOST}/otlp      (org token, not basic auth)
 Uploads     https://${DEMO_HOST}/upload    (basic auth)
===============================================================================

Add to ~/.claude/settings.json, then start a NEW Claude Code session -- the exporter
reads this once at process start:

{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://${DEMO_HOST}/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer ${TOKEN}"
  }
}

Push your session files whenever you want them in the demo:

  TALIUM_UPLOAD_URL=https://${DEMO_HOST}/upload \\
  TALIUM_UPLOAD_AUTH=${DEMO_USER}:<password> \\
    utils/upload_claude_sessions.sh ${TALIUM_ORG_ID} \$(whoami)

===============================================================================
EOF
