# AWS team deployment: collector → ingest → worker → api, plus a synthetic endpoint

**Status:** 2026-09-23, IMPLEMENTED on `demo-release-v2` and rehearsed on the laptop against the
real bucket. Pending: the EC2 infra (Step 4) and the bring-up on the host (Step 5). The runbook
is now `docs/deployment.md` §0; this file keeps the reasoning.

**Found during the rehearsal (see deployment.md §0.2 and §0.5):**
- **Identity forks on directory import.** The manifests carry `github_user: null`, so the CSV
  roster (keyed by email and github_login) matched neither `anil` nor `sumandra` and minted
  `github:anilk-talium` / `github:sum-talium` as separate people. The team membership landed on
  those while the spend stayed on the originals. Fix: `utils/aws_directory_import.sh --link
  <user>=<login>` sets `users.github_user` first, and refuses an import that would fork. The
  proper fix is client-side: set `github_user` in `talium-sync`'s config (or
  `TALIUM_GITHUB_LOGIN`).
- **Your own endpoint is on an old client too.** 92 of 92 `anil` sessions are `local:` repos.
  sumandra's are 13 of 44 `github:`. §6 applies to both clients.
- The sweep is reproducible: wipe and re-sweep gives the same 136 sessions.
- **GitHub backfill added** (`utils/aws_git_backfill.sh`; §7's "no GitHub App" limit no longer
  applies). Rehearsal: 27 PRs, 34 issues and 2 reviews from `talium-ai/code-econ`; the worker
  produced 24 sha links over 23 sessions and classified 27 PRs, and the overview shows 20 merged PRs.
  PR authors resolved to `anil`/`sumandra` only because linking ran first; the script refuses
  the other order. The App installation covers only `code-econ`.
**Goal:** a teammate can open the dashboard over HTTPS, and their `talium-sync` uploads (already
landing in S3 via `upload.talium.ai`) flow into it without anyone running anything by hand. A
second port on the same hostname serves the synthetic org (`econ_synth`) from the same host.

## 0. Decisions (confirmed 2026-09-23)

| Question | Decision |
|---|---|
| Host | **New, dedicated EC2**. It has its own Caddy on 80/443, and the upload box stays untouched. |
| Database | **Postgres container on the host** (stop-gap style: pure compose, named volume on EBS). Both `econ` and `econ_synth` live in the same container. |
| Real-data seed | **Empty.** `init` builds the schema and the collector sweeps all of S3. There is one org, `talium`, and no `local-dev` rows mixed in. |
| Synth endpoint | **Same hostname, second port**: `https://econ.talium.ai:9443`. That is one DNS record and one certificate. 9443 rather than 8443, which ingest already uses inside the compose network. |
| Upload path | **Out of scope.** `talium-uploadd` + tusd stay on their own box. The Python uploader and the `/upload/*` route are **not** deployed. |

## 1. Facts the plan turns on (verified against the live bucket)

- **The JSONL is not under `artifacts/talium-upload/`.** That prefix holds only the
  `talium-sync` binary. `talium-uploadd` runs with `TALIUM_S3_PREFIX=upload/cc/logs`, so
  manifests live at:
  ```
  s3://talium-bucket/upload/cc/logs/uploads/<endpoint_id>/manifest.json
  s3://talium-bucket/upload/cc/logs/uploads/<endpoint_id>/claude/raw/<rel>
  s3://talium-bucket/upload/cc/logs/tus-tmp/...        (scratch, ignore)
  ```
  Three endpoints exist today: `smajee-mbp-9ce81b65a4f9`, `mac-9ce81b65a4f9`,
  `macbookpro-a018233ac157`.
- **The collector needs only a sweep scope, not a prefix setting.**
  `ClaudeCodeSourceReader.group()` takes each file's prefix from the manifest's own S3 key
  (`src/collector/claude_code/source_reader.py:133`). `collect-loop.sh` already accepts
  `COLLECT_SCOPE` (uncommitted change on #61). Set it to `upload/cc/logs/uploads/`.
- **The prod org is `talium`** (`declared.org_id` in the manifests, and `auth.enrollment_secrets`
  in `../upload/deploy/uploadd.yaml`). The stack's `TALIUM_ORG_ID` must be `talium` too, so
  the ingest org token and the manifest org agree.
- **The teammate's client is stale.** `smajee-mbp` reports `uploader_version: 0.1.1`, has no
  `repo_url` on any entry, and only **16 of 94 files carry `repo`**. That is the
  pre-Part-A `CWDFromSlug` bug. The dashboard still works without it, but
  sessions degrade to `local:<cwd>` repo identity and PR linkage is thin. See §6.
- **The collector compat layer (Part B) is only on `feat/uploader-collector-integration-#61`**:
  `endpoint_id`, multi-vendor, and the prefix join. That branch is 37 commits ahead of `main`,
  plus the uncommitted `COLLECT_SCOPE` / sync-overlay work. The host must clone a ref that has
  all of it. See §3 step 1.
- The SPA calls `/v1` against `location.origin`, so real and synth **cannot share one
  origin under two paths**. A different port is a different origin, so `:443` and `:9443`
  on one hostname is enough.

## 2. Target topology

```
                         new EC2  "talium-econ"  (t4g.medium / 50 GiB gp3, EIP)
 browser ──https──┐    ┌───────────────────────────────────────────────────────────┐
                  ├───▶│ caddy  basic auth, one cert for econ.talium.ai            │
                  │    │   :443   /otlp/* → ingest (org token, no b-auth)          │
                  │    │          /*      → api        (econ)                      │
                  │    │   :9443  /*      → api_synth  (econ_synth)                │
                  │    │                                                           │
                  │    │ postgres:16 ── econ, econ_synth   (volume on EBS)         │
                  │    │ init → ingest ← collector (loop)   worker (econ only)     │
                  │    └──────────────────────────────▲────────────────────────────┘
                  │                                   │ s3:GetObject/ListBucket (instance role, read-only)
 talium-sync ──https──▶ upload.talium.ai ──▶ s3://talium-bucket/upload/cc/logs/uploads/
 (you, sumandra)        (separate box, unchanged)
```

Services: `caddy`, `postgres`, `init`, `ingest`, `worker`, `api`, `api_synth`, `collector`.
Absent on purpose: `uploader` (Python), `minio`, `tusd`, `talium-uploadd`. ingest's
`/talium/ingest/v1/*` is still never routed (msging registration is `mode: open`, see
`deploy/caddy/Caddyfile`).

## 3. Work items

### Step 1: Get a deployable ref (laptop)
- Commit the pending #61 work: `collect-loop.sh` (`COLLECT_SCOPE`), the sync overlay and
  scripts, and `stack_up/down.sh`. Decide separately whether the `config/api.yaml` threshold
  tweaks (`under: 0.30`, `long_running_duration_s: 18000`) should ship. They are product
  knobs, so they should be a separate commit.
- Push, then **either** merge #61 → `main` **or** deploy from the branch/tag. Recommendation:
  tag it (`deploy-2026-09-xx`) so the host checks out something immutable.
- The host needs read access to the private repo: a GitHub deploy key (read-only) on the
  instance.

### Step 2: New compose + config (repo)
| File | What |
|---|---|
| `docker/docker-compose.aws.yml` (new, project `talium-aws`) | Stop-gap topology (postgres container, `init`, `ingest`, `worker`, `api`) + demo's S3 collector + `api_synth`. Log rotation (`x-logging`) as in demo. No published ports except Caddy. |
| `docker/config/collector.aws.yaml` (new) | `source_store.backend: s3`, `bucket: ${TALIUM_S3_BUCKET}`, `region: ${AWS_REGION}`, `endpoint_url: null`. File checkpoints at `${TALIUM_DATA_DIR}/collector_checkpoints.json`. msging → `http://ingest:8443`. Credentials come from the instance role; no static keys. |
| `deploy/caddy/Caddyfile.aws` (new) | Two site blocks, `{$ECON_SITE}` and `{$SYNTH_SITE}`, both basic auth. `aws_up.sh` derives them from `ECON_HOST` + `SYNTH_PORT`: `econ.talium.ai` and `econ.talium.ai:9443`. Caddy reuses the one certificate for both ports, and ACME HTTP-01 still runs on :80. `/otlp/*` only on the :443 site. **No `/upload/*`.** A site address starting with `http://` serves plain HTTP (for the laptop rehearsal). Caddy publishes 80, 443 and `SYNTH_PORT`. |

Service specifics:
- `collector`: `COLLECTOR_CONFIG=/app/docker-config/collector.aws.yaml`,
  `COLLECT_SCOPE=${TALIUM_S3_KEY_BASE}uploads/` with `TALIUM_S3_KEY_BASE=upload/cc/logs/`.
  Keep the flat var, not nested `${A:+…}`, for parity with the sync overlay.
- `api`: build arg `VITE_VIEWER_ID=${ECON_VIEWER_ID}`. This is your directory id, set after
  the directory import (Step 5).
- `api_synth`: its own `image: talium-aws-api-synth`, build arg
  `VITE_VIEWER_ID=${SYNTH_VIEWER_ID:-github:vp-eng}`, DSN `…/econ_synth`, and **no
  `depends_on: init`** (same reason as `docker-compose.synth.yml`: init must never touch the
  synthetic DB). It depends on postgres healthy only.
- `worker`: DSN `econ` only. It never sees `econ_synth`, so nothing re-folds synthetic rows.
- `postgres`: TCP `pg_isready -h 127.0.0.1` healthcheck (stop-gap's load-bearing detail).
  **No** `initdb.d` seed mount; the real DB starts empty by decision.

### Step 3: Host scripts (repo, run ON the host)
| Script | What |
|---|---|
| `utils/aws_up.sh` | Modelled on `stopgap_up.sh`. Writes `.env`: persisted `POSTGRES_PASSWORD`, `DEMO_PASS_HASH` with `$` doubled, `ECON_HOST`, `SYNTH_PORT`, `TALIUM_ORG_ID=talium`, `TALIUM_S3_BUCKET=talium-bucket`, `TALIUM_S3_KEY_BASE`, `AWS_REGION=us-west-2`, and the viewer ids. Then `up -d --build --wait`, and prints the URLs and the org token. It is idempotent, so a redeploy is `git pull && utils/aws_up.sh`. |
| `utils/aws_down.sh` | `down` or `down -v` (`--wipe`). Warns that `-v` drops **both** databases. |
| `utils/aws_synth_load.sh` | Re-runnable. `createdb econ_synth` if missing (or drop + create with `--replace`), then `gunzip -c deploy/seed/econ_synth.sql.gz \| psql -d econ_synth`, then restart `api_synth`. Refuses any DB name not ending in `_synth` (the loader's own guard). |
| `utils/synth_dump.sh` (laptop) | `pg_dump` `econ_synth` from `talium_stack_pg` → `deploy/seed/econ_synth.sql.gz`. That file is already gitignored by `deploy/seed/*.sql.gz`. Copy it to the host with scp. |
| `utils/synth_demo_refresh.sh --dump` (laptop) | *Added 2026-10 (forecast in the dashboard, issue #85).* Rebuilds the whole synthetic demo (generate, forecast, then `synth_dump.sh`) and writes the same `deploy/seed/econ_synth.sql.gz`. The dump now also carries `forecast_runs` and `forecast_scope_series`. Use this instead of `synth_dump.sh` alone whenever the demo should look current. |

Why a dump and not running the generator on the host: `econ-forecast/artifacts/` is
gitignored, and the generator + loader add a Python toolchain step on the box for no gain.
Regenerate on the laptop, re-dump, then re-run `aws_synth_load.sh --replace`.

Refresh steps (added with the dashboard forecast): on the laptop
`utils/synth_demo_refresh.sh --end-date today --force --dump` (about 10 minutes measured: generate about 5 min, forecast about 40 s, dump about 4 min; a
`--dsn` load would add about 2 min; it regenerates the
dataset, runs the forecast and builds the dump; `--force` replaces an earlier
`econ-forecast/artifacts/org-demo`), then
`scp deploy/seed/econ_synth.sql.gz host:/opt/talium/deploy/seed/`, then on the host
`utils/aws_synth_load.sh --replace`. `--replace` is required: a database loaded from an older dump
has no forecast tables' rows, and the forecast is as of the dataset's last day, so a demo that is
not refreshed shows an increasingly old "current period".

### Step 4: Infra (AWS console or talium-infra)
1. **EC2**: Ubuntu 24.04 arm64, t4g.medium (the webui `npm ci` + build wants about 2–3 GB),
   50 GiB gp3, EIP. Install Docker Engine + the compose plugin (current v2.x).
2. **Security group**: 80/443 and **9443** from anywhere (ACME + users). 22 only from your
   IP, or none if you use SSM.
3. **Instance role**, read-only on the upload prefix:
   ```json
   { "Effect": "Allow", "Action": "s3:ListBucket", "Resource": "arn:aws:s3:::talium-bucket",
     "Condition": { "StringLike": { "s3:prefix": ["upload/cc/logs/uploads/*"] } } },
   { "Effect": "Allow", "Action": "s3:GetObject",
     "Resource": "arn:aws:s3:::talium-bucket/upload/cc/logs/uploads/*" }
   ```
   (+ `AmazonSSMManagedInstanceCore` if you want SSM instead of SSH.)
4. **IMDSv2 hop limit = 2**, or containers cannot reach the role credentials. This is the
   same trap `../upload/deploy/DEPLOY.txt` §3 calls out.
5. **DNS**: one A record, `econ.talium.ai` → EIP, **before** the first `up`, so ACME doesn't
   burn rate limit.

### Step 5: Bring-up (on the host)
```bash
git clone git@github.com:<org>/code-econ.git /opt/talium && cd /opt/talium && git checkout <tag>
ECON_HOST=econ.talium.ai DEMO_PASSWORD='…' utils/aws_up.sh      # synth on :9443 by default
# laptop: utils/synth_demo_refresh.sh --dump && scp deploy/seed/econ_synth.sql.gz host:/opt/talium/deploy/seed/
utils/aws_synth_load.sh
```
Then the directory, so manager screens authorize:
- scp `test-roster/csv_roster/` (real names, **not** committed) to the host, and run
  `docker compose … run --rm -v $PWD/test-roster:/roster:ro api python -m
  server.directory.cli_directory_import --source /roster/csv_roster --apply`.
- Set `ECON_VIEWER_ID=<your directory id>` in `.env`, then run `utils/aws_up.sh` again to rebuild the
  `api` image with it.

### Step 6: Docs
- `docs/deployment.md`: new top section, "Team deployment (current)", covering this plan's
  runbook. Mark §1–§8 (Python uploader, `docker-compose.demo.yml`, `deploy_up.sh`) as legacy.
- Leave a `TODO` in `docker-compose.demo.yml` and `deploy_up.sh` to retire them once the aws
  stack is proven. Retiring them is a follow-up, not part of this change.

## 4. Laptop dress rehearsal (before touching AWS)

Run the same `docker-compose.aws.yml` locally against the **real** bucket, so the only
difference on EC2 is where credentials come from:
- `docker/docker-compose.aws.local.yml` override: mount `~/.aws:/root/.aws:ro` and set
  `AWS_PROFILE` on `collector` only.
- `ECON_SITE=http://localhost:8088 SYNTH_SITE=http://localhost:8089`, set directly and
  bypassing `aws_up.sh`'s derivation (`http://` makes Caddy skip TLS).
- Use its own project name, so it never shares volumes with `talium-stack`.

It passes when §5's checks pass locally.

## 5. Verification

```bash
curl -sI https://econ.talium.ai/healthz                    # 401 (basic auth in front)
curl -s -u demo:pw https://econ.talium.ai/healthz          # {"status":"ok"}
curl -s -u demo:pw https://econ.talium.ai:9443/healthz     # {"status":"ok"}  (synth)
docker compose … logs collector | grep -m3 manifest        # 3 endpoints under upload/cc/logs/uploads/
docker compose … exec postgres psql -U postgres -d econ -c \
  "select user_id, count(*) from sessions group by 1"       # anil + sumandra (+ 3rd endpoint's user)
docker compose … exec postgres psql -U postgres -d econ -c \
  "select count(*) filter (where repo_id like 'local:%') local, count(*) total from session_info"
```
- A new `talium-sync` push from either laptop appears on the dashboard within about
  `COLLECT_INTERVAL_S` + worker latency.
- Browser: Overview → Team → Person on `:443` as your viewer, and the same walk on `:9443` as
  `github:vp-eng`.
- `docker compose -f docker/docker-compose.aws.yml config -q` with a dummy `.env` goes in the
  test/CI step. Compose files have no pytest, and this is the cheapest lint.

## 6. Teammate client (parallel track, not blocking)
- Publish a `talium-sync` built from `../upload` at or after `dae4f26` (Part A: real-cwd repo
  resolution + `repo_url` + repo-only manifest delta) to `artifacts/talium-upload/`.
  Have sumandra (and you) upgrade and re-run. The meta-change condition in `staging.Commit`
  re-writes existing manifests with repo filled in, so no bytes are re-uploaded. **But the
  collector will not re-ship those sessions.** Its checkpoint identity is the JSONL's
  size + head hash (`collector/store.py`), which a metadata-only change leaves unchanged.
  Verified 2026-09-23. Until the collector folds manifest metadata into the identity, a wipe and
  re-sweep is the way to re-attribute.
- **Check that `talium-uploadd` 0.1.0 passes `repo_url` through.** If the server re-serializes
  `FileEntry` with a struct that lacks the field, it drops it silently. The collector falls
  back to `repo`, so this is degraded, not broken. Confirm after the first post-upgrade
  sync by reading one manifest.

## 7. Known limits (accepted for a 2-person demo)
- **One baked viewer per image.** Everyone behind the shared password sees `econ` as you
  (manager), and `synth` as `github:vp-eng`.
- **The browser prompts for basic auth once per port**, because credentials are cached per
  origin. A network that blocks non-standard HTTPS ports can't reach `:9443`. If that bites,
  moving synth to its own hostname is a Caddyfile-only change. A per-user view needs the authenticating proxy
  that `docker/Dockerfile`'s comment describes.
- **No GitHub App on the host yet.** PR/feature/quality screens fill only from whatever
  `git_artifacts` exist, and the DB starts empty, so at first there are none. Follow-up: put
  the GitHub App key on the host and run `utils/git_backfill.sh` for `talium-ai/*` on a cron.
  The webhook receiver is not in any compose file today, so backfill is the path that adds no
  new public surface.
- **Backups**: an EBS snapshot (DLM daily) of the root volume is the cheap default. `econ`
  can also be rebuilt from S3 by wiping and re-sweeping. `econ_synth` is rebuilt from the dump.
- **Postgres is not reachable off-host.** Use `docker compose exec postgres psql` for
  ad-hoc queries.

## 8. Order of execution
1. Step 1 (commit/tag) → 2. Step 2–3 (compose, config, scripts) → 3. §4 laptop rehearsal →
4. Step 4 infra → 5. Step 5 bring-up → 6. §5 verification → 7. Step 6 docs.
§6 (teammate client) can run any time in parallel.
