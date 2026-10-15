# API and web-service latency: implementation plan

**Status:** IMPLEMENTED 2026-09-26, on `perf/api-latency`, all phases (A/B/0/C/D/E) in one
branch at the user's request rather than split into three PRs. Full local test suite green
(`tests/server/`, 1078 passed, 7 skipped; one pre-existing unrelated flake in
`test_otel_e2e.py` reproduces on `demo-release-v2` too and is tracked separately). Verified
end to end against a local API server over `econ_synth`: every endpoint returns 200, the
concurrent-load ratio is 0.89x (target: <= 1.3x), and `utils/api_parity.py` produces
byte-identical snapshots across repeated runs. §5.4's on-prod latency numbers still need a
real deploy to measure; not run as part of this implementation.
**Branch:** new `perf/api-latency`, cut from `demo-release-v2` (`61b2dd8`).
**Scope:** `code-econ` only:
- API: `src/server/api/`
- storage: `src/server/storage/`
- worker writers: `src/server/worker/`
- Caddy: `deploy/caddy/`
- the synthetic generator (`econ-forecast/`) and the synthetic loader

**Delivery:** three PRs, in order.

| PR | Phases | Risk | Needs a migration |
|---|---|---|---|
| 1 | A (sync handlers + workers), B (Caddy compression) | low | no |
| 2 | 0 (synthetic `turns` fix), C (extract fields in SQL), D (per-endpoint queries) | medium | no |
| 3 | E (promoted `session_info` columns + backfill) | medium | yes |

Phases C and D ship in one PR as two commits. D splits the query that C slims, and once C
takes the JSON out of the shared query the model mix *has to* get its own query (§4.4).

---

## 0. Why

All numbers below were measured on prod (`cc-econ-demo`, t3a.medium, 2026-09-26). Every
measurement was read-only.

### 0.1 Where the time goes

| Symptom | Measured | Cause |
|---|---|---|
| `index.js` takes ~20 s | 756 KB served in **32 ms** on-host, but Caddy spent **12–24 s** handing it to Cloudflare's Mumbai edge | network: Oregon origin ↔ Mumbai edge, uncompressed (Caddy has no `encode`), never edge-cached (basic auth) |
| Every click takes seconds | synthetic endpoints take **1–6 s on-host**, with no network in the way | code/data shape (§0.2) |
| Parallel calls are worse | 4 concurrent calls took **~7 s each**, about the *sum* of their sequential times; `/v1/users/me/timezone` went from 6 ms alone to 7 s | handlers are `async def` but call blocking SQLAlchemy/psycopg, so each request freezes uvicorn's single event loop |
| Not the host | load 0.3, no swap, CPU credits in `unlimited` mode and rising | — |
| Not Postgres | the scope query executes in **106 ms** | — |

Caddy's own access log backs the event-loop finding:
- A 22-byte `favicon.ico` took 2 s while an overview request was running.
- A 19 KB CSS file finished at 09:17:38.7, the same instant a `/v1/economics` computation
  finished. The browser had already abandoned that request 3 s earlier.

### 0.2 Why the endpoints are slow

Every manager endpoint calls `aggregate.fetch_scope_sessions`, which runs
`manager.scope_sessions_in_range` (`config/queries/manager.yaml:200`).
- **It fetches everything.** For every session in the window it returns the whole `info`,
  `models`, `tools`, `files` and `otel` JSONB documents.
- **Python parses all of it.** psycopg then decodes 33k–73k JSON values per request.
- **Only a few numbers are used.** Of each document, the endpoints read a handful of
  numbers (§3.1).

cProfile inside `talium-aws-api_synth-1` shows JSON decoding at 60–75% of wall time:

| Endpoint (synthetic, 35 users) | Wall | Cursor (detoast + transfer) | Python |
|---|---|---|---|
| `org_overview` 15 d | 3.2 s | 0.8 s | 2.3 s |
| `economics` org 15 d | 3.8 s | 1.0 s | 2.8 s |
| `teams` 60 d | 6.6 s | 1.9 s | 4.7 s |
| `teams/development` 60 d | 4.8 s | 1.4 s | 3.4 s |

A 60-day window is 14,389 sessions and ~60 MB of JSON text. Two things make it worse:
- **Repeated fetches.** `org_overview` fetches twice (current and previous window).
  `economics` fetches three times (current, period-to-date for the burn-up, plus the window),
  although it only ever reads cost, datasource and `started_at`.
- **Real data carries more JSON.** In real (`econ`) data `otel` averages **7.8 KB compressed
  per session** (max 113 KB; ~152 ledger requests each). That is 2.5× `info`. Synthetic
  `otel` is ~5 bytes, so the synthetic numbers above *understate* what real data will cost
  at the same session count.

### 0.3 What the fix buys (probe, 60 d, `econ_synth`, 14,389 sessions, fetch only)

| Fetch | Time | Speed-up |
|---|---|---|
| today: full JSON columns | 5.2 s | — |
| Phase C: the same rows, fields extracted in SQL | 1.8 s | 2.8× |
| Phase E: the same rows, promoted columns | **0.2 s** | 25× |
| Phase D: model mix as one SQL aggregate | 0.18 s | replaces a JSON walk over every session |

**Why C is only 2.8×.** In Phase C, Postgres still has to decompress (detoast) every `info`
and `otel` document to pull the fields out: 445 ms just for two fields. Phase E is the real
fix. Phase C gives most of the win without a migration and fixes the row contract that
Phase E only re-sources.

**The probe matched today's Python exactly:**
- On real `econ` (137 sessions), every field.
- On synthetic, every field except 12 `session_type`s, which is the synthetic `turns` bug
  (§1, Phase 0).
- The model split was identical on both, once the SQL's NULL handling was fixed. The first
  draft silently dropped JSONL-only sessions; the §5.1 parity tests exist to catch exactly
  that kind of bug.

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| D1 | Route handlers become plain `def`; the API runs **2 uvicorn workers** per container | FastAPI runs `def` handlers in a thread pool, so DB waits overlap. CPU-bound aggregation is still limited by the GIL (one core per process), so real parallelism needs processes; 2 matches the 2 vCPUs. |
| D2 | Caddy compresses (`encode zstd gzip`) on both sites | 756 KB → ~200 KB on the slow Oregon–Mumbai leg. Cloudflare accepts compressed origin responses and re-encodes for the browser as needed. |
| D3 | One *row contract* of scalar attributes, named exactly as the Phase E columns | Phase C writes the Python against these names, sourced from SQL expressions. Phase E swaps the expressions for columns and the Python does not change again. |
| D4 | The per-session recoverable **cap stays at read time** (`_session_buckets`) | The cap is `min(buckets, cost_effective_usd_micros)`, which mixes a fold-owned value with a true-up-owned column. Two writers, so there is no single write-time point where both are current. |
| D5 | Column ownership follows the writer (`schema/session_info.py`'s existing rule) | Fold-owned (derived from `info`/`files` in `persist_session_info`): `recoverable_*`, `reasoning_tokens`, `has_files`, `session_type`. True-up-owned (derived from the cost block in `_write_cost_block`): `otel_vendor_cost_usd_micros`. |
| D6 | One Python definition per derived value, in a new `server/storage/session_scalars.py` | Used by the writers (E), the synthetic loader (E), `precedence` / `aggregate` for rows that still carry JSON, and the parity tests. The SQL expressions (C) and the backfill (E) are *mirrors* of it, pinned by parity tests (§5.1). |
| D7 | `title` and the four channel costs are extracted in SQL only for person/PR scopes and **never promoted** | They are read over one person's sessions or one PR's linked sessions (bounded row counts), never org-wide. |
| D8 | Segment `turns` is a **count** (`worker/segments/beats.py:418` writes `turns_in_span`, and `SessionScreen.tsx:210` renders `{seg.turns} turns`) | The synthetic generator writes the list of turn indices (`content.py:342`). Fix the generator and the loader. Every SQL and Python reader treats an array as its length, so already-loaded data is read sensibly. |
| D9 | The response contract (OpenAPI) does not change | This is a pure performance change. `test_openapi_contract.py` must pass untouched, and §5.2 diffs actual responses before and after. |

**User-visible change:** on synthetic data only, the dominant session type is now the segment
with the most turns. Today it is the segment whose turn-index list sorts highest, which is in
practice the last segment. 12 of 14,389 sessions change type. Real data is unaffected: the
probe found zero differences there.

---

## 2. Phase A: sync handlers and multiple workers (PR 1)

### 2.1 `src/server/api/app.py`

- **Change all 27 `async def` route handlers to `def`**, from `healthz` through
  `get_economics`. Nothing in a handler body awaits. Every body is `with session_scope() as
  s: ... return service.x(...)`, so the change is the keyword only.
- **Keep async:**
  - the `log_requests` middleware (`app.py:94`). It awaits `call_next`, which dispatches a
    sync endpoint to the thread pool, so it never blocks the loop.
  - the `handle_api_error` exception handler (`app.py:107`), which does no I/O.
- **Leave `StaticFiles` mounted as is.** It streams files asynchronously, and once the loop
  is no longer blocked the JS/CSS stops queuing behind API work.
- **Thread pool and DB pool.**
  - Starlette's default thread limit is 40 per process. The DB pool is 5 + 5 overflow per
    process (`config/storage.yaml`).
  - Once more than 10 requests are in flight, the extras wait up to `pool_timeout_s: 30`
    for a connection. That is acceptable for this traffic, so neither limit changes.
- **Add a docstring line** on `create_app` stating the rule: "route handlers are sync; only
  middleware and exception handlers are `async`".

### 2.2 `src/server/api/cli_serve_api.py`: worker processes

uvicorn only forks workers from an **import string**, not an app object, so the entry point
becomes a factory:

```python
_CONFIG_ENV = "TALIUM_API_CONFIG"

def app_from_env() -> FastAPI:
    """uvicorn factory: each worker process builds its own app, engine, pool and query catalog
    from the config path main() exported (a forked worker cannot inherit the parent's engine)."""
    config = load_config(os.environ.get(_CONFIG_ENV, "config/api.yaml"))
    return create_app(config, load_storage_config(config.storage_config))

def main() -> None:
    ...                                         # existing --config / --port
    parser.add_argument("--workers", type=int, default=None,
                        help="override config's http.workers")
    args = parser.parse_args()
    config = load_config(args.config)           # fail fast in the parent on a bad config
    os.environ[_CONFIG_ENV] = args.config
    uvicorn.run("server.api.cli_serve_api:app_from_env", factory=True,
                host=config.http.host, port=args.port or config.http.port,
                workers=args.workers or config.http.workers)
```

- **Config.** `src/server/api/config.py`: `HttpConfig.workers: int = 1`. Set
  `config/api.yaml` `http.workers: 2`.
- **Compose needs no change.** `api` and `api_synth` in `docker/docker-compose.aws.yml`
  already pass `--config config/api.yaml`, and `TALIUM_DB_DSN` is inherited by the forked
  workers.
- **Safe to fork.** No module-level cache exists in `src/server/api/` (checked: no
  `lru_cache`, no mutable module globals besides the constant resolver maps in
  `precedence.py`).
- **Connection budget.** 2 containers × 2 workers × 10 connections = 40, well under Postgres'
  default `max_connections` of 100, alongside worker and ingest.
- **Memory.** `api_synth` idles at 286 MB today, mostly from JSON rows. Watch `docker stats`
  after the deploy; Phases C and E shrink it.

### 2.3 Tests

- **New `tests/server/api/test_app_handlers.py`.** Walk `create_app(...).routes`. For every
  `fastapi.routing.APIRoute`, assert
  `not inspect.iscoroutinefunction(route.endpoint)`, with the route path in the message. This
  is the regression guard: someone adding an `async def` handler that touches the DB
  re-introduces the stall.
- **Same file:** `app_from_env` builds an app from `TALIUM_API_CONFIG` pointing at
  `config/api.yaml`, with the test DSN.
- **Existing suites must pass unchanged:** `tests/server/api/test_app*.py`, since TestClient
  drives sync handlers the same way.

### 2.4 Acceptance, measured on the prod host

Commit the probe used for §0 as `utils/api_latency.py`:
- **Arguments:** base URL, viewer, window. It runs against the containers directly, e.g.
  `docker exec -i talium-aws-api-1 python - < utils/api_latency.py`.
- **Two modes:** sequential and concurrent.
- **Output:** one line per call (ms, KB, path).

Targets:
- `/v1/users/me/timezone` fired alongside `org/overview` returns in **< 200 ms**. Today it
  takes 2.7–7 s.
- Org screen's four parallel calls finish in **≤ 1.3 × (the slowest of the three heavy calls
  run alone)** on synthetic 15 d. Today they take the sum.

---

## 3. Phase B: Caddy compression (PR 1)

### 3.1 `deploy/caddy/Caddyfile.aws`

Add `encode zstd gzip` as the first line of **both** site blocks, `{$ECON_SITE}` and
`{$SYNTH_SITE}`:

```
{$ECON_SITE} {
	encode zstd gzip
	log { ... }
	...
}
```

- **Position doesn't matter to Caddy.** Its directive order places `encode` before
  `reverse_proxy`; first line is only for readability.
- **Defaults are fine.** The default `minimum_length` is 512 bytes, and the default MIME
  matcher covers `application/javascript`, `text/css`, `text/html` and `application/json`.
  API JSON gets compressed too, which is harmless and helps larger responses.
- **Update the header comment:** "Routes, and what is absent on purpose" gains "responses are
  compressed at origin; Cloudflare re-encodes as needed".
- **Same change in `deploy/caddy/Caddyfile`** (the non-AWS stack), for parity.

### 3.2 Deploy gotcha

The Caddyfile is a **single-file bind mount** (`../deploy/caddy/Caddyfile.aws:/etc/caddy/Caddyfile:ro`).
`git pull` replaces the file with a new inode, and a running container keeps seeing the old
one. `aws_up.sh`'s `up -d --wait` does not recreate an unchanged Caddy container, so
`caddy reload` would reload stale content. Run:

```bash
docker compose --env-file .env.aws -f docker/docker-compose.aws.yml restart caddy
```

Add this line to `docs/deployment.md` §0 ("after pulling a Caddyfile change").

### 3.3 Acceptance

After a page load, the Caddy access log shows:
- `/assets/index-*.js` with `resp_headers.Content-Encoding` set;
- `size` about 200–230 KB, down from 756,195.

```bash
sudo docker logs --since 10m talium-aws-caddy-1 2>&1 | grep 'assets/index' | grep -o '"size": [0-9]*\|Content-Encoding[^]]*'
```

**Expected effect:** the transfer is throughput-bound, so roughly a 3–4× shorter JS download.
It is **not** a full fix; the Oregon↔Mumbai leg stays slow (§8).

---

## 4. Phases 0, C, D: extract in SQL, per-endpoint queries (PR 2)

### 4.0 Phase 0: synthetic `turns` is a count

- **Generator.** `econ-forecast/src/forecast/synthetic/org/content.py:342`: change
  `"turns": [t["index"] for t in group]` to `"turns": len(group)`. Update any test in
  `econ-forecast/tests/` that asserts on the list shape; grep for `"turns"`.
- **Loader.** `src/server/storage/synthetic_load.py`, in the `session_info_rows` loop
  (`:302`): normalise already-generated artifacts, so the copy on prod needs no regenerating:
  ```python
  for seg in (row.get("info") or {}).get("segments") or []:
      if isinstance(seg.get("turns"), list):
          seg["turns"] = len(seg["turns"])
  ```
  Comment it with the D8 rationale.
- **Test:** `tests/server/storage/test_synthetic_load_unit.py` gains a case where a list
  `turns` loads as its length.

### 4.1 New module `src/server/storage/session_scalars.py` (D6)

This is the single Python definition of each derived value. It is pure functions with no DB
access, importable from `api`, `worker` and `storage`. `api` already imports
`server.storage`; `storage` must not import `api`.

```python
"""Per-session scalars derived from session_info's JSON (api-latency plan D3/D6). THE
definition of each value: the SQL mirrors in config/queries/*.yaml (Phase C) and the backfill
in migration <rev> (Phase E) must agree with these; tests/server/storage/test_session_scalars.py
pins that."""

def priced_total(block: dict | None, key: str) -> int:
    # moved verbatim from aggregate._priced_total: None/missing/unpriced -> 0
def recoverable_buckets(info: dict | None) -> dict[str, int]:
    # {"resume": priced_total(info.get("resume_cost"), "total_recoverable_usd_micros"),
    #  "cache_bust": priced_total(info.get("cache_busts"), "total_usd_micros"),
    #  "interrupted": priced_total(info.get("interrupted"), "total_usd_micros")}  -- UNCAPPED (D4)
def reasoning_tokens(info: dict | None) -> int | None
def has_files(files: dict | None) -> bool                    # bool(files or {})
def segment_turns(seg: dict) -> int                          # number -> int; list -> len (D8); else 0
def dominant_segment_label(segments: list | None) -> str | None
    # moved from aggregate.py: max(segments, key=segment_turns).get("label"); first max wins
def otel_vendor_cost(otel: dict | None) -> int | None
    # moved verbatim from precedence._otel_vendor_cost's body
def fold_scalars(info: dict | None, files: dict | None) -> dict   # the fold-owned column values (E)
```

- **Moved functions stay importable.** `aggregate._priced_total` and
  `aggregate.dominant_segment_label` become imports from `session_scalars`, so existing
  imports and tests keep working.
- **`precedence._otel_vendor_cost(row)`** delegates to `session_scalars.otel_vendor_cost`
  (§4.3).

### 4.2 The row contract (D3)

Every manager fetch returns rows with these scalar attributes instead of JSON. The names are
the Phase E column names.

| Attribute | Type | Definition (`session_scalars`) | Read by |
|---|---|---|---|
| `otel_vendor_cost_usd_micros` | bigint \| null | `otel_vendor_cost(otel)` | `precedence` cost rung `otel_vendor` |
| `recoverable_resume_usd_micros` | bigint | `recoverable_buckets(info)["resume"]` | `aggregate.recoverable` |
| `recoverable_cache_bust_usd_micros` | bigint | `…["cache_bust"]` | same |
| `recoverable_interrupted_usd_micros` | bigint | `…["interrupted"]` | same |
| `reasoning_tokens` | bigint \| null (null = never estimated) | `reasoning_tokens(info)` | `aggregate.reasoning_share` |
| `has_files` | bool | `has_files(files)` | `aggregate.sessions_with_no_output_pct` |
| `session_type` | text \| null | `dominant_segment_label(info.segments)` | `aggregate.session_type_counts` |
| `title` *(person/PR only, D7)* | text \| null | `info.title` | `_person_recent_sessions` |
| `channel_priced` *(person/PR only)* | bool | `channel_cost` and `carry` are both objects | `aggregate.channel_totals` |
| `channel_carry_usd_micros`, `channel_recache_usd_micros`, `channel_output_usd_micros`, `channel_input_usd_micros` *(person/PR only)* | bigint | `carry.carry_usd_micros`, `channel_cost.{recache,output,input}.usd_micros`, missing → 0 | same |

### 4.3 SQL expressions (Phase C) and Python changes

**SQL fragments.** These are verbatim what the probe ran, with the NULL fix. Every cast is
guarded by `jsonb_typeof`, so an unexpected shape yields NULL/0 instead of a
`DataError`/500 for the whole endpoint. That is the failure the unguarded probe hit on list
`turns`.

```sql
-- otel_vendor_cost_usd_micros  (mirror of session_scalars.otel_vendor_cost)
CASE WHEN jsonb_typeof(i.otel->'cost_basis') = 'object' AND i.otel->'cost_basis' <> '{}'::jsonb
      AND COALESCE(CASE WHEN jsonb_typeof(i.otel->'cost_basis'->'estimated') = 'number'
                        THEN (i.otel->'cost_basis'->>'estimated')::numeric END, 0) = 0
      AND COALESCE(CASE WHEN jsonb_typeof(i.otel->'cost_basis'->'unknown') = 'number'
                        THEN (i.otel->'cost_basis'->>'unknown')::numeric END, 0) = 0
      AND jsonb_typeof(i.otel->'cost_usd_micros') = 'number'
     THEN (i.otel->>'cost_usd_micros')::bigint END                  AS otel_vendor_cost_usd_micros,
-- recoverable_* (mirror of priced_total; same shape for cache_busts/interrupted with total_usd_micros)
CASE WHEN jsonb_typeof(i.info->'resume_cost'->'total_recoverable_usd_micros') = 'number'
     THEN (i.info->'resume_cost'->>'total_recoverable_usd_micros')::bigint ELSE 0 END
                                                                     AS recoverable_resume_usd_micros,
CASE WHEN jsonb_typeof(i.info->'reasoning_tokens') = 'number'
     THEN (i.info->>'reasoning_tokens')::bigint END                 AS reasoning_tokens,
(i.files IS NOT NULL AND i.files <> '{}'::jsonb)                   AS has_files,
st.session_type
...
LEFT JOIN LATERAL (
  SELECT e.seg->>'label' AS session_type
  FROM jsonb_array_elements(CASE WHEN jsonb_typeof(i.info->'segments') = 'array'
                                 THEN i.info->'segments' ELSE '[]'::jsonb END)
       WITH ORDINALITY AS e(seg, ord)
  ORDER BY CASE jsonb_typeof(e.seg->'turns')
             WHEN 'number' THEN (e.seg->>'turns')::numeric
             WHEN 'array'  THEN jsonb_array_length(e.seg->'turns') ELSE 0 END DESC,
           e.ord                                   -- first max wins, like Python's max()
  LIMIT 1) st ON true
```

**Person/PR-only fragments.**
- `i.info->>'title' AS title`
- `(jsonb_typeof(i.info->'channel_cost') = 'object' AND jsonb_typeof(i.info->'carry') = 'object') AS channel_priced`
- the four channel costs, each as a guarded `number` extraction defaulting to 0, e.g.
  `CASE WHEN jsonb_typeof(i.info->'channel_cost'->'recache'->'usd_micros') = 'number' THEN (…)::bigint ELSE 0 END AS channel_recache_usd_micros`
- the carry: `i.info->'carry'->'carry_usd_micros'`

**Python changes in `src/server/api/manager/aggregate.py`.** Each function now reads the
contract attributes, not JSON:
- **`_session_buckets(row)`** (was `(info, cost)`): buckets come from the three
  `recoverable_*` attributes. The cap stays at `row.cost_effective_usd_micros` (D4). The
  proportional scaling and remainder logic are unchanged. `recoverable(rows)` calls
  `_session_buckets(r)`.
- **`reasoning_share(rows)`:** `rt = r.reasoning_tokens`. The `seen` / None semantics are
  unchanged.
- **`sessions_with_no_output_pct(rows)`:** `not r.has_files`.
- **`session_type_counts(rows)`:** `st = r.session_type`.
- **`channel_totals(rows)`:** `if not r.channel_priced: complete = False; continue`, then sums
  the four `channel_*` attributes. Same `None`/`complete` semantics.
- **`model_usage_breakdown(split: dict)`:** now takes a split, not rows. Callers:
  - org overview passes the SQL split (§4.4);
  - `single_user/service.py:1005` becomes
    `model_usage_breakdown(manager_aggregate.model_split([row]))`.

  `model_split(rows)` stays for single-session, PR and single-user callers, whose rows still
  carry `otel`/`models`.
- **New `model_split_from_rows(rows)`:** turns `scope_model_split` result rows into the same
  `{model: {"tokens", "cost_usd_micros"}}` dict `model_split` returns.

**`src/server/api/precedence.py`:**
- **`_otel_vendor_cost(row)`:** reads the scalar when the row carries it, otherwise the dict:
  ```python
  v = getattr(row, "otel_vendor_cost_usd_micros", _ABSENT)
  if v is not _ABSENT:
      return v
  return session_scalars.otel_vendor_cost(getattr(row, "otel", None))
  ```
  The dict path remains for `dashboard.session_detail` and the other single-session reads that
  still select `otel`. Both paths share one definition. Update the module docstring's "session_row is anything with these
  attributes" paragraph to name the scalar.

**`src/server/api/manager/service.py` `_person_recent_sessions`:** reads `r.title` and
`r.reasoning_tokens` instead of `(r.info or {}).get(...)`.

**Every query that feeds these functions must supply the contract.** Otherwise the change
raises `AttributeError`, which is loud; that is intended.

| Query | Change |
|---|---|
| `manager.scope_sessions_in_range` | JSON columns replaced by the fragments (then split in D) |
| `manager.repo_sessions_in_range` | same treatment (project detail, economics repo scope) |
| `manager.sessions_by_ids` (PR/feature detail, bounded N) | **add** the vendor, recoverable and person/PR fragments; **keep** `i.models`, `i.otel` (still needed by `model_split`/`model_fit` for one PR's sessions) |
| `dashboard.sessions_in_range` (single-user overview) | **add** the vendor and recoverable fragments so `recoverable()` and the cost chain have one implementation. Leave its other JSON columns alone (§8, `TODO(single-user-slim-fetch)`). |

### 4.4 Phase D: per-endpoint queries

Replace `scope_sessions_in_range` with three row shapes plus one aggregate. They are named by
shape rather than by endpoint, so eight endpoints don't need eight queries.
`aggregate.fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="session")` maps
`shape ∈ {"cost", "session", "person"}` to the query names.

All three row shapes keep the same `WHERE` and `ORDER BY s.started_at`. The sort no longer
spills to disk once rows are narrow; it spilled 19.7 MB at `work_mem` 4 MB with JSON rows.

| Query | Columns | Used by |
|---|---|---|
| `manager.scope_cost_facts` | `s.session_id, s.user_id, s.started_at, s.last_event_at, s.ended_at, s.parent_session_id, i.tokens_total, i.cost_effective_usd_micros, i.datasource, i.repo_id, i.repo_id_provenance` + `otel_vendor_cost_usd_micros` | `teams_table`, `projects_table`, `economics` (window and period-to-date), `org_overview`'s previous window, `project_detail` |
| `manager.scope_session_facts` | cost facts + `s.agent` + `recoverable_*` ×3 + `reasoning_tokens` + `has_files` + `session_type` | `org_overview`, `team_detail`, `org_usage`, `my_tips` |
| `manager.person_session_facts` | session facts + `title`, `channel_priced`, `channel_*` ×4 | `person_detail` |
| `manager.repo_cost_facts` | cost facts filtered by `i.repo_id = :repo_id` (was `repo_sessions_in_range`) | `project_detail`, `economics` repo scope |
| `manager.scope_model_split` | the aggregate below | `org_overview` `highlights.model_mix` |

**Column check against each endpoint's actual reads** (from §0.2's code walk):
- **`teams_table`:** `_team_summary` → cost chain only; `last_active_at` → `last_event_at`;
  grouping → `user_id`.
- **`projects_table`:**
  - `repo_rollup_canonical` → `repo_id`, `repo_id_provenance`, `user_id`,
    `parent_session_id`, `cost_effective_usd_micros`, `tokens_total`
  - `repo_contributors` → the same keys
  - last activity → `last_event_at`
- **`economics`:** `datasource`, cost chain, `started_at`, `cost_effective_usd_micros` (the
  daily burn reads the column directly, not the chain; `service.py:1227`).
- **`project_detail`:** `datasource`, cost chain, `user_id`.
- **`org_usage`:**
  - `agent`, `user_id`, `parent_session_id`
  - `started_at` / `ended_at` / `last_event_at` for durations
  - `session_type`, `has_files`, `reasoning_tokens`
  - cost chain, `tokens_total`
- **`org_overview`:**
  - everything `org_usage` needs minus the durations
  - `recoverable_*`, `agent` (`spend_by_agent`), and `repo_*` (`repo_rollup_canonical`)
- **`team_detail` / `my_tips`:** cost chain, `recoverable_*`, `datasource`, `user_id`.

**`manager.scope_model_split`.** Mirrors `aggregate.model_split`: the ledger per request when
`otel.requests` is a non-empty array, else the JSONL `models` rollup, never both for one
session.

```sql
WITH scope AS (
  SELECT i.otel, i.models
  FROM sessions s JOIN session_info i ON i.session_id = s.session_id
  WHERE s.user_id = ANY(:user_ids) AND s.started_at < :to
    AND COALESCE(s.ended_at, s.last_event_at) >= :from)
SELECT model, sum(tokens)::bigint AS tokens, sum(cost)::bigint AS cost_usd_micros FROM (
  SELECT COALESCE(r->>'model', 'unknown') AS model,
         <sum of input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, each guarded>
           AS tokens,
         COALESCE(<cost_usd_micros guarded>, <cost_est_usd_micros guarded>, 0) AS cost
  FROM scope, jsonb_array_elements(scope.otel->'requests') r
  WHERE jsonb_typeof(scope.otel->'requests') = 'array'
  UNION ALL
  SELECT m.key, <sum of input, output, cache_read, cache_write, each guarded>, 0
  FROM scope, jsonb_each(CASE WHEN jsonb_typeof(scope.models) = 'object'
                              THEN scope.models ELSE '{}'::jsonb END) m
  -- NOT (… AND …) is NULL when otel has no 'requests' key; COALESCE keeps those sessions.
  -- Dropping them was the probe's first bug.
  WHERE COALESCE(jsonb_typeof(scope.otel->'requests') <> 'array'
                 OR scope.otel->'requests' = '[]'::jsonb, true)
) x GROUP BY model;
```

`model_split`'s `req.get("cost_usd_micros")` falls back to `cost_est_usd_micros` only when
the vendor cost is `None`, and the SQL's `COALESCE` order is the same. The `org_overview`
call site becomes
`aggregate.model_usage_breakdown(aggregate.model_split_from_rows(s.execute(catalog.get("manager", "scope_model_split"), params).all()))`.

This aggregate still detoasts `otel`, so on real data it is the one remaining JSON-heavy read.
It is computed in Postgres and returns about 8 rows instead of the whole ledger. Promoting a
per-session per-model table is option 4 territory (§8).

**Service call-site changes (`src/server/api/manager/service.py`):**

| Function | Before | After |
|---|---|---|
| `org_overview` (`:175`) | `fetch_scope_sessions` × 2 (window and previous) | window `shape="session"`, previous `shape="cost"`, plus `scope_model_split` |
| `teams_table` (`:321`) | full | `shape="cost"` |
| `projects_table` (`:356`) | full | `shape="cost"` |
| `team_detail` (`:447`) | full | `shape="session"` |
| `person_detail` (`:616`) | full | `shape="person"` |
| `project_detail` (`:701`) | `repo_sessions_in_range` | `repo_cost_facts` |
| `economics` (`:1145`) | full × 2, or `repo_sessions_in_range` × 2 | `shape="cost"` / `repo_cost_facts` for both window and period |
| `my_tips` (`:1283`) | full | `shape="session"` |
| `org_usage` (`:1361`) | full | `shape="session"` |

**Query hygiene:**
- Delete `scope_sessions_in_range` and `repo_sessions_in_range` from `manager.yaml` once no
  caller remains. Grep `src/` and `tests/` for both names.
- Update the comment block above them (`manager.yaml:195–199`) to describe the three shapes
  and the D3 contract.

---

## 5. Verification (PRs 2 and 3)

### 5.1 Scalar parity tests: new `tests/server/storage/test_session_scalars.py` (`requires_db`)

These tests exist to pin every mirror to the Python definition.

- **Setup.** Insert `session_info` rows that together cover every shape seen or possible:
  - `otel.cost_basis`: `{}`, missing, estimated > 0, unknown > 0, and all-vendor with
    `cost_usd_micros` null;
  - `resume_cost`, `cache_busts`, `interrupted`: missing, JSON `null`, total null, total 0,
    total > 0;
  - `reasoning_tokens`: missing, `null`, 0, > 0;
  - `files`: null, `{}`, non-empty;
  - `segments`: missing, `[]`, one segment, a tie on `turns`, list-shaped `turns`, `turns`
    missing, `label` missing;
  - `channel_cost` / `carry`: each missing, `null`, and partial.
- **For each row, assert** that the SQL fragments (Phase C: run the actual catalog query;
  Phase E: read the backfilled columns) equal the `session_scalars` functions applied to the
  same JSON.
- **Model split.** A mix of ledger, JSONL-only, no-`requests`-key and empty-`requests`
  sessions: `scope_model_split` equals `model_split` over the full rows.
- **Pin the Python definitions directly.** Unit tests (no DB) for each `session_scalars`
  function, including `segment_turns` on list and number, and first-max-wins.

### 5.2 Response parity across the refactor

A new dev tool, `utils/api_parity.py`, pinned to a base URL and viewer:
- **What it calls.** Every manager GET endpoint, over three windows (7 d, 30 d, 60 d), for:
  - the org;
  - every team, the first 3 people and every project, discovered via `/v1/scopes`;
  - one PR and one feature.
- **What it writes.** Sorted-key JSON to a directory.
- **Workflow:**
  1. On the pre-change commit, snapshot a local stack loaded with **both** datasets: a
     `pg_dump` of prod `econ` restored locally (read-only dump) and `econ_synth`.
  2. Snapshot again after PR 2 and after PR 3.
  3. `diff -r` must be empty. The only allowed difference is `session_types` on synthetic,
     from Phase 0. Record that exact diff in the PR description.

### 5.3 Existing suites

These must pass with at most fixture-shape updates, never by changing asserted values:
- `tests/server/api/`: `test_manager_*`, `test_app_*`, `test_precedence.py`,
  `test_service_overview.py`, `test_synthetic_smoke.py` (against a `*_synth` DSN), and
  `test_openapi_contract.py` (D9: `openapi.json` is not regenerated because nothing changes).
- **Fixture updates.** Test fixtures that build rows by hand, e.g. `SimpleNamespace` rows in
  `test_manager_aggregate.py`, gain the contract attributes. Grep for `info=` and `otel=` in
  `tests/server/api/`.

### 5.4 Performance acceptance

Measured with `utils/api_latency.py` on the prod host, synthetic, sequential:

| Endpoint | today | after PR 2 (target) | after PR 3 (target) |
|---|---|---|---|
| `org/overview` 15 d | 2.7 s | < 1.2 s | < 0.4 s |
| `economics` org 15 d | 3.3 s | < 1.0 s | < 0.3 s |
| `teams` 60 d | 5.5–6.1 s | < 1.0 s | < 0.4 s |
| `teams/development` 60 d | 3.7 s | < 1.5 s | < 0.5 s |

- **Where the targets come from:** the §0.3 fetch probe plus the non-JSON Python time the
  profile shows (~0.1–0.3 s).
- **If a target is missed,** re-profile with the §0 cProfile harness before changing
  anything else.

---

## 6. Phase E: promoted columns (PR 3)

### 6.1 Migration `src/server/storage/dbm/alembic/versions/<rev>_session_info_scalar_columns.py`

`down_revision = 'c4e8a2f1d7b3'` (the current head, `drop_allowances`).

**Upgrade:**

```python
op.add_column("session_info", sa.Column("otel_vendor_cost_usd_micros", sa.BigInteger(), nullable=True))
for c in ("recoverable_resume_usd_micros", "recoverable_cache_bust_usd_micros",
          "recoverable_interrupted_usd_micros"):
    op.add_column("session_info", sa.Column(c, sa.BigInteger(), nullable=False, server_default="0"))
op.add_column("session_info", sa.Column("reasoning_tokens", sa.BigInteger(), nullable=True))
op.add_column("session_info", sa.Column("has_files", sa.Boolean(), nullable=False,
                                         server_default=sa.false()))
op.add_column("session_info", sa.Column("session_type", sa.String(), nullable=True))
op.execute(_BACKFILL_SQL)
```

**Backfill.** `_BACKFILL_SQL` is one `UPDATE session_info i SET ... FROM (lateral
session_type) ...` using **exactly** the §4.3 fragments, with `i.` references to the row being
updated.
- It is pure SQL with no application imports, like every other migration here.
- Size: 14.8k rows on `econ_synth`, a few seconds; 153 rows on `econ`.
- It rewrites every row once, and autovacuum reclaims the old versions.

**Downgrade:** drop the seven columns.

**No new index.** Grouping stays in Python over narrow rows. `session_type` is only counted
over the already-filtered scope.

### 6.2 ORM: `src/server/storage/schema/session_info.py`

- **Add the seven mapped columns** with ownership comments in the file's existing style:
  - the fold-owned block next to `tokens_total` ("derived from `info`/`files` by
    `persist_session_info` via `session_scalars.fold_scalars`; never written by the
    true-up");
  - `otel_vendor_cost_usd_micros` next to `cost_effective_usd_micros` ("true-up's own
    column, written in `_write_cost_block`'s statement").
- **Regenerate the DDL fixture:**
  `python -m server.storage.cli_schema_dump --ddl > tests/fixtures/storage_ddl.sql`, per
  `test_schema_dump.py:21`.

### 6.3 Writers

- **`src/server/worker/persistence.py` `persist_session_info`:** after `row.info = info.info`,
  assign the fold-owned columns from `session_scalars.fold_scalars(info.info, info.files)`.
  Extend the comment block at `:56` to say these columns are the fold's and are derived, not
  stored in the dataclass. `fold_api.SessionInfo` does **not** change; derivation at persist
  is the single chokepoint for every fold path, `rollup_from_family` included.
- **`src/server/worker/otel_trueup.py` `_write_cost_block`** (`:701`): add
  `row.otel_vendor_cost_usd_micros = session_scalars.otel_vendor_cost(block)` next to
  `row.cost_effective_usd_micros`. The row-creation branch for OTel-only sessions needs
  nothing more: the server defaults cover the fold-owned columns, which are correct for a row
  with `info={}`.
- **`src/server/storage/synthetic_load.py`** (`:302` loop, after the Phase 0 normalisation):
  - `row.update(session_scalars.fold_scalars(row.get("info"), row.get("files")))`
  - `row["otel_vendor_cost_usd_micros"] = session_scalars.otel_vendor_cost(row.get("otel"))`

  `_upsert_table` already keeps only keys that are table columns (`:157`), so the new keys
  flow through.

### 6.4 Queries

In `scope_cost_facts`, `scope_session_facts`, `person_session_facts`, `repo_cost_facts`,
`sessions_by_ids` and `dashboard.sessions_in_range`:
- **Replace** each promoted fragment with its column, e.g. `i.otel_vendor_cost_usd_micros`,
  and drop the `LATERAL` join.
- **Keep** the person/PR-only fragments (`title`, `channel_*`) as expressions (D7).

No Python changes, which is the D3 payoff.

### 6.5 Tests

- **`test_session_scalars.py`** gains the backfill variant: run the migration on the edge-case
  rows, then compare columns to the functions (§5.1).
- **`tests/server/worker/test_graph_persistence.py`:** `persist_session_info` writes the
  fold-owned columns, and a second persist with different `info` updates them.
- **`tests/server/worker/test_otel_trueup.py`:** `_write_cost_block` writes
  `otel_vendor_cost_usd_micros` for all-vendor, mixed and empty `cost_basis`.
- **`tests/server/storage/test_synthetic_load_unit.py`:** loaded rows carry the columns.
- **New migration test** in the style of `tests/server/api/test_budgets_migration.py`:
  upgrade, check columns and backfilled values on seeded JSON, downgrade, check the columns
  are gone, upgrade again. Restore the schema in `finally`; that test's own history shows why.
- **`tests/server/storage/test_schema_dump.py`:** passes against the regenerated golden.

### 6.6 Deploy order (both writers must not run old code against the new columns)

A worker still on old code writing after the migration would leave the new columns at their
defaults. Nothing would error, but the numbers would be quietly wrong. So stop the writers
first, on the host in `~/code-econ`:

```bash
C="docker compose --env-file .env.aws -f docker/docker-compose.aws.yml"
$C stop collector ingest worker
git pull
utils/aws_up.sh                          # rebuilds; the api images now contain the migration
$C run --rm --no-deps api python -m server.storage.cli_db --config config/storage.yaml migrate
PW=$(sed -n 's/^POSTGRES_PASSWORD=//p' .env.aws)
$C run --rm --no-deps -e TALIUM_DB_DSN="postgresql+psycopg://postgres:${PW}@postgres:5432/econ_synth" \
   api python -m server.storage.cli_db --config config/storage.yaml migrate
$C up -d --wait                          # writers back on new code
```

`aws_up.sh` brings every service up before the migrate step runs. If that makes a worker
write in between, re-run `$C stop collector ingest worker` right after `aws_up.sh`, then
migrate, then `up -d`. Confirm during implementation whether `aws_up.sh` accepts a service
list; if it does, use it to build without starting the writers.

**Verify:**
- `select count(*) from session_info where session_type is null and info ? 'segments'` is ~0.
- `utils/api_latency.py` meets the §5.4 PR 3 column.

---

## 7. Rollout summary

| Step | On prod | Rollback |
|---|---|---|
| PR 1 | `git pull && utils/aws_up.sh`, then `restart caddy` (§3.2) | revert the commit; `restart caddy` |
| PR 2 | `git pull && utils/aws_up.sh` (no schema change) | revert |
| PR 3 | §6.6 | `cli_db` downgrade to `c4e8a2f1d7b3`, then revert. The columns are derived, so nothing is lost. |

After each PR, record the `utils/api_latency.py` numbers in this plan's status line.

---

## 8. Not in scope (follow-ups; each gets a source TODO or an item here)

- **Single-user overview fetch.** `single_user/service.py::_fetch_sessions_in_range` →
  `dashboard.sessions_in_range` still selects `turns`, `files`, `subagents`, `otel` and
  `models` for one user's window: peak hours, repo file stats and the tokens/active-time
  chains. It is per-user, so the row counts are smaller, but real `otel` is the biggest
  column. Add `TODO(single-user-slim-fetch)` at `_fetch_sessions_in_range` in PR 2, pointing
  here.
- **Option 4, per-user daily rollup table.** Would make 60-day org views read about
  35 × 60 rows. Only worth it if §5.4's PR 3 targets aren't met, or data grows 10×+.
- **A per-session per-model table,** so `scope_model_split` stops detoasting `otel`. Same
  trigger as option 4.
- **Edge delivery:**
  - let Cloudflare cache `/assets/*`, either by exempting hashed assets from basic auth (a
    product/security call: they contain app code, no data) or by moving to Cloudflare Access;
  - or move the origin nearer the users (ap-south-1 / eu-*);
  - separately, confirm or rule out a path-MTU black hole. The host has jumbo MTU 9001,
    `TcpExtTCPTimeouts` 1,510 of 1,512 retransmits, and the security group allows ICMP from
    one IP only. Take a `tcpdump -i ens5 port 8443` capture during a reload before touching
    the security group or `tcp_mtu_probing`.
- **`sessions_by_ids`** keeps its JSON columns (PR/feature detail, bounded N).
