# OTel Intake v1 — Implementation Plan

**Status:** Executed 2026-08-04 — Phases 0/A–F complete, `pytest tests/` green (1081).
Module doc: `docs/modules/otel-intake.md`. Deltas found during execution are recorded inline
in the design doc (§4.1, §4.3, §4.4, §4.6, §9), not here; the three that changed a contract
were the fifth table (`otel_watermarks`), the cost block moving to its own `session_info.otel`
column, and the normalizer gaining `assistant_message.provider_request_id` — the JSONL side of
the join key, which did not exist.
**Primary spec:** `docs/design/2026-08-04-otel-intake-design.md` — the authority for this work
(receiver §4.1–4.2, tables §4.3, true-up §4.4, auth §4.5, vendor mapping §4.6, decisions
D-OT1…D-OT6, phasing §9). This plan only sequences it.
**Depends on (already implemented):**
- `src/server/storage/` — `Base`, `schema/`, `session_scope`, `QueryCatalog`, `dbm/` (Alembic),
  `cli_db.py`.
- `src/server/ingest/` — FastAPI app (`app.py` / `create_app`), config pattern, msging srv mount.
- `src/server/worker/` — `dispatch.py`, `rollup.py` (mirror its timer pattern), `cli_work.py`.
- `src/common/msging/srv/auth.py` — the bearer-key hash/issue/verify pattern to mirror (not import).
**Context specs (read for boundaries, do not modify blindly):**
- `docs/design/system-architecture.md` §6.4 (thin ingest), §8.1–8.3 (worker archetypes,
  transactional progress), §10.2 (storage constraints), §13 (content-free logs), §18.1 (auth).
- `docs/plan/2026-08-01-user-dashboard-implementation.md` P0 (pricing engine — a **seam**, not a
  dependency: see Phase 5 estimator note).
**Governance:** root `CLAUDE.md` — CLI naming (`cli_{function}.py`), structured JSON logging,
pytest, TODO markers, requirements.txt updated immediately, no vendor-AI references in code/commits.
**Code to be created:** `src/server/ingest/otlp/`, `src/server/worker/otel_trueup.py`,
3+1 tables in `src/server/storage/schema/`, `config/otel/{claude_code,gen_ai}.yaml`,
`config/queries/otel.yaml`, config extensions in `config/ingest.yaml` + `config/worker.yaml`,
`tests/fixtures/otel/`, `docs/modules/otel-intake.md` enrollment section.

---

## 0. Scope, sequencing, prerequisites

**Delivers (design §9 v1):** vendor-blind OTLP/HTTP receiver (metrics + mapped request events),
mapping configs for `claude_code` + `gen_ai`, the three `otel_*` tables with vendor-scoped keys,
org-token auth, true-up worker with provenance, end-to-end replay test against the recorded dump.

**Out of scope (v1.1+, per design §9):** `gemini_cli.yaml` / `codex.yaml` mappings, org-gateway
deployment guide, `max_series_per_org` guard, usage-reconciler drift check, full logs firehose.

**Sequencing:** Schema → Mapping configs → Auth → Receiver → True-up → End-to-end.
Do not start a phase before its predecessor's tests are green.

**Prerequisites before writing code:**
1. Read the design doc in full, especially §4.3 (dedup keys are the idempotency contract) and
   §4.6 (canonical-vs-residue rule).
2. Test DB via `TALIUM_TEST_DSN` (no SQLite — JSONB and `ON CONFLICT` are load-bearing).
3. **Fixtures with anonymization:** source material is `~/.claude/otel-local/data/
   claude-code-{metrics,logs}.jsonl` (raw OTLP JSON). Copy a trimmed sample into
   `tests/fixtures/otel/` **after rewriting** `user.email`, `user.account_uuid`,
   `user.account_id`, `organization.id`, `user.id`, and `process.owner` to synthetic values
   (secret hygiene; these are real identifiers). Keep ≥1 session with: multiple export
   intervals of the same series, a `query_source` background call, and the full event-name
   zoo (so filter drop-counts are testable). Record a second tiny fixture with a simulated
   counter reset (edited `start_unix_nano`) — the epoch fold test needs it.
4. New dependency: `opentelemetry-proto` (protobuf bindings for OTLP; widely used, Apache-2.0,
   maintained by the OTel org). Transitively `protobuf`. Pin both in `requirements.txt`
   immediately. Nothing else — no OTel SDK, no collector libs.

---

## 1. Phase A — Schema (design §4.3)

**Goal:** four tables land in the storage module; Alembic migration reviewed via `--sql` before
apply; `Base.metadata` picks them up.

**Files:**
- `src/server/storage/schema/otel_series.py` — `OtelSeries`: `series_id` text PK
  (sha256 over vendor + metric_name + canonically-sorted attrs — hashing lives in the
  receiver, the model just stores), `vendor`, `metric_name`, `metric_kind`, `temporality`,
  `org_id`, `session_id` (nullable, indexed), `user_key` (nullable), `attrs` JSONB,
  `first_seen_at`. Index `(vendor, session_id)`.
- `src/server/storage/schema/otel_metric_points.py` — `OtelMetricPoint`: composite PK
  `(series_id, time_unix_nano)`, `start_unix_nano`, `value` numeric, `received_at`
  (indexed — prune + watermark key). No FK constraint enforcement debate: plain FK, no
  cascade (prune handles points; series rows are tiny and kept).
- `src/server/storage/schema/otel_api_requests.py` — `OtelApiRequest`: composite PK
  `(vendor, request_id)`, columns exactly per design §4.3 (nullable cost/cache/duration),
  `attrs` JSONB, `received_at` (indexed). Index `(vendor, session_id)`.
- `src/server/storage/schema/org_tokens.py` — `OrgToken`: `token_hash` text PK, `org_id`,
  `descriptor`, `created_at`, `revoked` bool. Mirrors the producers-table key pattern.
- ~~Alembic revision via `dbm` operations~~ — **not done, deliberately (recorded
  2026-08-04).** The repo is still in storage.md §6's **phase 1, recreate era**:
  `dbm/alembic/versions/` is empty, `init` builds from models, and no service is running a
  migration chain. Cutting a revision here would produce `0001` = *the entire schema*, which
  starts the migration era for the whole project — a project-wide transition, not this
  feature's to make. The §10.2 rule ("what runs is what was reviewed") is honoured the way
  phase 1 honours it: DDL reviewed via `cli_schema_dump --ddl`, pinned by the
  `tests/fixtures/storage_ddl.sql` golden, and `init` builds from the same models.
  **Open for the reviewer:** if the migration era should start now, that is a separate
  decision and a separate change.

**Tests (`tests/server/storage/`):** model round-trip incl. JSONB attrs; composite-PK
insert-if-absent (`ON CONFLICT DO NOTHING` inserts zero on replay — the idempotency
contract); migration up from empty on the test DB.

**Gate:** migration reviewed + applied; replay-insert test green. **Stop for review here**
(schema is the hardest thing to change later).

---

## 2. Phase B — Vendor mapping configs + loader (design §4.6)

**Goal:** the mapping YAML contract exists as typed config; receiver and fold consume only
this — no vendor literal anywhere else in code.

**Files:**
- `config/otel/claude_code.yaml` — exactly the design §4.6 skeleton (detect rules, identity,
  events.accept/request_event/fields, measures).
- `config/otel/gen_ai.yaml` — GenAI semconv pseudo-vendor: `gen_ai.client.token.usage`
  (histogram) → tokens_input/tokens_output via `gen_ai.token.type` label; identity attrs
  per semconv; empty `events.accept` v1 (semconv event mapping is thin — document what was
  checked, leave a TODO with the unmapped pieces).
- `src/server/ingest/otlp/mapping.py` — pydantic models for the mapping file; loader reads
  every `config/otel/*.yaml` at startup via the shared config loader; `detect(resource_attrs,
  scope_name) → vendor | "unknown"` evaluating configs in filename order.

**Tests (`tests/server/ingest/`):** mapping files parse into typed models; detection picks
`claude_code` on the real fixture resource attrs and `unknown` on garbage; field map applied
to a fixture `api_request` yields the exact column dict (golden); unmapped attrs land in
residue.

**Gate:** both YAMLs load; detection + field-map goldens green.

---

## 3. Phase C — Org-token auth (design §4.5)

**Goal:** `tk_org_` bearer keys verified on OTLP routes; issuance via DB CLI. Mirror
`msging/srv/auth.py` mechanics (sha256 at rest, prefix, in-process cache, revoked check) —
**mirror, don't import**: msging's auth is producer-typed and its internals are its own.

**Files:**
- `src/server/ingest/otlp/auth.py` — `OrgAuth.verify(authorization) → OrgPrincipal{org_id}`,
  raising the ingest error type on missing/unknown/revoked; small TTL cache like msging's.
- `src/server/storage/cli_db.py` — add `org-token issue --org <id> --descriptor <s>` and
  `org-token revoke --hash-prefix <p>` operations (plaintext printed once at issue; hash
  stored; consistent with §18.1 "replaced, never recovered").

**Tests:** issue → verify round-trip; revoked → 401; cache TTL respected; no plaintext in DB.

**Gate:** auth tests green; CLI issue/revoke work against the test DB.

---

## 4. Phase D — OTLP receiver (design §4.1–4.2)

**Goal:** the two routes accept real Claude Code traffic end-to-end into the tables, with the
isolation guards.

**Files:**
- `src/server/ingest/otlp/decode.py` — Content-Type dispatch: `application/x-protobuf`
  (opentelemetry-proto `ExportMetricsServiceRequest` / `ExportLogsServiceRequest`) and
  `application/json` (OTLP/JSON); gzip Content-Encoding; size gate → 413.
- `src/server/ingest/otlp/receiver.py` — the §4.1 pipeline: auth → decode → detect → parse
  (attr→canonical via mapping) → filter (accept_events, count-and-drop with per-event.name
  counters logged) → series-hash compute → writes (`ON CONFLICT DO NOTHING`; one transaction
  per body) → OTLP response (200 empty; `partialSuccess` counts for unmapped records; 400
  malformed; 429/503 + `Retry-After` from guards).
- `src/server/ingest/otlp/router.py` — FastAPI router: `POST /otlp/v1/metrics`,
  `POST /otlp/v1/logs`; **route-scoped semaphore** (config default 16) shedding 429
  *before* decode; per-org token-bucket rate limit (config; breach → 429 + warning log).
- `src/server/ingest/app.py` — mount the router on the existing app; wire config.
- `config/ingest.yaml` — `otlp:` section: `max_body_bytes`, `max_inflight`,
  `org_rate_per_s`, `configs_dir` (`config/otel/`).
- Logging (§13, content-free): `otlp_batch_accepted` (vendor, org, points, events_kept,
  events_dropped{name:count}), `otlp_shed`, `otlp_unknown_vendor`, `otlp_auth_failed`.

**Tests:** golden ingest of both fixture files via FastAPI test client (protobuf AND JSON
encodings — build protobuf test bodies by re-encoding the JSON fixtures through
opentelemetry-proto); **run-twice idempotency** (row counts identical); filter drop-counts
match the fixture's event zoo; unknown-vendor path (metrics stored `vendor=unknown`, events
dropped + alarm log); semaphore shed returns 429 with `Retry-After`; gzip; 413; bad body 400.

**Gate:** full fixture replay lands correct row counts twice; guards behave.

---

## 5. Phase E — True-up worker (design §4.4)

**Goal:** session cost blocks with provenance appear in `session_info`; watermark advances
transactionally; prune runs.

**Files:**
- `config/queries/otel.yaml` — named queries: `touched_sessions` (distinct vendor+session
  past watermark), `series_last_values` (last point per epoch per series — window function),
  `requests_for_session`, `jsonl_requests_for_session` (whatever the fold tables expose for
  per-request usage — align with `fold.yaml`'s shapes), `prune_points`, watermark get/set.
- `src/server/worker/otel_trueup.py` — the §4.4 tick: fold branches on
  `(metric_kind, temporality)`; canonical-measure grouping via the same mapping configs;
  three-way join `(vendor, request_id)`; provenance `otel | otel_only | jsonl_est`;
  writes the cost block into `session_info` **in the same transaction** as the watermark
  advance. **Estimator seam:** pricing engine is dashboard-plan P0 and may not exist —
  v1 rule: `jsonl_est` rows carry token counts with `cost: null` + TODO marker referencing
  the pricing module; do NOT hand-roll a price table here.
- Wire into the worker process mirroring `rollup.py`'s timer registration; interval in
  `config/worker.yaml` (`otel_trueup_interval_s: 60`, `otel_points_retention_days: 30`).
- Prune job: same timer family, deletes points older than retention **only** below the
  fold watermark.

**Tests (the acceptance battery):**
- last-value fold golden vs hand-computed totals from the fixture (the 150×-inflation trap
  test: assert fold ≠ naive sum);
- counter-reset fixture: totals = Σ last-per-epoch;
- **late-arrival convergence** (design §4.4): OTel-first / JSONL-first / interleaved arrival
  orders all converge to the identical cost block, with `otel_only → matched` migration;
- run-twice idempotency on the whole tick;
- prune never deletes above the watermark.

**Gate:** convergence battery green.

---

## 6. Phase F — End-to-end + docs

**Goal:** the standing replay harness covers the new path; enrollment documented; drift
between doc and code reconciled.

- End-to-end test: fixture OTLP replay through the live app + a JSONL fixture for the same
  session through the existing pipeline + one true-up tick → assert the final
  `session_info` cost block (golden), provenance split included. Run twice; identical.
- `docs/modules/otel-intake.md` — module doc: file map, config reference, the §4.2
  enrollment block (managed settings), debug story ("show your work for session X": the
  drop-counters, `otel_*` row queries, fold recompute by session id).
- Update design doc §9 checkboxes / any deltas discovered during implementation (record,
  don't silently diverge). Update `docs/design/system-architecture.md` only if a boundary
  actually moved.
- `utils/` wrapper only if a new CLI entry point was added (Phase C extends `cli_db.py`,
  so likely none).

**Gate:** e2e golden green in CI; `pytest tests/server` fully green; requirements.txt final.

---

## 7. Standing rules for the executor

- Every write path proves run-twice idempotency in its own tests — not just Phase F.
- No vendor literals outside `config/otel/*.yaml` and its loader tests.
- Logs are content-free; identifiers only (§13).
- Skipped work = TODO marker in source at the exact spot, per CLAUDE.md; remove on completion.
- If the design doc and reality disagree, stop and update the doc first (it is frozen, not
  immutable — but divergence must be recorded, and D-OT1…D-OT6 need new evidence to reopen).
