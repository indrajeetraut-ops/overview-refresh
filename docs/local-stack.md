# Local Stack — running the pipeline against a real Claude Code

A dockerised stack you drive with your own Claude Code as the client. Verified end to end on
2026-08-05: real OTLP over the wire, both signal sources joining on `request_id`.

Separate from `docker/docker-compose.yml`, which is the **test** Postgres (`utils/db_up.sh`,
port 5432). This stack has its own project, database and port (5433), so `pytest` doing
`init --force` can never drop the data you are looking at, and both can run at once.

For the same containers on AWS — behind TLS and basic auth, with S3 staging and a database
that outlives the host — see [deployment.md](deployment.md).

---

## 1. What you need running, and why

| Service | Needed for | What it does |
|---|---|---|
| **postgres** | everything | The single database. `otel_*` tables, `sessions`, `session_info`. |
| **init** | everything | One-shot: builds the schema, mints an org token. Exits. |
| **ingest** | everything | The one front door: `/otlp/v1/{metrics,logs}` for OTel **and** the msging routes for JSONL, one process. |
| **worker** | everything | The true-up tick (60 s) and the prune. **Nothing reaches `session_info.otel` without it.** |
| **api** | everything | The dashboard: P2's read-only API plus the P3 static SPA it serves, one process. Not gated behind `--jsonl` -- `otel_only` is a real tier with a real (if smaller) dashboard, not a degraded one. |
| **uploader** | JSONL only | Receives a tar of `~/.claude/projects`, stages it. |
| **collector** | JSONL only | Sweeps the staging bucket, normalizes, ships to ingest. Batch, looped here. |

**Claude Code itself is the OTel client.** Nothing is installed on your machine for that half
— it exports OTLP straight to ingest. That is the zero-install tier, and it is a real tier:
you get spend truth from it alone.

The uploader and collector exist only for the *other* half. Adding them buys session
structure (turns, tools, files) and closes the `request_id` join, which is what turns
`otel_only` into `both`, and gives every request a cost even where OTel never saw it.

```
Claude Code ──OTLP/protobuf + Bearer token──► ingest ──► otel_series / otel_metric_points
     │                                          │        otel_api_requests
     └─ writes ~/.claude/projects/*.jsonl       │                    │
              │                                 │                    ▼
              └─► uploader ─► collector ────────┘              worker (60 s tick)
                                                                     │
                                                          session_info.otel
                                                                     │
                                                                     ▼
                                                      api (dashboard, :8090) ◄── you
```

---

## 2. Start it

OTel only — postgres, ingest, worker:

```bash
utils/stack_up.sh
```

Everything, including the JSONL path:

```bash
utils/stack_up.sh --jsonl
```

It prints the settings block with your org token filled in. Paste that into
`~/.claude/settings.json` and **start a new Claude Code session** — the exporter is read at
startup:

```json
{
  "env": {
    "CLAUDE_CODE_ENABLE_TELEMETRY": "1",
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
    "OTEL_EXPORTER_OTLP_ENDPOINT": "http://localhost:8443/otlp",
    "OTEL_EXPORTER_OTLP_HEADERS": "Authorization=Bearer tk_org_..."
  }
}
```

The token is printed again on every `stack_up.sh`, and survives `stack_down.sh`. Only
`stack_down.sh --wipe` destroys it — after which you must re-point Claude Code at the new one.

| | |
|---|---|
| Dashboard | `http://localhost:8090` (`STACK_API_PORT`) |
| Ingest | `http://localhost:8443` (`STACK_INGEST_PORT`) |
| Uploader | `http://localhost:8080` (`STACK_UPLOADER_PORT`) |
| Postgres | `postgresql://postgres@localhost:5433/econ` (`STACK_PG_PORT`) |

---

## 3. View the dashboard

```
open http://localhost:8090
```

Comes up immediately, even before anything has folded -- an empty timeframe is the honest
first screen, not an error. Once the worker has ticked (§4 below, up to ~2 min after your
first activity), the Overview and Sessions screens fill in. Single-user v1: it shows whichever
user id folded sessions carry, no login (`docs/plan/2026-08-06-dashboard-ui-implementation.md`
BLUE §9 item 3 -- auth arrives with the org dashboard, not before).

Storytelling (the full session deep-dive: burn chart, narrator line, acts, receipt) and
provenance (the token-origin breakdown, the practice scorecard) aren't built yet -- those
zones render their honest "hasn't run yet" state rather than fake data. `/session/{id}`
this pass is a plain metrics panel, not the storytelling view.

---

## 4. Watch it work

Use Claude Code for a couple of minutes, then:

```bash
docker logs -f talium_stack_ingest | grep otlp_
```

Metrics arrive every 60 s, log events every ~5 s. Each accepted body logs counts only — no
payload content ever reaches a log line:

```
otlp_batch_accepted signal=logs vendor=claude_code org_id=local-dev events_kept=1
                    events_dropped={'tool_result': 1, 'hook_execution_start': 1} rejected=0
```

Then the fold, within ~60 s more:

```bash
docker logs -f talium_stack_worker | grep otel_trueup
```

```
otel_trueup_tick vendor=claude_code sessions=1 sources={'both': 0, 'otel_only': 8, 'jsonl_only': 0}
                 cost_basis={'vendor': 8, 'estimated': 0, 'unknown': 0} otel_gap={}
```

And the answer:

```bash
docker exec talium_stack_pg psql -U postgres -d econ -c "SELECT session_id, otel -> 'measures' AS measures, otel -> 'sources' AS sources, otel -> 'cost_basis' AS cost_basis FROM session_info;"
```

Freshness is ~1–2 min end to end: 60 s client export + up to 60 s worker tick.

---

## 5. Closing the join

With `--jsonl` running, ship your session files:

```bash
utils/upload_claude_sessions.sh
```

The collector sweeps every 30 s, so nothing else to run. Within a tick or two the same
session's `sources` moves from `otel_only` to `both`:

```
otel_trueup_tick vendor=claude_code sessions=1 sources={'both': 8, 'otel_only': 0, 'jsonl_only': 0}
```

That migration is the hybrid working: `otel_only` is a **transient** label for a request whose
JSONL has not arrived yet, and every tick re-runs the join.

Note the two sources are structurally different, and the cost block shows it rather than
hiding it:

- `otel_only` that never migrates = a **background call** (`prompt_suggestion`,
  `generate_session_title`). The JSONL path cannot see these at all — ~5.7% of session spend.
- `jsonl_only` + `otel_gap: unobserved` = an **interrupted request**. OTel fires only on
  completion, so this is permanent — but the cost is still estimated from its tokens.
- `jsonl_only` + `otel_gap: pre_otel` = the request **predates telemetry**. Expect a lot of
  these the first time you upload history; nothing is broken.

---

## 6. Poking at it

```bash
docker exec talium_stack_pg psql -U postgres -d econ
```

Some queries worth having:

```sql
-- what a session's series look like
SELECT metric_name, attrs->>'type' AS type, attrs->>'query_source' AS src, count(*)
FROM otel_series s JOIN otel_metric_points p USING (series_id)
WHERE session_id = 'X' GROUP BY 1,2,3;
```

```sql
-- the request ledger, including the calls JSONL never sees
SELECT request_id, model, input_tokens, output_tokens, cost_usd_micros, query_source
FROM otel_api_requests ORDER BY event_ts DESC LIMIT 20;
```

```sql
-- how far the fold has got
SELECT * FROM otel_watermarks;
```

Issue a second token, or retire one:

```bash
docker exec talium_stack_ingest python -m server.storage.cli_db org-token list --org local-dev
```

Replay the recorded fixtures instead of using Claude Code live — useful for a deterministic
check that the stack is wired correctly:

```bash
TOKEN=$(docker exec talium_stack_ingest cat /data/org_token.txt) && PYTHONPATH=src venv/bin/python -c "
import json, pathlib, httpx, os
from server.ingest.otlp.decode import encode_protobuf
with httpx.Client(base_url='http://localhost:8443') as c:
    for sig, f in (('metrics','metrics_session.jsonl'), ('logs','logs_session.jsonl')):
        for line in pathlib.Path('tests/fixtures/otel/'+f).read_text().splitlines():
            r = c.post(f'/otlp/v1/{sig}', content=encode_protobuf(json.loads(line), sig),
                       headers={'authorization': 'Bearer $TOKEN', 'content-type': 'application/x-protobuf'})
            assert r.status_code == 200, r.text
print('ok')"
```

That lands 12 series / 80 points / 8 requests and folds to `cost_usd = 1.05188999999999989`
(the goldens in `tests/fixtures/otel/README.md`).

---

## 7. Stop it

```bash
utils/stack_down.sh
```

```bash
utils/stack_down.sh --wipe
```

`--wipe` drops the volumes: database **and** org token. The next start mints a new token and
Claude Code has to be re-pointed at it.

---

## 8. Gotchas

- **Claude Code reads the exporter config at startup.** Editing `settings.json` does nothing
  to a running session; open a new one.
- **No cost block until the worker has ticked.** Up to 60 s after the export, which is itself
  up to 60 s after the activity. Nothing is wrong for the first two minutes.
- **`otel_only` is not an error.** It is the correct state for a session whose JSONL has not
  been uploaded — and the permanent, correct state for background calls.
- **The stack DB is not the test DB.** 5433/`econ` here, 5432/`econ_test` for `pytest`. Don't
  point `TALIUM_TEST_DSN` at this one; the tests drop and recreate their schema.
- **`http://`, not `https://`.** Fine locally. A real deployment terminates TLS in front —
  the token is a bearer credential and cleartext would hand it to anyone on the path.
- **A `vendor=unknown` warning in the ingest log** means a payload no mapping claimed. From a
  real Claude Code that means `config/otel/claude_code.yaml` did not load or its detect rules
  no longer match the client's scope name — worth chasing, not ignoring.
