# Ingestion & Session Processing — Implementation Plan

**Status:** Plan for execution (to be run in a separate thread). Pre-implementation.
**Primary spec:** `docs/modules/ingest.md` — the authority for this work (intake, queue, worker,
fold seam, subagents). Every phase below implements a numbered section of it.
**Depends on (already implemented):**
- `docs/modules/event-messaging.md` → `src/common/msging/` (`MsgServer.verify/ack/nack`, `Envelope`,
  `make_store`) — the wire + auth.
- `docs/modules/storage.md` → `src/server/storage/` (`session_scope`, `get_engine`, `QueryCatalog`,
  `Base`, `schema/`, `cli_db.py`) — engine, schema ownership, query loader, DB CLI.
**Context specs (read for boundaries, do not modify blindly):**
- `docs/design/system-architecture.md` — §5 (event messaging), §6.4 (ingest thin), §8/§8.1
  (doorbells), §8.5 (session lifecycle), §13 (content-free logs), §14 (volumes), §15/§15.1
  (concurrency, Postgres-as-queue), §18.1 (auth), §20 (config loader, tree).
- `docs/modules/normalizer.md` §5–6 (subagent linkage: `parent_session_id`, `agentId`).
- `docs/modules/source-adapter.md`, `docs/modules/batch-uploader.md` (upstream feeders — no changes).
- `docs/design/2026-07-19-upload-batch-ingestion.md` §4.2–4.4, §5, §6 (S3 store, idempotency, identity).
- `docs/design/notes/Data_schema_notes.md` (original table thinking).
**Governance:** `CLAUDE.md` (root) — CLI naming, structured JSON logging, pytest, git rules,
requirements hygiene, TODO markers, no Claude references in code/commits.
**Code to be created:** `src/server/ingest/`, `src/server/worker/`, new tables in
`src/server/storage/schema/`, `config/queries/ingest.yaml`, `config/queries/fold.yaml`,
`config/ingest.yaml`, `config/worker.yaml`, `utils/ingest.sh`, `utils/worker.sh`.

---

## 0. Scope, sequencing, prerequisites

**What this delivers:** the pipeline from the ingest HTTP front door through the Postgres-as-queue
to a working fold loop with a **stubbed** fold. The actual metric computation (the real
`session_info` shapes) is **out of scope** — it sits behind the `SessionFold` seam (`ingest.md` §8)
and is a later module. The stub proves the loop end-to-end.

**Sequencing (dependency order):** Schema → Queries → Ingest → Worker → Subagent rollup →
End-to-end. Each phase is independently testable; do not start a phase before its predecessor's
tests are green.

**Prerequisites before writing code:**
1. Read `docs/modules/ingest.md` in full — it is the spec; this plan only sequences it.
2. Confirm the test DB: tests read `TALIUM_TEST_DSN` (storage §9). A local Postgres one-liner is in
   the storage test README. No SQLite — JSONB, `ON CONFLICT`, and `SKIP LOCKED` are load-bearing.
3. Skim the existing patterns to mirror: `src/server/uploader/{app.py,cli_serve.py,config.py}`
   (FastAPI + CLI shape), `src/server/storage/schema/producers.py` (model + JSONB + generated-column
   pattern), `src/server/storage/queries.py` (how `QueryCatalog` loads `config/queries/*.yaml`).
4. Requirements are already present (`sqlalchemy`, `alembic`, `psycopg`, `fastapi`, `uvicorn`,
   `httpx`, `structlog`, `pydantic`, `pytest`). Add nothing unless a real need appears; if so, pin it
   and update `requirements.txt` immediately (CLAUDE.md).

---

## 1. Phase 1 — Schema (`ingest.md` §4)

**Goal:** the two queue tables land in the storage module (single-definition rule, storage §3.1) and
`Base.metadata` picks them up.

**Files:**
- `src/server/storage/schema/event_queue.py` — `EventQueue` model exactly as `ingest.md` §4.1
  (`id` BigInteger identity PK, `event_id` unique index, `session_id` indexed, `envelope` JSONB,
  `enqueued_at`). Reuse the shared `ts`/`jsb` annotations from `schema/base.py` (storage §3.1).
- `src/server/storage/schema/queue_jobs.py` — `QueueJob` model as `ingest.md` §4.2, including the
  **partial unique index** `uq_queue_jobs_pending` (`postgresql_where=text("status = 'pending'")`) and
  the claim index `ix_queue_jobs_claim`.
- `src/server/storage/schema/__init__.py` — import both new modules so `Base.metadata` is complete
  (storage §2).

**Storage-doc amendments (`ingest.md` §13):** update `docs/modules/storage.md` §3.3 and §3.6 to record
that the reserved "events/chunk table" is realized as the **transient `event_queue`** (no durable
per-event store; `seq`/`chunk_number` dropped — identity is `event_id`), that `sessions.signal_blob`
remains the durable normalized signal + dedup ledger, and that `queue_jobs` (arch §15.1) is added under
storage's schema ownership. Add both new tables to the §3.6 living-record matrix.

**Acceptance:**
- `python -m server.storage.cli_schema_dump --ddl` emits CREATE TABLE/INDEX for both (storage §7);
  update the golden DDL snapshot in `tests/fixtures/` (storage §9) as a reviewable diff.
- `python -m server.storage.cli_db init --force` against `TALIUM_TEST_DSN` builds them clean.
- `tests/server/storage/test_queue_schema.py`: columns/indexes present; `event_id` unique;
  the partial-unique index rejects a 2nd pending `(topic, dedup_key)` but allows a `done` + a new
  `pending`.

---

## 2. Phase 2 — Query catalog (`ingest.md` §4.3)

**Goal:** the named SQL is loadable and correct.

**Files:**
- `config/queries/ingest.yaml` — `stage_events`, `ring_fold` (verbatim from `ingest.md` §4.3).
- `config/queries/fold.yaml` — `claim_job`, `drain_events`, `delete_drained`, `sweep_orphans`,
  plus `mark_job_done`, `mark_job_failed`, and the subagent queries added in Phase 5.

**Acceptance:**
- `QueryCatalog.load(Path("config/queries"))` compiles them without `QueryConfigError` (storage §4).
- `tests/server/storage/test_queue_queries.py`: round-trip each against a real DB —
  `stage_events` + duplicate `event_id` ⇒ one row; `ring_fold` twice for a session ⇒ one pending job;
  `claim_job` with two concurrent transactions ⇒ `SKIP LOCKED` hands the job to exactly one.

---

## 3. Phase 3 — Ingest service (`ingest.md` §3)

**Goal:** `POST /v1/events` authenticates, parses, stages, rings, acks — thin and stateless.

**Files (layout `ingest.md` §3.1):**
- `config.py` — `IngestConfig` (pydantic): `max_body_bytes`, `producer_auth`, plus HTTP host/port
  (mirror `uploader/config.py`). References `config/ingest.yaml` (§9 of the spec). Storage/msging
  configs load from their own YAML — never re-declare a DSN.
- `errors.py` — ingest-local errors (malformed batch, oversized body).
- `repo.py` — the ingest repository over storage's `session_scope` + `QueryCatalog`: `stage_batch`
  (bulk `stage_events` + per-session `ring_fold` in **one** `session_scope`, `ingest.md` §3.3).
- `intake.py` — framework-free batch lifecycle (`ingest.md` §3.3 steps 1–6): gunzip → parse
  `Envelope.model_validate_json` per line → on any parse/validation failure raise → caller maps to
  `nack(malformed)`; success → `repo.stage_batch` → `ack`. Buffer-then-parse (`ingest.md` §3.4).
- `routes.py` / `app.py` — the only FastAPI files. Wire `MsgServer.verify` as a dependency (handlers
  receive a `Principal`, never a header — event-messaging §4). `MsgServer` is built with
  `make_store(msging_cfg)` (event-messaging §5). Also expose `POST /v1/producers/register`
  (delegates to `MsgServer.issue`) and `/healthz`. Handlers are plain `def` (storage §4). Response
  codes map to shipper outcomes: 200 ok / 400 malformed / 401 auth / 5xx retryable (event-messaging
  §4.1). Enforce `max_body_bytes`.
- `cli_serve.py` — `python -m server.ingest.cli_serve --config config/ingest.yaml` (mirror
  `uploader/cli_serve.py`). Wrapper `utils/ingest.sh`.

**Acceptance (`tests/server/ingest/`):** malformed line ⇒ whole-batch `nack(malformed)` 400;
bad/absent auth ⇒ 401; duplicate batch ⇒ zero net `event_queue` rows; body over `max_body_bytes`
⇒ rejected; a valid 2-session batch ⇒ N staged rows + 2 coalesced jobs, `ack` 200. Drive via
FastAPI `TestClient` posting gzip'd JSONL built from `Envelope` fixtures.

---

## 4. Phase 4 — Worker & fold seam (`ingest.md` §5, §8)

**Goal:** the dispatch loop + the `fold_session` handler run end-to-end against a **stub** fold.

**Files (layout `ingest.md` §5.1):**
- `config.py` — `WorkerConfig`: `topics`, `poll_interval_s`, `claim_batch`, `sweep_interval_s`,
  `max_attempts` (spec §9). References `config/worker.yaml`.
- `fold_api.py` — the **abstracted seam** (`ingest.md` §8): `SessionFold` Protocol (`fold`,
  `rollup_subagents`) plus the plain dataclasses it exchanges (`SessionState`, `SessionFoldResult`,
  `SessionInfoDelta`). **No metric logic here.**
- `fold_stub.py` — a minimal `SessionFold`: append new events to `sessions.signal_blob`, record raw
  counts into `session_info` (proving the loop; real extraction is a later module). Reads identity
  from the first envelope header to `load_or_init` the `sessions` row (`ingest.md` §5.2).
- `dispatch.py` — topic-agnostic loop: poll (`poll_interval_s`) → `claim_job` (`SKIP LOCKED`) → run
  the topic handler in one `session_scope` → `mark_job_done`, or `mark_job_failed` with backoff
  (`attempts`/`run_after`, capped at `max_attempts`). Run `sweep_orphans` every `sweep_interval_s`
  and `ring_fold` each result (`ingest.md` §5.3 — doorbell advisory, table is truth).
- `fold_session.py` — the `fold_session` handler (`ingest.md` §5.2): `drain_events` → `max_id` →
  `load_or_init` → **dedup by `event_id` against the session blob** → `fold(...)` → `delete_drained`
  → (if tail `id>max_id`) `ring_fold(session_id)`. Subagent trigger added in Phase 5. All in the one
  transaction the loop opened.
- `cli_work.py` — `python -m server.worker.cli_work --config config/worker.yaml`. Wrapper
  `utils/worker.sh`.

**Acceptance (`tests/server/worker/`):** two workers + one session ⇒ exactly one folds (SKIP LOCKED);
burst of arrivals ⇒ one coalesced job; a mid-fold tail (insert `id>max_id` after the drain read)
survives and re-folds; re-running a fold on an already-folded session ⇒ zero economic change (dedup
vs blob); lost-doorbell (delete the pending job, leave staged rows) ⇒ `sweep_orphans` recovers.

---

## 5. Phase 5 — Subagents & rollup (`ingest.md` §7)

**Goal:** child economics roll up into the parent — own + inclusive + itemized.

**Work:**
- In `fold_session.py`: after a session with `parent_session_id` folds, `ring_fold(parent_session_id)`
  (child-driven upward trigger).
- In `fold_stub.py` `rollup_subagents`: on a parent fold, find children via the
  `sessions.parent_session_id` index (new `fold.yaml` query `children_of`), read their `session_info`,
  rebuild `session_info.subagents` **keyed by `child_session_id`** (replace-by-key, idempotent), and
  compute `own` + `inclusive` + itemized totals (`ingest.md` §7 — the confirmed shape).
- Add `fold.yaml` queries: `children_of` (`WHERE parent_session_id = :session_id`),
  `session_info_of` (read children's rows).

**Acceptance (`tests/server/worker/test_subagents.py`):** child-first and parent-first orderings
converge to the same `inclusive` total; a child resume ⇒ parent `subagents[child]` replaced and total
refreshed (no double-count); two-level nesting (grandchild→child→parent) propagates up; parent+child
streams disjoint ⇒ no double-count between own-fold and rollup.

---

## 6. Phase 6 — End-to-end & wrappers

**Goal:** a full slice proven; operator entry points in place.

**Work:**
- `tests/server/test_ingest_to_fold.py`: build `Envelope` batches (parent + a subagent, using
  `normalizer` sample data where useful — `tests/sample-data/`), POST to the ingest `TestClient`,
  run the worker loop once, assert `sessions` + `session_info` reflect the folded result including the
  subagent rollup. This is the acceptance for the whole plan.
- `utils/ingest.sh` → `python -m server.ingest.cli_serve`, `utils/worker.sh` →
  `python -m server.worker.cli_work` (CLAUDE.md shell-wrapper convention; arch §20 tree).
- Verify with `/verify` or a manual run: start ingest, post a batch, run the worker, inspect rows.

---

## 7. Testing strategy (CLAUDE.md rule 2 + storage §9)

- **Real Postgres**, `TALIUM_TEST_DSN`; suite skips with a clear message when unset.
- **Fixtures:** session-scoped `init --force` against the test DB; per-test transaction-rollback for
  isolation (mirror storage's fixtures).
- **After each phase, run that module's tests** (CLAUDE.md rule 4 "Verify"): `pytest tests/server/ingest`,
  `pytest tests/server/worker`, `pytest tests/server/storage`. Fix failures immediately (rule 2).
- Golden DDL diff for the schema change (storage §9).

---

## 8. Conventions & guardrails (CLAUDE.md)

- **CLI naming:** `cli_serve.py` (ingest), `cli_work.py` (worker) — never bare `cli.py`; wrappers in
  `utils/` call `python -m {module}.cli_{function}`.
- **Logging:** structured JSON via structlog, loggers `ingest` and `worker`, the declared events in
  `ingest.md` §10. `session_id`/`event_id`/`batch_id` on every relevant line (arch §13). **Never log
  payload contents or bound parameter values.**
- **Schema ownership:** tables live in `src/server/storage/schema/`; ingest/worker write repositories
  over storage's `session_scope` + `QueryCatalog` — they never `create_all`/migrate (storage §1, §6).
- **Git:** direct `git` with file paths, never `git -C`. No "Claude"/"co-authored" strings in code or
  commit messages.
- **TODOs:** any deferred sub-task gets a `TODO` in-source with a description; remove it when done.

---

## 9. Open decisions to confirm before/at kickoff

1. **Schema location.** Plan puts `event_queue`/`queue_jobs` in `storage/schema/` (storage §3.1
   single-definition rule). If the team wants them physically under `ingest/`, that's a deliberate
   deviation to note. *Default: storage/schema.*
2. **Apply storage.md §3.3/§3.6 amendments now** (Phase 1) vs. after ingest review. *Default: now,
   as part of Phase 1, since the schema change is concurrent.*
3. **Fold stub scope.** The stub records raw counts only; the real metric registry (own/inclusive
   shapes, cost engine) is the fold module (storage §3.4), out of scope here. Confirm the stub is an
   acceptable end-to-end proof for this milestone.
4. **`producer_auth` in the deploy target.** `true` by default; `false` only inside a fully trusted
   zone (RFC §5.3). Confirm per environment.

---

## 10. Dependency graph

```
Phase 1 (schema) ──► Phase 2 (queries) ──► Phase 3 (ingest) ─┐
                                    └────► Phase 4 (worker) ──┼─► Phase 5 (subagents) ─► Phase 6 (e2e)
```

Phases 3 and 4 both depend on 1+2 but are independent of each other; they can proceed in parallel if
two hands are available, joining at Phase 5. The fold seam (§8 of the spec) means Phase 4 never blocks
on the real metric work.
