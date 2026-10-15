# Deploying to AWS

There are two generations here. **§0 is current**: the team stack that reads the Go uploader's
(`talium-uploadd`, `upload.talium.ai`) S3 prefix. §1–§9 below it are the **legacy** demo built
around the Python uploader (`docker-compose.demo.yml`, `deploy_up.sh`), kept until §0 is proven.

---

## 0. Team stack (current)

One dedicated EC2 host runs collector → ingest → worker → api, plus a second API serving the
synthetic org. Uploads do **not** come here: developers run `talium-sync` against
`upload.talium.ai`, which stages to S3, and this host only reads that prefix. Plan and
rationale: [plan/2026-09-23-aws-team-deployment.md](plan/2026-09-23-aws-team-deployment.md).

```
 browser ──https──▶ caddy   :443   /otlp/* → ingest (org token)   /* → api       (econ)
                            :9443                                 /* → api_synth (econ_synth)
                    postgres (econ + econ_synth)   init · ingest · worker · collector
                                                                      │ read-only, instance role
 talium-sync ──▶ upload.talium.ai ──▶ s3://talium-bucket/upload/cc/logs/uploads/<endpoint>/
```

| File | Role |
|---|---|
| `docker/docker-compose.aws.yml` | the stack (`talium-aws`); only Caddy publishes ports |
| `docker/config/collector.aws.yaml` | S3 source, file checkpoints on the `aws_data` volume |
| `deploy/caddy/Caddyfile.aws` | two sites (`ECON_SITE`, `SYNTH_SITE`), basic auth, `/otlp/*` exempt |
| `utils/aws_up.sh` / `aws_down.sh` | bring up / stop; `--local` = laptop rehearsal |
| `utils/aws_synth_load.sh` · `utils/synth_dump.sh` | ship the synthetic org as a dump |
| `utils/aws_directory_import.sh` | roster import that links, never forks, people |
| `utils/aws_git_backfill.sh` | GitHub App backfill (PRs, reviews, issues); incremental, cron-able |

### 0.1 Infrastructure (once)

- **EC2**: Ubuntu 24.04, t4g.medium or larger (the webui build wants ~2–3 GB), 50 GiB gp3, an
  EIP. Docker Engine + compose plugin.
- **Security group**: 80, 443, 9443 from anywhere; 22 from your IP only (or SSM).
- **Instance role**, read-only on the upload prefix:
  ```json
  [{ "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::talium-bucket",
     "Condition": { "StringLike": { "s3:prefix": ["upload/cc/logs/uploads/*"] } } },
   { "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": "arn:aws:s3:::talium-bucket/upload/cc/logs/uploads/*" }]
  ```
- **IMDSv2 hop limit 2** — at 1 the role works on the host but not inside containers:
  `aws ec2 modify-instance-metadata-options --instance-id <id> --http-put-response-hop-limit 2 --http-tokens required`
- **DNS**: one A record (e.g. `econ.talium.ai`) → the EIP, resolving *before* the first start.
- **Repo access**: a read-only GitHub deploy key on the host.

### 0.2 Bring-up

```bash
git clone <repo> /opt/talium && cd /opt/talium && git checkout <release branch or tag>
ECON_HOST=econ.talium.ai DEMO_PASSWORD='…' utils/aws_up.sh
```

`aws_up.sh` writes `.env.aws` (gitignored; the DB password is generated once and kept), builds,
**checks from inside a container that the bucket is listable** — this is where a missing role
or hop limit 1 shows up, with the fix printed — then starts everything and prints the URLs.
Every setting is remembered, so a redeploy is `git pull && utils/aws_up.sh`.

The collector's first sweep takes a few minutes. Then:

```bash
# synthetic org: dump on the laptop, load on the host
utils/synth_dump.sh                                        # laptop -> deploy/seed/econ_synth.sql.gz
scp deploy/seed/econ_synth.sql.gz host:/opt/talium/deploy/seed/
utils/aws_synth_load.sh                                    # host; --replace to reload later

# team directory -- AFTER the sweep, so the people already exist
scp -r test-roster/csv_roster host:/opt/talium/roster       # real names: never committed
utils/aws_directory_import.sh --roster roster --link anil=anilk-talium --link sumandra=sum-talium --apply
ECON_VIEWER_ID=anil utils/aws_up.sh                        # bake the viewer into the real UI

# GitHub history -- AFTER the directory import (see below)
scp app.private-key.pem host:/opt/talium/secrets/github-app.pem
# .env.github on the host (gitignored): TALIUM_GITHUB_APP_ID, _CLIENT_ID, _WEBHOOK_SECRET,
# _STATE_SECRET, and TALIUM_GITHUB_PRIVATE_KEY_PATH=secrets/github-app.pem
utils/aws_git_backfill.sh                                  # every repo the App installation covers
```

**Order: sweep → directory import → GitHub backfill.** A PR author becomes a person through the
same GitHub-login link; a login nobody carries is auto-created as `github:<login>`. Backfill
before linking and every PR is credited to a new `github:anilk-talium` while the spend stays on
`anil`. `aws_git_backfill.sh` refuses while any session user is unlinked. After the backfill the
worker links PRs to sessions by commit sha and classifies them on its own — no refold.

**Why `--link`.** The directory merges a roster row into an existing person only through the
GitHub login. Session users are named by `talium-sync`'s user id (`anil`), and carry a GitHub
login only if the client declared one (`github_user` in `~/.talium/sync.yaml`, or
`TALIUM_GITHUB_LOGIN`). Current clients don't, so an unlinked import creates a second person
(`github:anilk-talium`) who owns the team membership while `anil` keeps all the spend — team
screens then show zero cost. The script refuses that shape unless `--force`d.

### 0.3 Verify

```bash
curl -sI https://econ.talium.ai/healthz                   # 401
curl -s -u demo:pw https://econ.talium.ai/healthz         # {"status":"ok"}
curl -s -u demo:pw https://econ.talium.ai:9443/healthz    # {"status":"ok"}  (synthetic)
docker compose --env-file .env.aws -f docker/docker-compose.aws.yml exec postgres \
  psql -U postgres -d econ -c "select user_id, count(*) from sessions group by 1"
```

The browser asks for the password once per port (credentials are cached per origin).

### 0.4 Laptop rehearsal

`utils/aws_up.sh --local` runs the same compose file under project `talium-aws-local`, reading
the **real** bucket with your `~/.aws` credentials (`docker-compose.aws.local.yml`), on
`http://localhost:8088` (real) and `:8089` (synthetic). Every other script takes `--local` too.
It shares nothing with `talium-stack`. `utils/aws_down.sh --local --wipe` removes it entirely.

### 0.5 Operating it

- **After pulling a Caddyfile change:** the Caddyfile is a single-file bind mount
  (`deploy/caddy/Caddyfile.aws:/etc/caddy/Caddyfile:ro`). `git pull` replaces the file with a new
  inode, but a running container keeps its old file handle, and `aws_up.sh`'s `up -d --wait` does
  not recreate an unchanged Caddy container. Restart it explicitly:
  `docker compose --env-file .env.aws -f docker/docker-compose.aws.yml restart caddy`
- Logs: `docker compose --env-file .env.aws -f docker/docker-compose.aws.yml logs -f collector`
- `aws_down.sh` keeps volumes; `--wipe` drops **both databases**, the certificate and the org
  token. `econ` rebuilds by re-sweeping S3, `econ_synth` from its dump.
- Backups: snapshot the root EBS volume (DLM daily); `aws_pgdata` is the one volume that matters.
- **Everyone behind the password sees the real dashboard as `ECON_VIEWER_ID`** (a manager) and the
  synthetic one as `github:vp-eng` — the viewer is baked into each UI bundle at build time.
- **GitHub is pull-only.** The webhook route is not exposed, so new PRs arrive only when the
  backfill runs. It is incremental (per-repo cursor); a host cron keeps it current:
  `*/15 * * * * cd /opt/talium && utils/aws_git_backfill.sh >> /var/log/talium-git.log 2>&1`
- **The App sees only the repos it is installed on** — today just `talium-ai/code-econ`.
  Sessions in other repos (e.g. sumandra's `talium-ai/Docs`) get no PR linkage until those
  repos are added to the installation in GitHub's settings.
- **Repo attribution needs current clients.** A `talium-sync` older than `../upload` `dae4f26`
  resolves few repos: its slug-to-cwd decoding breaks on any path with `-` or `.`, such as
  `code-econ`. Those sessions fall back to `local:<cwd basename>`. Upgrading the client rewrites
  the manifest with `repo` filled in, but **the collector does not pick that up for sessions
  already collected**. Its checkpoint is the JSONL's size + head hash, and a metadata-only change
  leaves both the same. Until that is fixed, re-attribute by rebuilding from S3 after every
  client has re-synced: `utils/aws_down.sh --wipe && utils/aws_up.sh`, then the synth load,
  directory import and GitHub backfill again.
  TODO(collector-meta-checkpoint): fold the manifest entry's repo/repo_url/branch into the source
  identity so a metadata-only change re-ships the session.
- **Correlation only considers PRs into the default branch** (`config/pr_class.yaml` `eval_scope`).
  PRs merged into another branch (sumandra's go to `integ`) never link to sessions until that
  branch is added under `per_repo`.

---

# Legacy: the Python-uploader demo

The demo stack: the whole pipeline on AWS, reachable over TLS, with data that persists so
sessions can be looked at days later. It is a demo, not production — one instance per tier, no
redundancy, a shared basic-auth password.

For the laptop equivalent, see [local-stack.md](local-stack.md). The two are the same
containers; this one has a real front door, a real database host, and S3 for staging.

Infrastructure lives in the **talium-infra** repo. This document is the app layer, and the
crossing points between the two.

---

## 1. What gets deployed

Two EC2 instances in `us-west-2a`, both Graviton/arm64, both without SSH ingress.

| | `talium-dev-app` | `talium-dev-postgres` |
|---|---|---|
| Terraform | `live/dev/app` | `live/dev/postgres` |
| Runs | caddy, init, ingest, worker, api, uploader, collector | PostgreSQL 17 on the host |
| Storage | 50 GiB root, disposable | 20 GiB root + **100 GiB EBS at `/dev/sdf`**, daily DLM snapshots, 7 kept |
| Network | EIP; 80/443 open to the internet | no public ingress; 5432 only from the app SG |

The split is the whole persistence story. The app instance can be rebuilt or replaced at any
time; the sessions live on the other one, on a volume that is snapshotted nightly and is not
destroyed with the instance.

```
laptop                              talium-dev-app
  Claude Code ──OTLP/https──┐   ┌─────────────────────────────────────────┐
                            ├──▶│ caddy :443   TLS + basic auth           │
  upload_claude_sessions.sh │   │   /otlp/*   → ingest    (org token)     │
                     ───────┘   │   /upload/* → uploader  (basic auth)    │
  browser ────────https────────▶│   /, /v1/*  → api       (basic auth)    │
                                └──────┬────────────────────┬─────────────┘
                                  5432 │                 s3 │
                                       ▼                    ▼
                             talium-dev-postgres    s3://talium-bucket/uploads/
```

## 1b. The stop-gap (no AWS permissions needed)

If the IAM permissions above are not sorted yet, `docker/docker-compose.stopgap.yml` puts the
same UI, services, and database on **one host** with basic auth in front and the database
pre-seeded from a dump. No S3, no Parameter Store, no instance profile — so nothing in it
waits on IAM. Postgres is a container with a volume, and the uploader stages to a local
directory exactly as it does on a laptop.

On the machine running your local stack, capture the data:

```bash
utils/stopgap_seed.sh
```

That writes `deploy/seed/econ-seed.sql.gz` (gitignored — it is real session data). Copy the
repo, including that file, to the target host. Then on the host:

```bash
DEMO_HOST=:80 DEMO_PASSWORD='pick-one' utils/stopgap_up.sh
```

`DEMO_HOST` doubles as Caddy's site address. `:80` serves plain HTTP with no certificate —
fine behind a VPN or for a quick look, but basic-auth credentials cross the network merely
base64-encoded, so use a real hostname (`DEMO_HOST=econ.example.com`, A record already
resolving) to get automatic TLS. `HTTP_PORT`/`HTTPS_PORT` change the published ports.

Two things that catch people out:

- **The seed loads only when the database volume is first created.** The postgres image runs
  `/docker-entrypoint-initdb.d/*` once. Re-running `stopgap_up.sh` will not reload it; that
  needs `down -v`, which destroys whatever is already there.
- **The dashboard defaults to user id `u1`.** Your seeded data is under whatever `user_id` it
  was uploaded as, so open `/?user=anil` (or whichever id) — otherwise you get an empty
  screen that looks like a broken deploy. `select user_id from users;` lists them.

Teardown is `docker compose --env-file .env -f docker/docker-compose.stopgap.yml down`
(add `-v` to drop the data too).

## 2. Prerequisites

On your machine: `terraform`, `aws`, `docker`, `tar`, `ssh`, and the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html)
(`brew install --cask session-manager-plugin`). `utils/deploy_up.sh` checks for all of them
before it does anything.

In AWS: credentials for account `883291274830` in `us-west-2`, with permission to create IAM
roles and instance profiles. **Check this first** — see §8.

A DNS name you control, for the TLS certificate. You will point an A record at the EIP in §4.

## 3. Apply the infrastructure

From the `talium-infra` repo. Both roots use a partial S3 backend, so `init` needs its
`backend.hcl`:

```bash
cd terraform/live/dev/postgres && terraform init -backend-config=backend.hcl && terraform apply
```

That creates the database instance, attaches the existing 100 GiB volume, and writes two SSM
SecureString parameters: `/talium/dev/econ/db_password` and `/talium/dev/app/db_dsn`. First
boot installs PostgreSQL 17, mounts the volume at `/var/lib/postgresql`, and creates the `econ`
role and database. Give it a couple of minutes, then confirm:

```bash
aws ssm start-session --target "$(terraform output -raw instance_id)"
# on the instance:
ls /var/lib/postgresql/.talium-postgres-ready && sudo -u postgres psql -l | grep econ
```

Then the app instance:

```bash
cd ../app && terraform init -backend-config=backend.hcl && terraform apply
terraform output public_ip     # the EIP
```

## 4. Point DNS at it

Create an A record for your chosen name at the EIP from the previous step, and wait for it to
resolve. **Do this before deploying** — Caddy's ACME http-01 challenge needs the name to
already resolve to the instance, and failed attempts consume Let's Encrypt rate limit.

```bash
dig +short econ.example.com     # must return the EIP
```

## 5. Deploy the app layer

From this repo:

```bash
DEMO_HOST=econ.example.com DEMO_PASSWORD='choose-something' utils/deploy_up.sh
```

It streams this working tree to `/opt/talium` on the instance through an SSM tunnel (port 22
stays closed to the internet), then runs `docker/remote-up.sh` there, which reads the database
DSN from Parameter Store, writes `.env`, builds, and brings the stack up.

On success it prints the URLs, the ingest org token, and a ready-to-paste settings block.

Useful variables: `DEMO_USER` (default `demo`), `TALIUM_ORG_ID` (default `talium-demo`),
`INSTANCE_ID` to skip the terraform lookup, `DEMO_PASS_HASH` if you would rather not put the
password in your environment.

Re-run the same command to redeploy after a code change. It is idempotent: the schema init
reports "exists", and the org token is restored from Parameter Store rather than re-minted, so
nobody has to re-paste their settings.

## 6. Point your laptop at it

**OTel.** Paste the block `deploy_up.sh` printed into the `env` section of
`~/.claude/settings.json`, then **start a new Claude Code session** — the exporter reads this
once at process start, so an existing session keeps sending wherever it was already sending.

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "https://econ.example.com/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer tk_org_..."
  }
}
```

There is one OTLP endpoint per Claude Code process, so this replaces the local stack rather
than adding to it. Metrics flush about every 60 s and the worker's true-up timer runs every
60 s, so expect data 1–2 minutes behind.

**JSONL.** Whenever you want the session files up there:

```bash
TALIUM_UPLOAD_URL=https://econ.example.com/upload \
TALIUM_UPLOAD_AUTH=demo:your-password \
  utils/upload_claude_sessions.sh talium-demo "$(whoami)"
```

Re-running is safe and cheap. The collector skips any file whose size and leading 8 KiB are
unchanged, and the fold only extends an append-only prefix, so re-sending an unchanged session
appends nothing. What it is *not* is free in S3: every push re-stages the whole tree under a
fresh `uploads/<upload_id>/` prefix. A lifecycle rule expires those after 14 days.

## 7. Operating it

Everything goes through Session Manager; there is no SSH from the internet.

```bash
aws ssm start-session --target "$(terraform -chdir=../talium-infra/terraform/live/dev/app output -raw instance_id)"

cd /opt/talium
docker compose --env-file .env -f docker/docker-compose.demo.yml ps
docker compose --env-file .env -f docker/docker-compose.demo.yml logs -f caddy collector
```

The database, from the app instance (it is the only host allowed to connect):

```bash
docker compose --env-file .env -f docker/docker-compose.demo.yml exec api \
  python -c "import os;print(os.environ['TALIUM_DB_DSN'])"
```

Stopping:

```bash
utils/deploy_down.sh            # containers down, volumes kept
utils/deploy_down.sh --wipe     # also drops volumes: re-issues the TLS cert, re-sweeps S3
```

Neither touches the database instance. The org token survives `--wipe`, because it is kept in
Parameter Store and restored on the next deploy.

## 8. Verification

```bash
# routing and basic auth
curl -sI  https://econ.example.com/healthz              # 401
curl -s -u demo:pw https://econ.example.com/healthz     # {"status":"ok"}
curl -s -X POST https://econ.example.com/otlp/v1/metrics # 401 -- org token, not basic auth

# JSONL end to end
TALIUM_UPLOAD_URL=https://econ.example.com/upload TALIUM_UPLOAD_AUTH=demo:pw \
  utils/upload_claude_sessions.sh talium-demo "$(whoami)"   # 201 + upload_id
aws s3 ls s3://talium-bucket/uploads/ | head

# then, on the instance, after a sweep or two
docker compose ... logs collector | tail
docker compose ... exec api python -c "
from server.storage.engine import get_engine, session_scope
from server.storage.config import StorageConfig
import sqlalchemy as sa
get_engine(StorageConfig.load('config/storage.yaml'))
with session_scope() as s: print(s.execute(sa.text('select count(*) from sessions')).scalar())"
```

Then open `https://econ.example.com/` in a browser and confirm the overview, sessions, and
insights screens render.

## 9. Things worth knowing

- **The dashboard and uploader have no application-level auth.** Both have documented no-op
  auth seams (`src/server/api/app.py`, `src/server/uploader/app.py`). Caddy's basic auth is
  the only thing in front of them, and the password is shared with no audit trail. Fine for a
  demo; not a substitute for filling those seams.
- **`/talium/ingest/v1/*` is deliberately unrouted.** msging registration is `mode: open`, so
  exposing it would hand a producer key to anyone who asked. The collector reaches ingest over
  the compose network. Do not add a route for it.
- **`DEMO_PASS_HASH` is stored in `.env` with every `$` doubled.** Compose interpolates `$VAR`
  inside env files, and an un-escaped bcrypt hash silently loses everything after its cost
  factor — producing a hash no password matches, which looks exactly like a wrong password.
  `deploy_up.sh` handles this; hand-edits must too.
- **The app instance's `/data` volume is disposable.** It holds the org token (mirrored to
  Parameter Store), the collector checkpoints (losing them costs a re-sweep, not data), and
  the msging producer store.
- **IAM is the likely first blocker.** An earlier `live/dev/postgres` apply left only the EBS
  volume and security group behind, which is the signature of a failure at `aws_iam_role`.
  If `terraform apply` stops on `CreateRole`, `CreateInstanceProfile`, or `PassRole`, that
  permission has to be granted before any of this can proceed.
- **Cost**, roughly: two `t4g.medium` (~$24/mo each), 170 GiB of gp3, one EIP, and snapshots.
  Stop both instances when the demo is idle; the EBS volume and its snapshots keep the data.
