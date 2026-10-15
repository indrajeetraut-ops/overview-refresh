# Storage Module — Design

**Status:** Draft v1 — iterative; the schema section awaits the initial table proposal.
**Scope:** the data-access layer for all server processes (`src/server/storage`): schema
definitions, engine/session management, the named-query loader, migrations, and the DB
management CLI. Architecture context: system-architecture.md §10.

---

## 1. Nutshell

One central storage module owns the **schema as SQLAlchemy declarative models** (no
`relationship()`, no lazy loading), a **named-query loader** for raw SQL kept in
`config/queries/*.yaml`, **Alembic** migrations under an explicit two-phase policy
(recreate era → migration era), and a **DB management CLI** where every
schema-touching action is an admin verb — services never create or migrate schema.

Consuming modules (ingest, workers) write their own repositories on top of this module's
Session and query catalog. Tables are shared contracts: defined once here, referenced by
name from any module's queries.

## 2. Layout

```
src/server/storage/
├── __init__.py          # public API re-exports (§4)
├── schema/              # ALL table models — one file per domain
│   ├── __init__.py      # imports every domain module → Base.metadata complete
│   └── producers.py     # first domain; events.py, sessions.py … arrive with their modules
├── engine.py            # engine + session factory from config
├── queries.py           # query catalog: scans config/queries/, compiles text()
├── producer_store.py    # PgProducerStore — implements msging.ProducerStore
├── dbm/                 # alembic env + versions/, verb implementations
├── cli_db.py            # admin verbs (§6)
└── cli_schema_dump.py   # DDL + ER-diagram emitters (§7)
```

Query YAML lives in `config/queries/`, one file per consuming domain
(`ingest.yaml`, `session_worker.yaml`, …): central location, per-owner files.

## 3. Schema

Representation is SQLAlchemy 2.0 typed declarative (`Mapped[...]` + `mapped_column`) — the
models *are* the schema (Alembic diffs them, the ER diagram generates from them; no second
definition to drift). Pydantic stays for wire contracts only (the envelope,
`ProducerRecord`). **One table per section below**, each landing as `schema/<domain>.py`,
all on the shared `Base` — so a reader (human or agent) can jump to a single table with its
own model, indexes, and rationale.

### 3.1 Conventions

- **Models are dumb table declarations** — columns, indexes, constraints. No
  `relationship()`, no lazy loading, no model methods with query logic. Joins are written
  explicitly in named queries.
- **FK constraints only for genuinely safe references.** Tables on the ingest/fold path use
  plain indexed columns instead (e.g. `sessions.producer_id`/`user_id`) so a row never
  blocks on another table's row arriving first. The initial set has no FKs.
- **Naming:** snake_case plural table names (`producers`, `events`); `_at` suffix for
  timestamps; all timestamps `timestamptz` UTC.
- **Dynamic maps are JSONB** (metric map, event payloads) so key churn never forces a
  migration. A JSONB key repeatedly used in WHERE clauses of high-volume reads is promoted
  to a real column — or a **generated column** derived from the blob (§3.2).
- A table is **defined exactly once** regardless of how many modules use it. Renaming a
  table or column is a cross-module contract change and is treated like one.

Shared declarations, used by every table section below:

```python
from datetime import datetime
from decimal import Decimal
from typing import Annotated
from sqlalchemy import Computed, DateTime, Index, Numeric, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

class Base(DeclarativeBase): ...

ts  = Annotated[datetime, mapped_column(DateTime(timezone=True))]   # timestamptz, always UTC
jsb = Annotated[dict, mapped_column(JSONB, default=dict)]           # JSONB object / map
jsl = Annotated[list, mapped_column(JSONB, default=list)]           # JSONB list
```

### 3.2 `producers` — registration backend

`schema/producers.py`. The DB-table backend for `msging.ProducerStore` (the other backend
is a file, §5). Each row is `producer_id` (the registration UUID, PK) plus `record` — the
**entire `ProducerRecord` as JSONB**, the same dict `FileProducerStore` writes to disk, so
choosing file vs. table is a config switch, not a rewrite. `record` also holds auth
opaquely: `{key_prefix, key_hash}` today, or a `{secret_ref}` pointer to a secret store
later.

```python
class Producer(Base):
    __tablename__ = "producers"
    producer_id: Mapped[str] = mapped_column(primary_key=True)   # registration UUID
    record:      Mapped[jsb]                                      # full ProducerRecord — mirrors file store
    # real indexed keys DERIVED from the blob (see below); the store writes only `record`
    key_hash:    Mapped[str] = mapped_column(String, Computed("(record ->> 'key_hash')", persisted=True))
    key_prefix:  Mapped[str] = mapped_column(String, Computed("(record ->> 'key_prefix')", persisted=True))
    __table_args__ = (
        Index("uq_producers_key_hash", "key_hash", unique=True),   # direct O(1) auth lookup
        Index("ix_producers_key_prefix", "key_prefix"),            # backs get_by_key_prefix
    )
```

**Pattern — indexed keys derived from a JSONB blob.** We want the blob (file-store
symmetry, migration-free field churn) *and* a real, indexed, unique key on the auth field
(`verify()` runs on every ingest batch — the hot path). A Postgres **STORED generated
column** gives both: `Computed("(record ->> 'key_hash')", persisted=True)` emits
`GENERATED ALWAYS AS ((record ->> 'key_hash')) STORED`. The column is a first-class,
indexable value that Postgres derives from `record` and keeps in sync — it *cannot* drift,
and the store still writes only `record` (no dual-write, no divergence from
`FileProducerStore`). This is the general answer whenever a queried field lives in a blob:
promote it to a generated column, never a hand-maintained duplicate.

Consequences: with `key_hash` unique-indexed, `verify()` can hash the presented key and
look it up directly — one row, no prefix-collision handling. `get_by_key_prefix` still
works via the `key_prefix` index; switching `verify()` to direct hash lookup is a small
msging-side follow-up (§5). `touch_last_seen`/`set_revoked` are `jsonb_set` updates on
`record` (fine at arch §14 volumes). `key_hash` is a hash, never plaintext — safe at rest;
the plaintext lives only in the client's `producer.json` (arch §7.1).

### 3.3 `sessions` — one row per session

`schema/sessions.py`. Session identity and lifecycle (§8.5), one row per `session_id`, plus
the **raw normalized signal** (`signal_blob`). The extracted intelligence derived from that
signal lives in `session_info` (§3.4). Per-event fields (`seq`/`chunk_number`, `parent_id`,
`schema_version`) belong to `event_queue` (transient, see §3.6 #13) while in flight, and to
`signal_blob` once folded — neither lives on `sessions` as a column.

`signal_blob` doubles as the fold's **dedup ledger**: the worker checks each drained event's
`event_id` against it before folding, so an already-folded event re-delivered through
`event_queue` is skipped (ingest.md §6).

```python
class Session(Base):
    __tablename__ = "sessions"
    session_id:    Mapped[str] = mapped_column(primary_key=True)
    producer_id:   Mapped[str] = mapped_column(index=True)    # plain column, no FK (§3.1)
    org_id:        Mapped[str | None]
    user_id:       Mapped[str | None] = mapped_column(index=True)   # plain column, no FK
    agent:         Mapped[str]                                # claude_code | codex | cursor | ...
                                                              #   (envelope `vendor` enum; validated in code)
    agent_version: Mapped[str | None]
    status:        Mapped[str] = mapped_column(default="live")  # live | ended | idle_closed (§8.5)
    started_at:    Mapped[ts]
    last_event_at: Mapped[ts]
    ended_at:      Mapped[ts | None]
    signal_blob:   Mapped[jsb]                                # raw normalized data (from the normalizer)
    info_blob:     Mapped[jsb]                                # debug / harness-dependent
    # subagent linkage — both directions, ingest-set (see note); NOT session_info.subagents (§3.4)
    parent_session_id: Mapped[str | None] = mapped_column(index=True)  # this row's parent, if it is a subagent session
    subagents:         Mapped[jsl]                                     # child session_ids spawned under this session
```

`agent` is a plain string (the low-cardinality `vendor` enum), not a FK to a lookup table —
there's nothing queryable a lookup would carry. Promote to a table only if agents ever gain
real attributes.

**Subagent linkage — both directions, on purpose.** A subagent runs as its own session; the
parent↔child relationship is recorded here explicitly, not inferred later from event content
at query time. **Set by the worker's fold, not literally by the ingest HTTP layer**: ingest is
thin and never writes to `sessions` (ingest.md §2 boundaries table); `parent_session_id` lives
only in the `session_start` event's `payload` (normalizer.md §6.2 — `EnvelopeHeader` carries no
field of its own for it), so the fold sets it on a session's first fold, and the parent's
`subagents` list is populated by the rollup (ingest.md §7) the first time it discovers that
child via `sessions.parent_session_id`. `parent_session_id`
is the child→parent link (a subagent row points at its parent; indexed so "list a session's
subagents" is a plain lookup), and `subagents` is the parent→child list of `session_id`s. The
schema carries **both and picks no traversal direction** — ingest and the workers use
whichever they need, and either side is rebuildable from the other. This is distinct from
`session_info.subagents` (§3.4), which is fold-worker *intelligence* about each subagent
(tokens, tools, files), not a link to its session row.

### 3.4 `session_info` — extracted intelligence (worker output)

`schema/session_info.py`. One row per session, written by the folding/extraction worker.
Kept **separate from `sessions` on purpose**: `sessions.signal_blob` is the raw normalized
data; `session_info` is the *intelligence extracted from it* — a different concern, a
different consumer (the rollup worker reads it, arch §9), and a shape that will evolve
heavily as extraction improves. Being derived, it is fully re-buildable by re-running the
worker over the raw signal (idempotent replay, arch §5.2). The extraction is now **graph-based**:
`session_info` is derived from the session's stored **causal graph** (`session_graph`, §3.4a), the
worker's real source of truth — the graph, not this row, is what a rollup reads.

```python
class SessionInfo(Base):
    __tablename__ = "session_info"
    session_id: Mapped[str] = mapped_column(primary_key=True)   # 1:1 with sessions; no FK (§3.1)
    cost_usd:   Mapped[Decimal | None] = mapped_column(Numeric(12, 4))  # normalized total; null until costed
    models:     Mapped[jsb]     # {model: {input, output, cache_read, cache_write, cost}}
    tools:      Mapped[jsb]     # {tool_name: {count, type}} — top-K derived at query time (arch §9.6)
    files:      Mapped[jsb]     # {path: {ops, hunks, ext}}
    skills:     Mapped[jsl]     # [{skill_name, tokens, started, ended}]
    subagents:  Mapped[jsb]     # {child_session_id: {cost, tokens, tools, files, ...}} — replace-
                                #   by-key rollup (ingest.md §7), not a list
    pr_list:    Mapped[jsl]     # PR refs — populated by the git correlator (arch §8.4), not the fold
    info:       Mapped[jsb]     # placeholder / catch-all for evolving intelligence
    otel:       Mapped[jsb]     # the OTel true-up's cost block (§3.7) — a SECOND writer
    # Promoted out of `otel` (dashboard-datasource-modes design §3.2), written by the true-up
    # in the same statement as `otel` — never by the JSONL fold. Real columns, not JSONB keys,
    # because the sessions list sorts/filters on cost and mode and JSONB sorting is forbidden.
    # All nullable: a row the JSONL fold creates before the true-up has ever ticked has no mode
    # yet, and `None` is the honest value, not a guessed `jsonl_only`.
    cost_effective_usd_micros: Mapped[int | None]  # vendor cost where present, else the estimate
    datasource: Mapped[str | None]                 # both | jsonl_only | otel_only
    jsonl_lag_s: Mapped[int | None]                # set only while OTel has requests JSONL hasn't
    created_at: Mapped[ts]
    updated_at: Mapped[ts]
```

**Two writers, two columns.** `info` belongs to the fold, which replaces it wholesale on every
arrival of a session (`persist_session_info`; `fold()` is contractually required to return a
complete `SessionInfo`). The OTel true-up worker is a separate writer on a separate schedule,
so its cost block gets its own column — a key inside `info` would be deleted by the next
upload of the session it describes. Any future third writer wants the same treatment.
`cost_effective_usd_micros`/`datasource`/`jsonl_lag_s` are the true-up's own columns for the
same reason, written alongside `otel` on every fold — `persist_session_info` deliberately never
names them.

Field notes: **`cost_usd` is `Numeric`**, never a string — nullable until the cost engine
runs. **`models`/`tools`/`files` are maps keyed by name** (store the full map; top-K/least-K
are query-time views per arch §9.6); **`subagents` is also a map**, keyed by
`child_session_id` for idempotent replace-by-key rollup (ingest.md §7), not a list; **`skills`/
`pr_list` are lists** of in-memory structs (per the no-unnecessary-tables directive — not child
tables). **`pr_list`
is written by the git correlator**, a different worker than the fold. **`tasks` are not
here** — the classifier produces its own task rows (arch §8.4; §3.6). `started`/`ended` are
not duplicated — they live on `sessions`. `session_id` is a plain PK (no FK), 1:1 with
`sessions`.

**The JSONB shapes above are illustrative, not a contract.** Which metrics are extracted and
how each map/list is keyed is owned by the **folding/extraction worker** (arch §9 metric
registry) and designed with that module — deferred, not decided here. Because the columns are
JSONB, that contract evolves in the worker with **no storage migration**, and the set is
expected to grow past the notes' list (candidates: code-production volume — net lines
added/removed — plus turn/error/retry counts). Storage's job is only to hold whatever the
worker produces; the only real column is `cost_usd`, because rollups aggregate it.

### 3.4a `session_graph` & `session_tree` — the causal graph (the fold's source of truth)

`schema/graph.py`. The extraction worker is **graph-based** (`GraphFold`): on each arrival it builds
this session's **causal graph** from the deduped event blob and derives `session_info` (§3.4) from it.
The graph itself is persisted, in two tables, so `session_info` is a *derived view* of a stored graph
rather than a one-shot computation — any consumer (the rollup, the dashboard, future analytics) can
load a whole session family in one fetch without re-folding.

```python
class SessionGraph(Base):                 # per-SESSION — the write-safe source of truth
    __tablename__ = "session_graph"
    session_id:         Mapped[str] = mapped_column(primary_key=True)   # 1:1 with sessions; no FK (§3.1)
    parent_session_id:  Mapped[str | None] = mapped_column(index=True)  # null for a root session
    graph_json:         Mapped[jsb]         # Graph.to_dict() — nodes/edges/meta for this ONE session
    schema_version:     Mapped[str]
    built_at:           Mapped[ts]

class SessionTree(Base):                   # per-FAMILY — a regenerable single-fetch read cache
    __tablename__ = "session_tree"
    master_session_id:  Mapped[str] = mapped_column(primary_key=True)   # the root session of the family
    tree_json:          Mapped[jsb]         # FLAT map {session_id: Graph.to_dict()} of master + ALL descendants
    member_count:       Mapped[int]
    built_at:           Mapped[ts]
```

**Two tables, two roles.** `session_graph` holds **one row per session** (a subagent is just another
session, §3.3) — each session/subagent writes *its own* row on arrival with **no cross-session
coordination**, which is what makes it the write-safe truth under out-of-order, child-before-parent,
concurrent-sibling arrival. `session_tree` holds **one row per family**, keyed by the master
(root) session: its `tree_json` is a **flat** map of the master graph plus every descendant graph
(any nesting depth) — hierarchy is carried by each graph's `parent_session_id` + the master's `Spawn`
child-refs, *not* by nesting the JSON. It is a **materialized read cache**, rebuilt at rollup
(`graph_build.materialize_tree`) by a BFS over the `session_graph` rows; it is regenerable and never a
second source of truth.

`GraphService.load_family(session_id)` reads the family in **one fetch** from `session_tree` when
fresh, and falls back to assembling it from the per-session `session_graph` rows when the cache is
absent/stale. The rollup worker (ingest.md §7) uses exactly this to compute the tree-scoped fields.
Like `session_info`, both tables are fully re-buildable from `sessions.signal_blob` (idempotent
replay, arch §5.2).

### 3.5 `users` — session and PR owner

`schema/users.py`. Tracks who a session and a git PR belong to. Simple now, expected to
evolve.

```python
class User(Base):
    __tablename__ = "users"
    user_id:       Mapped[str] = mapped_column(primary_key=True)
    email:         Mapped[str | None]
    name:          Mapped[str | None]
    github_user:   Mapped[str | None] = mapped_column(index=True)  # git correlator attributes PRs by this
    account_uuid:  Mapped[str | None] = mapped_column(index=True)  # links to the OTel-side identity, below
    info:          Mapped[jsb]
    created_at:    Mapped[ts]
```

`sessions.user_id` stays a plain column (no FK), like `producer_id` — a session can arrive
before its user row exists, and ingest must not block on ordering. `github_user` is indexed
because the git correlator (arch §8.4) resolves PR authors to users through it.

`account_uuid` bridges a second identity namespace: OTel telemetry carries the developer's
vendor account UUID (`user.account_uuid`, `config/otel/*.yaml`'s `identity.user_attrs`), which
has no relationship to whatever string the JSONL-side collector was told `user_id` is at upload
time. Declared once, at JSONL upload (`utils/upload_claude_sessions.sh` reads it from the local
Claude Code OAuth credentials, `DeclaredFields.account_uuid` -> the envelope info bag ->
`ingest.yaml`'s `upsert_user_account_uuid`), never inferred or guessed. `otel_trueup.py`'s
`_ensure_session_row` resolves an OTel-only stub's `user_id` through this column
(`otel.yaml`'s `resolve_user_by_account_uuid`) so a session with no matching JSONL session_id
still lands under the same `user_id` a dashboard is scoped to, instead of surfacing the raw
account UUID. Falls back to the raw UUID when no link is recorded yet — only ever set when a
real upload declared it, so a deployment that never links loses nothing.

`github_user` bridges a THIRD identity namespace the same way: git-source auto-creates a
`github:<login>` identity the first time an unrecognized PR/commit author shows up
(`apply.py`'s `resolve_user_id`, git-source.md §6.3), which by default has no relationship to
whatever `user_id` a person's own JSONL uploads use. Optionally self-declared at JSONL upload
just like `account_uuid` (`utils/upload_claude_sessions.sh`'s `github_user` param ->
`DeclaredFields.github_user` -> the envelope info bag -> `ingest.yaml`'s
`upsert_user_github_login`), also never inferred — email is deliberately not used for this
(`apply.py`'s own reasoning: GitHub's noreply addresses "defeat" matching outright).
`fold_handler.py` resolves the ordering both ways: before writing `sessions.user_id`, it checks
whether some OTHER `user_id` already claims this login (`resolve_canonical_user_id_by_github_login`)
— the common case being a `github:<login>` stub that git-source auto-created before this person
ever declared their handle — and if so, folds this session under THAT `user_id` instead of
forking a second identity. Case-normalized lower-case on both write paths (GitHub logins are
case-insensitive; the case reported can still differ between a webhook, REST/GraphQL, and
whatever a person typed by hand). This does not retroactively repair `git_artifacts.user_id`
values already written under a stub identity before the declaration happened — resolution is
correct for every session/PR folded from that point forward, not a historical backfill.

### 3.6 Deferred & TBD tables (explicitly tracked)

**Convention: nothing is silently dropped.** Every table from the notes that is not yet
designed above is listed here with an explicit status — **DEFERRED** (will be designed with
its consuming module) or **TBD** (needs discussion before design) — plus the matrix below.
Not everything here becomes a table: many are JSONB inside a parent row or in-memory
structs (avoid unnecessary tables; add one only for a query/join/integrity/audit need).

- **`orgs`** — **DEFERRED.** `sessions.org_id` and `users` carry org identity as plain
  values; the org lookup/dimension table is designed when org-level rollups or multi-org
  resolution land.
- **`events` / chunk table** — **Realized as `event_queue`** (ingest.md §4.1), but as a
  **transient staging table**, not a durable per-event store: the fold drains and deletes
  each row once folded, so it never grows with history — durability lives in S3 (raw) and
  `sessions.signal_blob` (normalized). `seq`/`chunk_number` are **dropped**; `seq` resets
  across uploads and is transport-only, so identity and fold order are `event_id` and
  `event_queue.id` respectively (ingest.md §4.1, §6), not `seq`.
- **`queue_jobs`** — **Adopted**, added under storage's schema ownership though designed in
  `ingest.md` §4.2 (ingest/worker scope, per §3.1's single-definition rule). A generic
  per-session background-work queue (`topic` + `dedup_key`, `FOR UPDATE SKIP LOCKED` claim);
  `fold_session` is its first consumer, not its only one (arch §15.1).
- **models** — **TBD, not a table now.** A model is a name string; token-per-model counts
  are a JSONB map in `session_info` (§3.4). **Could become a `model_pricing` table later if
  necessary** — and only in that form: carrying input/output/cache prices, effective date,
  and source+version (queried by the cost engine, needs per-run audit per arch §12), seeded
  from the LiteLLM snapshot (arch §19). Owned by the cost engine, not this module.
- **`skill` / `hunk` / `tool` / `file` / `subagent`** — **Not tables.** In-memory structs
  the folding worker builds and stores as JSONB inside `session_info` (§3.4); extraction is
  server-side (arch §6.2). Promote any one to a table only if cross-session querying needs
  it.
- **`tasks`** — **DEFERRED.** Owned by the classifier module (its own task rows).
- **Goods / PR** — **Adopted** as `git_artifacts` (`docs/modules/git-source.md` §9), alongside
  `git_installations`, `git_repos`, and `git_deliveries`. `session_info.pr_list` remains the
  denormalized read view (§3.4); `git_artifacts` is the acquisition module's normalized store,
  populated by the mapper, not by the fold worker.

Living record of every table in the notes and its disposition:

| # | Notes table (§) | Status | In design | Resolution |
|---|---|---|---|---|
| 1 | `SessionTbl` (1.1) | Adopted | `sessions` (§3.3) | `seq`/`parent_id` are event-level → future `events`/chunk table; `schema_version` dropped (it describes the event blob format, not a session attribute); added `status` + lifecycle timestamps + `agent_version`. |
| 2 | `ProducerTbl` (1.2) | Adopted | `producers` (§3.2) | `producer_id` (UUID PK) + `record` JSONB = full `ProducerRecord`, mirrors the file store; `ip_address`/`name` live in the blob; auth opaque in the blob (hash, or a secret-store pointer later); `key_hash`/`key_prefix` are STORED generated columns (unique index on `key_hash`) for the hot `verify()` lookup. |
| 3 | `AgentTbl` (1.2) | Not a table | — | Low-cardinality `vendor` enum → plain `sessions.agent` string column (§3.3); version → `sessions.agent_version`. Promote to a table only if agents gain queried attributes. |
| 4 | `UserTbl` (1.2) | Adopted | `users` (§3.5) | Simple table now; needed to track session and git-PR owners; `github_user` indexed for git correlation; `org_ids` list dropped (session carries `org_id`). |
| 5 | `OrgTbl` (1.2) | **DEFERRED** | — | Org identity carried as plain values; table designed with org rollups/resolution. |
| 6 | `modelsTbl` (1.3) | **TBD** | — | No table now (JSONB map in `session_info`); may become a `model_pricing` table under the cost engine if needed. |
| 7 | `Tool` (1.3) | Not a table | `session_info.tools` (§3.4) | JSONB map `{tool_name: {count, type}}`; classification server-side (arch §6.2). |
| 8 | `File` / `hunk` (1.3) | Not a table | `session_info.files` (§3.4) | JSONB map `{path: {ops, hunks, ext}}`. |
| 9 | `subagent` (1.3) | Not a table | `sessions.parent_session_id` + `sessions.subagents` (§3.3); `session_info.subagents` (§3.4) | Session *link* (both directions, set by the worker's fold — see §3.3 note) lives on `sessions`; per-subagent *intelligence* (tokens/tools/files) stays a JSONB map keyed by `child_session_id` in `session_info`. |
| 10 | `Task` (1.3) | **DEFERRED** | — | Classifier module owns it. |
| 11 | `SessionInfoTbl` (1.3) | Adopted | `session_info` (§3.4) | Separate from `sessions` — the extracted-intelligence payload (cost, models, tools, files, skills, subagents, PRs) vs. the raw signal; worker-written, JSONB-heavy, evolves. |
| 12 | Goods / PR (1.4) | Adopted | `git_artifacts` (git-source.md §9) | Four-table acquisition module (`git_installations`, `git_repos`, `git_artifacts`, `git_deliveries`), designed in git-source.md, owned here per §3.1; `session_info.pr_list` remains the denormalized read view. |
| 13 | `events` / chunk table (§3.6, orig.) | Adopted | `event_queue` (ingest.md §4.1) | Realized as a **transient** staging table (drained + deleted per fold), not a durable per-event store; `seq`/`chunk_number` dropped, `event_id`/`event_queue.id` are identity and fold order. |
| 14 | — (new, not in original notes) | Adopted | `queue_jobs` (ingest.md §4.2) | Generic per-session dispatch queue (`topic`, `dedup_key`, `SKIP LOCKED` claim); designed in the ingest module, owned here per §3.1. |

### 3.7 OTel intake tables — `otel_series`, `otel_metric_points`, `otel_api_requests`, `org_tokens`

**Adopted**, owned here under §3.1's single-definition rule but designed in
`docs/design/2026-08-04-otel-intake-design.md` §4.3/§4.5 — the same arrangement as
`queue_jobs`. They carry the second signal source (OTLP straight from the agent client);
the JSONL path's tables are untouched by them, and the two meet only in the true-up
worker's write to `session_info`.

- **`otel_series`** — one row per metric stream (metric name + full label set). The label
  set is ~1 KB and repeats every export interval, so it is stored once here and the
  datapoints reference it by `series_id`, a sha256 the receiver computes over vendor +
  metric name + canonically-sorted attributes. That hash is also the dedup key.
- **`otel_metric_points`** — append-only datapoints, `(series_id, time_unix_nano)` PK. The
  volume table (~10⁷ rows/day at the reference org) and a **staging buffer**: pruned past a
  retention window once folded. `start_unix_nano` preserves reset epochs, without which
  cumulative totals are unrecoverable after a client restart.
- **`otel_api_requests`** — the durable per-request spend ledger, `(vendor, request_id)` PK.
  The join key back to JSONL. Kept, not pruned.
- **`org_tokens`** — `tk_org_` bearer keys, sha256 at rest, `token_hash` PK, revocation by
  flag. Separate from `producers` on purpose: one credential kind per intake path (D-OT4).
- **`otel_watermarks`** — one row per vendor holding the `received_at` the true-up fold has
  consumed up to. Arch §8.3 requires progress state in Postgres, advancing in the same
  transaction as the work it describes, and leaves the mechanism to the module; this is it.
  Per vendor, not global: a new vendor's first tick must not be told it has already folded
  everything.

Two deliberate departures from the design doc's own table sketch, both recorded there:

1. **No FK from `otel_metric_points.series_id` to `otel_series`** (the design's ER draws
   one). §3.1's rule stands: the receiver writes a series row and its points in the same
   transaction, so the reference holds by construction, and an FK would put a constraint
   check on the hottest insert path in the system for no integrity gain.
2. **Everything on `otel_api_requests` except `vendor`, `request_id`, `org_id` and
   `received_at` is nullable**, including columns the design types as plain `text`/`bigint`.
   The receiver runs one transaction per export body: a NOT NULL violation on a single
   malformed record would abort the whole batch, contradicting the receiver's contract that
   a valid body with unmapped records answers 200 + `partialSuccess` and never a 5xx.
   Record-level validation belongs in the receiver, where it can count and report.

### 3.8 Forecast tables — `forecast_runs`, `forecast_scope_series`

**Adopted**, owned here under §3.1's single-definition rule, designed in
`docs/plan/2026-10-05-forecast-dashboard-integration.md` §3.4/§3.5. They hold the
precomputed dashboard forecast (synthetic demo): one loaded `forecast_export.json`
(schema `dashboard-forecast/1.x`, written by the forecast package's dashboard export) is one
`forecast_runs` row plus one `forecast_scope_series` row per scope x preset. The API reads them;
nothing fits or prices at request time, and mainline never imports `forecast.*`.

| Table | Columns |
|---|---|
| `forecast_runs` | `run_id` text PK, `origin` date, `data_through` date, `generated_at` timestamptz, `meta` jsonb, `loaded_at` timestamptz |
| `forecast_scope_series` | `run_id`, `scope_type`, `scope_id`, `preset_id` (composite PK), `status` text, `payload` jsonb |

- **`run_id`** is the first 16 hex characters of the sha256 of the export file's bytes: the same
  file always gives the same id, any other file (even one differing only in `generated_at`)
  another.
- **`meta`** holds everything run-wide that is not a column: `schema` (the version string, so a
  reader can see the minor), `timezone`, `band`, `quantile_grid`, `dates`, `periods`, `presets`,
  and `scopes` (`[{scope_type, scope_id, name}]` in export order -- the display names exist
  nowhere else, and they are not the directory's: the export says `QA` where the directory says
  `Qa`). Any extra top-level key of a newer minor version is kept under its own name
  (must-ignore); extra keys inside a scope object other than its type, id and name are not kept.
  `generated_at` may carry any UTC offset and is stored normalised to UTC. The loader checks the
  document's shape, not the numbers in the arrays or whether `timezone` is a valid zone name:
  readers must defend against those. `loaded_at` is the database's `now()` at load time (the
  load transaction's start, so up to the whole synthetic load earlier than the write).
- **`scope_type`** is `org | team | repo`; `scope_id` is the dashboard id (the directory org id,
  a team id, a repo id). No FKs: ids are checked against the dataset by the synthetic loader,
  not by constraints.
- **`status`** is the cost run's scope status: `complete`, `conditional`, `inactive` or
  `incomplete`.
- **`payload`** is the export's series entry **without `status`**: `{daily, periods}` for a
  scope with numbers (about 15 KB per row on the example org), `{}` for `incomplete`; an extra
  key a newer minor adds to a series entry is carried along. Money is integer `usd_micros`, as
  in the export.
- **One run at a time.** The loader (`forecast_load.replace_run`; see "Forecast load" in
  `src/server/storage/README.md`) takes a `SHARE ROW EXCLUSIVE` lock on both tables, deletes
  every row of both and inserts the new run, inside the caller's transaction; it never commits.
  A failure rolls the replace back and the previous run stays; two concurrent loaders are
  serialised. `synthetic_load --replace` truncates both tables (`truncate_all`).

Migration `231990ad3837` creates both tables; the downgrade drops them (a run is reproducible
from its export file).

### 3.9 `budget_cycle` — the org's budget cadence

**Adopted**, designed in `docs/plan/2026-10-14-overview-ux-refresh.md` §3 (Track A1). One row
per org says what a budget period is; read and written only through
`server.api.manager.budget_cycles`.

| Table | Columns |
|---|---|
| `budget_cycle` | `org_id` text PK, `kind` text (`month\|quarter\|half\|year\|sprint\|custom`), `recurrence` text (`recurring\|one-time`), `sprint_weeks` int, `anchor_date` date, `anchor_number` int, `custom_end` date, `updated_at` timestamptz |

- **No row = the default cycle**: a recurring calendar month, which is how every deployment
  behaved before this table. Nothing seeds it.
- **Field use by kind.** `sprint`: `sprint_weeks`, `anchor_date` (start of the sprint numbered
  `anchor_number`). `custom`: `anchor_date` (first day) and `custom_end` (last day, inclusive).
  Calendar kinds use none and are always `recurring`. The API validates and nulls the unused
  fields; there are no CHECK constraints.
- **`budgets.period`** stores the cycle's label (`monthly`, `quarterly`, `half-yearly`, `yearly`,
  `sprint`, `custom`; the one mapping is `budget_cycles.CYCLE_LABELS`). Rows under another label
  stay stored but are not read, so a kind change shows "no budget" until amounts are entered
  for the new cadence. Amounts are never converted.

Migration `f3a8d1c6b947` creates the table; the downgrade drops it (the configured cycle is lost,
`budgets` is untouched).

## 4. Data-access API

Public surface, re-exported from `src.server.storage`:

```python
class StorageConfig(BaseModel):
    dsn: str                      # required; ${VAR}-interpolated by the shared config loader
    admin_dsn: str | None = None  # only the CLI uses it (init/--force); services never see it
    pool_size: int = 5            # steady connections held by this process
    max_overflow: int = 5         # extra burst connections above pool_size
    pool_timeout_s: int = 30      # wait for a free connection before erroring
    pool_recycle_s: int = 1800    # retire idle connections before NAT/LB/RDS idle cutoffs
    pool_pre_ping: bool = True    # liveness-check on checkout; survives failovers/restarts
    connect_args: dict = {}       # passed through to psycopg (e.g. prepared-statement opts)
    echo_sql: bool = False        # dev aid; logs SQL statements
    queries_dir: str = "config/queries"

def get_engine(cfg: StorageConfig | None = None) -> sqlalchemy.Engine
    # Process-wide singleton; first call builds it (cfg required), later calls return it.
    # Raises RuntimeError if first call omits cfg.

@contextmanager
def session_scope() -> Iterator[Session]
    # THE transaction boundary (arch §10.2): commit on clean exit, rollback on exception,
    # always close. "Write results + mark progress" = one session_scope block.

class QueryCatalog:
    @classmethod
    def load(cls, queries_dir: Path) -> "QueryCatalog"
        # Scans *.yaml, compiles every statement to sqlalchemy.text() at load time.
        # Raises QueryConfigError on: duplicate (file_key, name), empty statement,
        # unparseable YAML. Called once at process startup — errors fail the boot.
    def get(self, file_key: str, name: str) -> sqlalchemy.TextClause
        # KeyError with a "did you mean" listing on miss.

Base: DeclarativeBase   # the single metadata root; schema/ modules subclass it
```

Usage shape in a consuming module (repositories live with their module, not here):

```python
with session_scope() as s:
    s.execute(catalog.get("ingest", "insert_events_if_absent"), rows)
    s.execute(catalog.get("ingest", "touch_producer_last_seen"), {...})
```

**Async ingest:** the engine is sync everywhere (arch §15 posture; volumes make async
unnecessary). Ingest's FastAPI handlers declare their DB endpoints as plain `def` (not
`async def`) so FastAPI runs them on its threadpool. Seam: swap `create_engine` for the
async variant if a measured need appears; the catalog and models are unaffected.

**Errors:** SQLAlchemy exceptions propagate — no error-in-result wrapping. The module
defines only `QueryConfigError` (bad YAML at load) and `SchemaVersionError` (§6 fail-fast).

### 4.1 Connections, pooling, and hosting

**Hosting never touches code.** Anything host-specific Postgres understands — host, port,
`sslmode=require` (RDS), proxy endpoints — rides inside the DSN string; local vs. RDS vs.
proxied is a different `TALIUM_DB_DSN` value, nothing else.

```yaml
# config/storage.yaml
storage:
  dsn: ${TALIUM_DB_DSN}          # e.g. postgresql+psycopg://…rds.amazonaws.com:5432/econ?sslmode=require
  admin_dsn: ${TALIUM_DB_ADMIN_DSN}
  pool_size: 5
  max_overflow: 5
  pool_recycle_s: 1800
  pool_pre_ping: true
```

- **Pools are per process** (SQLAlchemy QueuePool). Each process builds its own
  `StorageConfig` — shared defaults from `storage.yaml`, overridable in the process's own
  config section (ingest larger, workers smaller). Total fleet connections =
  Σ(pool_size + max_overflow) per process; keep well under the server's
  `max_connections` (~30 vs. 100 default at current process count — ample).
- **Load balancing is not client-side.** Writes go to the primary; RDS/Aurora endpoint
  DNS handles failover. If connection counts ever outgrow the server (many workers), a
  PgBouncer / RDS Proxy goes in front — DSN change plus, for transaction-pooling mode,
  disabling psycopg prepared statements via `connect_args`. Read replicas remain the API
  boundary's seam (arch §16): an optional `reader_dsn` if that day comes; not designed now.

## 5. Producer store

`PgProducerStore` implements the existing `msging.ProducerStore` protocol
(`get / get_by_key_prefix / upsert / touch_last_seen / set_revoked`) against the
`producers` table, selected via msging's `make_store()` config switch
(`backend: postgres`). It stores the whole `ProducerRecord` in the `record` JSONB
(§3.2) — the same shape `FileProducerStore` writes to disk — so the backends are
interchangeable. `get_by_key_prefix` uses the generated `key_prefix` column; `upsert` is
`INSERT … ON CONFLICT (producer_id)` (idempotent, arch rule 3);
`touch_last_seen`/`set_revoked` are `jsonb_set` updates on `record`. Each method is one
`session_scope` block. Follow-up (§3.2): a `get_by_key_hash` for direct O(1) `verify()`
lookup against the unique `key_hash` index.

## 6. Migrations — two-phase policy

Migration vs. reset is an **explicit admin decision**; no verb ever falls back to
another's behavior. Services check the database revision at startup and fail fast with
`SchemaVersionError` on mismatch — they never create or migrate schema.

**Phase 1 — recreate era (now).** No migration files exist. Schema changes are model
edits; databases are rebuilt at will (`init --force`).

**Phase 2 — migration era (production).** Migration files exist. Every change: edit models
→ `revision` (auto or manual; admin reviews the script) → `migrate` (applies to head).
The transition needs no special verb — the first `revision --autogenerate` produces
`0001` = the full current schema, and a fresh database gets it by running `migrate` from
empty. (Adopting a *pre-existing* database into migration tracking is future work, §12.)

### CLI verbs (`cli_db.py`, wrapper `utils/db.sh`)

| Verb | Behavior | Destructive |
|---|---|---|
| `init` | Tables exist → report and exit, touch nothing. Absent → create database/role if needed (admin DSN), then build schema: phase 1 from models; phase 2 by running the migration chain from empty (fresh installs prove the migrations). | no |
| `init --force` | Drop everything, rebuild unconditionally. The only destructive verb. | **yes** |
| `revision --autogenerate -m "…"` | **Auto:** diff the models and write a migration script, for review. First one = the whole schema. | no |
| `revision -m "…"` | **Manual:** empty script to hand-write — data backfills, renames autogen can't infer. | no |
| `migrate [--sql]` | Apply all pending migrations to head. `--sql` renders the script without executing (offline review). | no |
| `status` | Current revision vs. head + model drift check (`alembic check`). | no |
| `seed` | Load environment-specific initial data (default org, pricing rows). Separate from init by design (arch §10.3). | no |
| `import` | Load from a dump/another instance. Mechanism deferred. | no |
| `org-token issue --org <id> [--descriptor <json>]` | Mint a `tk_org_` OTLP ingest token (§3.7). Prints the plaintext key **once** — it is replaced, never recovered (arch §18.1); only its sha256 is stored, and the log line carries the hash prefix, not the key. | no |
| `org-token revoke --hash-prefix <p>` | Flip `revoked` on the one token whose hash starts with `<p>`. Refuses on zero or multiple matches rather than guessing — revoking the wrong org's fleet is silent until its exports stop. | no |
| `org-token list --org <id>` | Inventory: hash prefix, active/revoked, issue date, descriptor. | no |

Exit codes: 0 success / no-op (including `init` finding tables present); 1 error;
2 refused (e.g. `init` on a non-empty DB without `--force`). All verbs log structured
events (§8).

## 7. Schema visibility

`cli_schema_dump.py` emits, from `Base.metadata` alone (no database connection):

- `--ddl` — the full CREATE TABLE/INDEX DDL via SQLAlchemy's mock engine, Postgres
  dialect. What you review is what `init` runs.
- `--er` — a Mermaid `erDiagram` (tables, columns, FK edges) written to
  `docs/modules/storage-schema.md`. The whole-data-model view is a generated artifact,
  regenerated on schema change, never hand-maintained.

## 8. Logging

structlog logger `storage`. Declared events: `queries_loaded {files, query_count}`,
`db_init {mode, force, revision}`, `db_migrate {from_rev, to_rev, scripts}`,
`db_revision {revision, auto}`, `schema_version_mismatch {found, expected}`. Statement
*names* (file_key/query name) may appear in logs; bound parameter values never do.

## 9. Testing & debugging

- **Real Postgres, no SQLite stand-in** — JSONB and `ON CONFLICT` are load-bearing.
  Tests read `TALIUM_TEST_DSN`; suite skips with a clear message when unset. Dev
  convenience: a docker one-liner documented in the test README.
- **Fixtures:** session-scoped fixture runs `init --force` against the test database;
  per-test transaction-rollback fixture keeps tests independent.
- **Golden DDL:** the `--ddl` dump is snapshotted in `tests/fixtures/`; schema changes
  show up as a reviewable golden diff in the PR.
- **Idempotency tests** (arch rule 3): `init` twice = no-op second time; `PgProducerStore.
  upsert` twice = one row; phase-2 `migrate` on an at-head database = no-op.
- **Debugging:** `echo_sql: true` logs every statement; `status` answers "what revision
  is this database and does it match the models."

## 10. Dependencies

Added to `requirements.txt`: `sqlalchemy>=2.0`, `alembic>=1.13`, `psycopg[binary]>=3.1`
(driver: `postgresql+psycopg`).

## 11. Open items

| # | Item | Working assumption |
|---|---|---|
| 1 | `import` verb mechanism | Deferred until a concrete need (arch §10.3). |
| 2 | Query catalog hot-reload | Not planned; restart-to-reload per arch §12. |
| 3 | Table set beyond §3.2–3.5 | `events`, `orgs`, goods/PR — designed with their consuming modules (tracked in §3.6). |

## 12. Future work (not implemented)

### 12.1 `stamp` — adopting a pre-existing database into migrations

**Not built now; recorded so the operator knows the escape hatch exists when needed.**

`stamp <rev>` would write a revision into the database's Alembic bookkeeping
(`alembic_version`) **without executing the migration SQL** — it records "this database is
already in the shape of revision X," the opposite of `migrate`, which runs the SQL to move
a database forward.

**Why it would be needed.** Our workflow never requires it: dev recreates freely, and a
fresh production database runs the migration chain from empty. `stamp` is only necessary
when a **long-lived database that cannot be dropped** must start following migrations
mid-life. Concretely: a staging database built for weeks by `init` (create_all) already
has every table. Introduce migrations, and `migrate` on that database tries to run
`0001`'s `CREATE TABLE producers` → *error: relation already exists*, because Alembic sees
no recorded revision and assumes the database is empty. `stamp 0001` records it at `0001`
without re-creating anything, and the next real migration (`0002`) then applies cleanly on
top.

**Also useful for recovery:** if a migration half-applies and is fixed by hand, `stamp`
re-aligns Alembic's marker with the database's true state.

Deferred because it only matters once a keep-forever database predates migrations — a
situation we do not yet have. Add the verb (a thin wrapper over `alembic stamp`) when that
first arises.
