# Manual Testing Guide — Batch Uploader → Collector (Normalizer + Engine)

How to exercise each piece of the batch ingestion pipeline on its own, and how to chain them
into a real end-to-end run. Every command below was actually run against this repo before
being written down here — paths, ports, and env vars are real, not illustrative.

Pipeline recap (see `docs/design/2026-07-19-upload-batch-ingestion.md` and
`docs/modules/{batch-uploader,source-adapter,normalizer}.md`):

```
developer's tar.gz  --HTTP-->  batch uploader (src/server/uploader)
                                    |  writes uploads/<upload_id>/{raw/..., manifest.json}
                                    v
                               S3 / local staging bucket
                                    |  read by
                                    v
                         collector engine (src/collector)
                           source-reader -> normalizer -> msging client
                                    |
                                    v
                        Ingest (not built yet) -- or, for testing, a dump file
```

Three independently-runnable pieces map onto that: the **uploader**, the **normalizer**
(inside the collector, but usable completely standalone), and the **collector engine**
(the adapter that reads what the uploader staged). This guide covers all three, then the
full chain.

## 0. Setup (once per shell)

```bash
cd /Users/anil/workspace/Talium/code-econ
source venv/bin/activate   # or prefix commands with venv/bin/

# A scratch directory both the uploader and collector configs read from (config
# rule 8: defaults in code -> YAML -> ${ENV} substitution). Point it wherever you like.
export TALIUM_DATA_DIR=/tmp/talium-demo/data

mkdir -p "$TALIUM_DATA_DIR"
```

`pytest` doesn't need `PYTHONPATH` set (`pytest.ini` already has
`pythonpath = src tests`). Running a CLI module directly does:
`PYTHONPATH=src python -m collector.cli_collect ...`. A couple of snippets below also touch
the test-only fixture builders (`tests/fixtures/...`), which need `PYTHONPATH=src:tests`.

### 0a. The no-hoops shortcut — `utils/test_*.sh`

If you just want to *run the tests* without any of the setup above, there's one wrapper per
independently-runnable piece. Each one creates/activates the venv, installs anything missing,
sets `TALIUM_DATA_DIR` + `PYTHONPATH`, and runs — from a bare shell, nothing exported:

```bash
utils/test_uploader.sh     # §1a — the uploader suite            (utils/test_uploader.sh serve → §1b live service)
utils/test_normalizer.sh   # §2a — the normalizer suite          (utils/test_normalizer.sh <file.jsonl> [out] → §2b)
utils/test_engine.sh       # §3a — the engine/adapter suite       (utils/test_engine.sh collect [out] → §3 dump run)
utils/test_e2e.sh          # §4a — the in-process end-to-end tests
utils/test_all.sh          # §5  — the whole suite
```

Any extra arguments pass straight through to `pytest` (`utils/test_normalizer.sh -k tool_pairing`,
`utils/test_all.sh -x`). The sections below give the underlying commands if you'd rather run them
by hand. `utils/_test_env.sh` is the shared bootstrap they all source — not run directly.

---

## 1. Batch uploader — standalone

### 1a. Automated

```bash
utils/test_uploader.sh            # or: pytest tests/server/uploader -q
# 37 passed
```

### 1b. Manual, over real HTTP

Start the service (default config `config/uploader.yaml`, staging root
`$TALIUM_DATA_DIR/uploader-staging`, port 8080):

```bash
PYTHONPATH=src python -m server.uploader.cli_serve --config config/uploader.yaml &
sleep 1
curl -sS http://localhost:8080/healthz   # {"status":"ok"}
```

Upload something. Two options:

**Your own real Claude Code history** (the actual one-liner from the RFC):

```bash
tar czf - -C ~ .claude/projects \
  | curl -sS -X POST "http://localhost:8080/v1/uploads?org=acme&user=anil" \
         -H "Content-Type: application/gzip" --data-binary @-
```

**A tiny synthetic archive instead** (deterministic, no personal data, fast):

```bash
PYTHONPATH=src:tests python -c "
from fixtures.uploader import archives
open('/tmp/talium-demo/demo-upload.tar.gz', 'wb').write(archives.build_subagents_archive())
"
curl -sS -X POST "http://localhost:8080/v1/uploads?org=acme&user=anil" \
     -H "Content-Type: application/gzip" \
     --data-binary @/tmp/talium-demo/demo-upload.tar.gz
```

Either way you get back a `201` with `upload_id`/`file_count`/`status: staged`. Verify what
landed on disk:

```bash
find "$TALIUM_DATA_DIR/uploader-staging" -name manifest.json
cat "$TALIUM_DATA_DIR/uploader-staging/uploads/<upload_id>/manifest.json" | python3 -m json.tool
```

The manifest is the uploader's real output contract — its presence (written *last*, doc §7)
is what tells the collector this upload is ready.

Stop the service when done: `kill %1` (or `pkill -f server.uploader.cli_serve`).

---

## 2. Normalizer — standalone (no uploader, no engine, no network)

The lowest layer. Point it at any real or synthetic Claude Code `<session>.jsonl`.

### 2a. Automated

```bash
utils/test_normalizer.sh          # or: pytest tests/collector/normalizer -q
# 55 passed
```

Three layers of coverage here:
- **Mapping/pairing/resilience** — the synthetic `tests/fixtures/normalizer/sessions.py`
  builders, each proving one rule in isolation (§3 mapping, tool pairing, defensive typing,
  redaction, subagent correlation).
- **Real-data replay** (`test_recorded_sessions.py`) — runs the normalizer over committed,
  *sanitized* real sessions in `tests/fixtures/normalizer/recorded/` (a 64-record regular
  session and a 409-record two-subagent spawner). Asserts it survives real record spread
  without crashing or dropping a record type, that subagents correlate, and run-twice
  idempotency — see `recorded/README.md` for provenance and how they're sanitized.
- **Shape contract** (`test_shape_contract.py`) — freezes the payload *structure* the
  normalizer emits (every key-path + value-type per `event_type`, unioned over the real +
  synthetic corpus so all nine event types are covered) into
  `tests/sample-data/normalizer-shape-contract.json`. A silent field rename/drop/retype — a
  fidelity leak that value-based tests miss — fails this with a readable diff. After an
  *intended* shape change, re-freeze and review the diff:

  ```bash
  NORMALIZER_REGEN_SHAPE=1 pytest tests/collector/normalizer/test_shape_contract.py
  ```

### 2b. Manual, against a real session file

```bash
utils/test_normalizer.sh ~/.claude/projects/<project-dir>/<session-id>.jsonl /tmp/talium-demo/normalized.json
# or, by hand:
PYTHONPATH=src python -m collector.cli_normalize \
    ~/.claude/projects/<some-project-dir>/<some-session-id>.jsonl \
    --out /tmp/talium-demo/normalized.json
```

Subagent siblings under `<session-id>/subagents/...` are auto-discovered. Open
`/tmp/talium-demo/normalized.json` and read the event-by-event breakdown directly — no other
part of the system needs to be running for this.

---

## 3. Collector engine (the adapter) — standalone

The engine reads what a *manifest* describes, so it needs something already staged. You
don't need a live uploader **process** for this — any of these get you staged data to point
the engine at:

- the output of §1b above (real HTTP round trip), or
- `pytest tests/server/uploader -q` (each test stages into its own `tmp_path`, not reusable
  after the run), or
- calling `service.handle_upload()` directly, which is what the automated collector tests do
  (see `tests/collector/test_engine.py`).

Using §1b's staged upload, run the engine with `--dump-to` — this uses the **real** msging
client code (register, one POST per msg, gzip, the durable store, the SSE ACK path — see
`src/collector/dump_transport.py`'s fake in-process Ingest) but writes the `Envelope`s it would
have shipped to a file, so no live Ingest process is needed:

```bash
PYTHONPATH=src python -m collector.cli_collect \
    --config config/collector.yaml \
    --dump-to /tmp/talium-demo/shipped.jsonl
```

Inspect it:

```bash
wc -l /tmp/talium-demo/shipped.jsonl
python3 -c "
import json
for line in open('/tmp/talium-demo/shipped.jsonl'):
    e = json.loads(line)
    print(e['msg_id'], e['info']['session_id'], len(e['payload']['events']), 'events')
"
```

Each line is one flat wire `Envelope` (post msging-rewrite: `msg_id` + `producer_id` +
`info` + `payload`, no `header`) — **one msg per normalized session** now, its events in
`payload['events']` and its identity in `info`, not one envelope per event.

Run the exact same command again: it should report `sources_processed: 0`,
`sources_skipped_unchanged: <n>` in the log, and the line count in `shipped.jsonl` shouldn't
grow — the checkpoint (`$TALIUM_DATA_DIR/collector_checkpoints.json`) is doing its job.

### 3a. Automated

```bash
utils/test_engine.sh              # or: pytest tests/collector --ignore=tests/collector/normalizer -q
# 23 passed  (store, checkpoint, source_reader, engine, dump_transport)
```

---

## 4. End to end

### 4a. Automated (fastest — no server process, no manual staging)

```bash
utils/test_e2e.sh                 # or: pytest tests/collector/test_engine.py -q
# 4 passed
```

This chains the *actual* uploader (`server.uploader.service.handle_upload`) and the *actual*
engine (`collector.engine.run_once`) in-process, wired to a mock ingest server via
`httpx.MockTransport` (same technique `--dump-to` uses, just recording to a Python list
instead of a file). It covers the plain run, the unchanged-file skip, a re-upload of a longer
file (duplicate `event_id`s — expected, ingest's dedupe job), and crash-resume.

### 4b. Manual, real processes wired together

1. §1b: start the uploader, POST an archive, confirm `manifest.json` exists.
2. §3: run `collector.cli_collect --config config/collector.yaml --dump-to
   /tmp/talium-demo/shipped.jsonl` against that same staging root.
3. Inspect `shipped.jsonl` as in §3.
4. Re-run step 2 unchanged — confirm nothing new ships.
5. POST a **longer** version of the same archive (reuse the same `org`/`user`, a fresh
   `upload_id` is minted automatically) and re-run step 2 — confirm the file's events
   reship (duplicate `event_id`s are correct and expected; see §4a).

---

## 5. Gotchas found while writing this guide

- **`config/collector.yaml`'s `msging.server_url` is a literal placeholder, not an `${ENV}`
  ref.** It's never actually contacted in `--dump-to` mode (the transport is swapped), but
  `ClientConfig.server_url` still requires a non-empty string to load at all — and this
  loader's env substitution (`config.py`'s `_ENV_REF`) has no bash-style `:-default`
  fallback, so an unset env var used to leave an empty YAML scalar, which parses as `null`
  and fails validation before dump mode gets a chance to ignore it. Fixed by making it a
  plain literal (`http://localhost:8000`) in the checked-in config; edit it directly for a
  real deployment.
- **The uploader's and collector's staging roots must match.** `config/uploader.yaml`'s
  `object_store.local.root` and `config/collector.yaml`'s `source_store.local.root` both
  resolve from `$TALIUM_DATA_DIR` but are two independently-written config files — if you
  copy either one elsewhere, double check they still point at the same
  `uploads/<upload_id>/...` root, or the collector will simply find nothing.
- **The auto-flush thread must stay off.** `config/collector.yaml` sets
  `msging.flush_interval_s: 0` deliberately — the engine flushes explicitly once per source
  (`engine.py`), and a background auto-flusher racing that would break the "did this
  source's flush fully drain" check the checkpoint commit depends on. Don't remove that line
  in a config you write yourself.
- **macOS `tar czf` embeds AppleDouble metadata files.** Running §1b's real-`~/.claude`
  one-liner on macOS, `tar` emits a `._<name>` sidecar (binary resource-fork data) next to
  every real entry, plus the occasional `.DS_Store` — and until this was fixed, the uploader
  staged them as if they were real session content, which then crashed the collector trying
  to `json.loads()` binary junk (`UnicodeDecodeError`). Fixed on both ends: the uploader
  (`unpack.py`) now skips `.DS_Store`/`._*` entries at unpack time so they're never staged
  or listed in the manifest going forward, and the collector's source-reader skips them
  defensively too (so a manifest staged *before* this fix, already sitting on disk, doesn't
  need to be re-uploaded — just re-run the collector). If you want tar to never write these
  in the first place: `COPYFILE_DISABLE=1 tar czf - -C ~ .claude/projects | curl ...`.
- **Full suite:** `utils/test_all.sh` (or `pytest tests/ -q`) → 196 passed, 9 skipped, across
  all of the above plus msging's own suite. The 9 skips are the storage DB tests, which need
  `TALIUM_TEST_DSN` pointed at a live Postgres (`utils/db_up.sh`); everything else runs with no
  external services.

---

## 6. GitHub acquisition — webhook + API round trip

**Scope:** GitHub webhooks and the App-authenticated REST/GraphQL APIs, landing real deliveries
in `git_deliveries`, ringing the `git_map` worker topic on `queue_jobs`, and mapping them into
`git_artifacts` (`src/server/ingest/git/`, `src/server/worker/git_map.py` — mounted/run inside
the real ingest and worker processes, not standalone). Run the worker (`utils/worker.sh`)
alongside the ingest host for a delivery to progress past `pending`. History backfill
(`cli_backfill.py`, GraphQL primary / REST gap-fill) and author→GitHub-login identity
resolution are built too (implementation plan A5/A6). The analyzer that correlates any of this
to sessions and classifies the resulting PRs (`docs/modules/pr-work-classification.md`,
implementation plan B1–B6) is also built — `src/server/worker/pr_class/` — but has no live-App
manual section of its own below since it needs no GitHub App, webhook, or tunnel: it runs
entirely off whatever `git_artifacts`/`sessions` rows already exist, so §6a's automated tests
are its manual-testing coverage (`tests/server/worker/pr_class/`). See `docs/modules/git-source.md`
for the contracts and `docs/plan/2026-08-25-git-integration-implementation.md` for what is and
isn't built.

### 6a. Automated (needs `TALIUM_TEST_DSN`, `utils/db_up.sh`)

```bash
source venv/bin/activate
utils/db_up.sh   # local Postgres, if not already running
export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test
python -m pytest tests/server/ingest/git/ -q
# signature (valid/invalid/absent/mutated/wrong-secret/no-prefix) + JWT claims + client
# hardening (rate-limit throttle, ETag, 401/secondary-limit backoff) + the real webhook route
# (land + ring, oversize, duplicate delivery, storage-down atomicity)

python -m pytest tests/server/worker/pr_class/ -q
# correlation (sha/trailer/heuristic precedence, owning-PR dedup, out-of-scope skip) + change-type
# and artifact-class/recall/module classification + integration-PR detection + the pr_classify
# orchestrator (idempotent replay) + B6 rollup (multi-PR dedup, rework netting, grouping splits,
# confidence-band coverage, pr_list refresh)
```

### 6b. Register the App (once, GitHub UI, your own org)

1. **Settings → Developer settings → GitHub Apps → New GitHub App.**
2. **Permissions (all read-only):** Repository → Metadata, Pull requests, Contents, Checks;
   Organization → Members.
3. **Subscribe to events:** Pull request, Pull request review, Push, Check suite, Installation,
   Installation repositories.
4. **Webhook → Active.** URL = your tunnel (6c). **Webhook secret:** generate one and keep it —
   `openssl rand -hex 32`.
5. **Generate a private key** — the `.pem` downloads once. Store it outside the repo.
6. Note the **App ID** and **Client ID** from the App's General page.

Keep the App **private** for the POC (installable only on your own org).

### 6c. Tunnel a public URL to your laptop

```bash
# option A: smee.io — open https://smee.io, "Start a new channel", then:
npx smee-client --url https://smee.io/YOUR_CHANNEL --target http://localhost:8099/talium/git/v1/webhook/github
# option B: cloudflared
cloudflared tunnel --url http://localhost:8099
```

Set the App's Webhook URL to the tunnel's public URL + `/talium/git/v1/webhook/github`.

### 6d. Env (this shell)

```bash
export TALIUM_GITHUB_APP_ID=123456
export TALIUM_GITHUB_CLIENT_ID=Iv1.xxxxxxxxxxxx
export TALIUM_GITHUB_PRIVATE_KEY_PATH=/absolute/path/to/app.private-key.pem
export TALIUM_GITHUB_WEBHOOK_SECRET=the-secret-from-6b
```

### 6e. Install the App on your org

App's public page → **Install** → pick the org → **All repositories** or a subset. Installing is
what opens the webhook channel *and* grants API access — one grant, both directions.

### 6f. Run the three probes

```bash
# 1. credentials: signs a JWT, mints an installation token, lists installations + their repos.
utils/github_installations.sh

# 2. webhook receiver + mapper: run the REAL ingest host (webhook route) AND the worker
#    (git_map topic) -- both need config/storage.yaml, config/msging.yaml, and Postgres up.
utils/db_up.sh
utils/ingest.sh --config config/ingest.yaml &
utils/worker.sh --config config/worker.yaml &
#    ...now open/close a PR on an installed repo, then check it landed AND mapped:
psql "$TALIUM_TEST_DSN" -c "select delivery_id, event, action, status from git_deliveries order by received_at desc limit 5;"
psql "$TALIUM_TEST_DSN" -c "select artifact_id, kind, state, merge_commit_sha from git_artifacts order by updated_at desc limit 5;"

# 3. API round trip: same repo's recent PRs via REST (list) and GraphQL (stats+files+reviews).
utils/github_probe.sh --repo your-org/your-repo

# 4. history backfill: GraphQL bulk reader by default, --reader rest for the gap-fill path.
#    First run has no stored cursor, so it reads the repo's whole history (bound this with
#    --since on a big repo). Needs the worker's storage config, not the worker process itself.
utils/git_backfill.sh --repo your-org/your-repo --storage-config config/storage.yaml
psql "$TALIUM_TEST_DSN" -c "select repo_id, backfill_cursor, backfill_state from git_repos;"
#    Run it again immediately: the cursor means a second pass reads almost nothing new.
utils/git_backfill.sh --repo your-org/your-repo --reader rest
```

### 6g. What "passing" looks like

- `github_installations.sh` prints your org and its repos → the private key, JWT and token mint all work.
- Opening a PR inserts one row in `git_deliveries` (`event=pull_request`), the worker picks up
  the matching `queue_jobs` row (`topic=git_map`) within a poll interval, and `git_deliveries.status`
  moves `pending` → `done` with a `pr` row appearing in `git_artifacts`. An unsigned/forged POST
  is rejected `401` and never lands. `git_artifacts.user_id` shows `github:<login>` for a
  non-bot author (auto-created in `users` on first sighting, A6) and stays `null` for a bot.
- `github_probe.sh` prints PRs twice: REST rows carry no `additions`/`deletions` (Pull Request
  Simple), GraphQL rows carry `+add/-del`, file list and review count in one call, plus the
  `rateLimit.cost` — the asymmetry that decides REST vs GraphQL for the historic pass. It also
  logs one `rate_limit_budget` line summarizing what the client measured for the run.
- `git_backfill.sh` populates the SAME `git_artifacts` table the webhook path does — a PR you
  also received live should show byte-identical rows either way (§11's reader-agreement bar).
  `git_repos.backfill_cursor` advances to (roughly) now; a second run right after the first logs
  a much smaller `backfill_page` count (ETag `304`s on the REST reader, an empty `search` page
  on GraphQL).

### 6h. Redelivery

GitHub's App page → **Advanced** tab lists every delivery, each with a **Redeliver** button —
it resends the *exact same* `X-GitHub-Delivery` id and body, which is the load-bearing case
`land_delivery`'s insert-if-absent exists for (git-source.md §5.2, §10): the row already exists,
so nothing new lands and the worker never re-rings `git_map` for it.
```bash
# Before redelivering, note the row's mapped_at:
psql "$TALIUM_TEST_DSN" -c "select delivery_id, status, attempts, mapped_at from git_deliveries where event='pull_request' order by received_at desc limit 1;"
# Click Redeliver in the GitHub UI, then re-run the same query --
# status/attempts/mapped_at must be byte-identical: the redelivery answered 202 but changed nothing.
```
A **manual redelivery only reaches deliveries from the past 3 days** (GitHub's own limit) — for
anything older, or a receiver outage discovered later, the gap-fill timer
(`config/worker.yaml`'s `git_gapfill_*`, or a manual `cli_backfill --since <date>`) is the actual
recovery path, not redelivery (git-source.md §10).

### 6i. Gotchas

- **Signature is over the RAW body.** The route never re-serializes before verifying; any proxy
  that rewrites the JSON body will break the HMAC.
- **JWT `iss` is the Client ID** (GitHub's current recommendation), App ID as fallback. Both work.
- **GraphQL returns HTTP 200 with `errors[]` on a permission gap or timeout** — the client raises
  on a non-empty `errors[]` rather than mapping partial data. If a probe fails here, check the
  App's permissions were approved on the installation.
- **No auto-retry from GitHub.** A delivery missed while the receiver is down is not resent (only
  redeliverable from the App's Advanced tab, within 3 days) — see §6h. The gap-fill timer is the
  actual recovery path past that window.

---

## 7. Go uploader (`talium-sync` → `talium-uploadd`) on the local stack

The same collector → ingest → worker → api pipeline as §4, fed by the Go uploader
(`../upload`) instead of `utils/upload_claude_sessions.sh` + `src/server/uploader/`. The topology is
the AWS one: `talium-uploadd` + `tusd` behind Caddy, writing to an S3 bucket (MinIO here) under
the `cc/logs` base prefix, and the collector reading that bucket (as `collector.demo.yaml` does).
A plain-directory variant is not possible — `talium-uploadd` finalizes a tus upload by copying the
S3 key tusd reports, which tusd's file store never provides.

```bash
utils/stack_up.sh --sync        # stack + docker/docker-compose.sync.yml; stops the jsonl collector
utils/sync_local.sh --source DIR   # optional: discover DIR instead of ~/.claude/projects
utils/sync_local.sh             # build talium-sync from ../upload HEAD, enroll, run --once
utils/sync_e2e_check.sh         # MinIO manifest + byte identity + Postgres fold
utils/stack_down.sh [--wipe]    # includes the overlay once it has been used
```

- **Secrets** are generated once into `var/stack-sync.env` (gitignored, 0600). The enrollment
  secret is keyed by the stack's `TALIUM_ORG_ID`, because `talium-uploadd` attests `org_id` from
  whichever secret matched — never from `talium-sync enroll --org`.
- **`~/.talium/sync.yaml` is never touched**: `sync_local.sh` enrolls into
  `var/talium-sync.local.yaml` and passes `--config` explicitly (and clears the `TALIUM_*` client
  env overrides, which outrank the file). `talium-sync` logs only to
  `~/Library/Logs/talium-sync/talium-sync.log`; the wrapper echoes the lines of its own run.
- **Base prefix**: `TALIUM_S3_PREFIX` (default `cc/logs`; set it empty for the bucket root) drives
  uploadd's `prefix`, tusd's `-s3-object-prefix` and the collector's `COLLECT_SCOPE` together.
- **`--jsonl` and `--sync` are mutually exclusive**: both collectors use
  `/data/collector_checkpoints.json`, deliberately — logical keys (`org/user/rel`) are identical
  across uploader generations, so switching must not reprocess.
- **Expected convergence**: after an upload, the next pass with nothing new still writes the
  manifest once (every file flips `live` → `stale`); the pass after that reports
  `"written":false`, and the collector reports `sources_skipped_unchanged` for everything.
- **Expected `repo: null`**: non-session files (`<slug>/.session_cache.json`, `tool-results/*`)
  and sessions whose working directory no longer exists (a deleted worktree) — the latter fold
  with `repo_id_provenance = path` (`local:<name>`), as the plan's risk table predicts.
- MinIO's images come from `quay.io/minio/*`; the Docker Hub `minio/*` repositories no longer exist.
