# Plan: integrate the causal graph into `feat/github-acquisition-poc`

**Status:** In progress · **Date:** 2026-08-29 · **Working branch:** `feat/causal-graph-integration` (off `feat/github-acquisition-poc`; `integ` kept read-only, copied from)
**Builds on:** `integ` @ `a2ce765` (causal_graph engine, schema 4.4.0-elect, unified-normalizer base) · `feat/github-acquisition-poc` @ `d56dcbc`
**Companion (all rationale, measurements, decisions):** `docs/design/2026-08-28-causal-graph-github-integration-feasibility.md`. This plan operationalizes it and does not restate it.

---

## Nutshell

Bring the `analysis/causal_graph` engine and an **integ-based unified normalizer** onto the github
branch, then replace the `fold` worker's raw-event JSONL extraction with a **graph-backed
`SessionFold`** — reaching **zero residual fold**. The graph is built **only from JSONL session
files**; the GitHub webhook stack (`ingest/git`, `pr_class`) is untouched.

Load-bearing decisions (settled in the companion, not re-litigated here):
- **integ's normalizer is the base**; github's git/PR fields (`pr_link` event, path-based `repo_id`)
  layer on; github's `fold` is re-pointed at it (feasibility §2/§2a).
- **Dropped from parity:** `api_error`/`info.errors`/`retried_tokens_est` (§4.C), `cache.writes_5m`/
  `writes_1h` TTL split (§4.D). Allowlisted, not regressions.
- **Zero-residual path:** port §4.B heuristics + exactly one builder-enrich (`repo_id → graph.meta`);
  everything else is already on nodes, a pricing layer, or a shared source-gap (§4.D).
- **Storage (FROZEN):** per-session `session_graph` rows are the write-safe source of truth; one
  per-family `session_tree` row holds a **flat `{session_id: graph_dict}` map** (master + all
  descendants, any depth; hierarchy by refs, not nesting) as the single-fetch read cache.
- **Validation is a per-field `session_info` differential** (legacy fold vs graph-backed) over a real
  corpus, with an explicit allowlist, backstopped by the branch's existing suites (§7).

Two steps are large enough to each carry their own focused sub-plan at execution time: **Step 1
(normalizer merge)** and **Step 4 (heuristic ports)**. This plan gives their approach, gates, and
files; it does not pre-write every field.

**Execution note — RESEQUENCED 2026-08-30.** A code check found the normalizer swap and the fold
replacement are **coupled**: the integ normalizer emits *per-record* assistant events, but the old
fold *sums* them → it would multi-count tokens. The only consumer of normalized events is the fold
(pr_class/git/otlp read other sources), so the swap only hurts the fold — which is being replaced
anyway. Chosen order (approved): **(A) land the graph engine first — purely additive — then swap the
normalizer and the graph-based fold together.** So the plan's Step 2 *engine landing* (2a) runs before
Step 1; the parts of Step 2 that need the normalizer (build-from-corpus, the tables, arrival wiring)
merge into the combined **Step 1+3** landing. Status: **Step 0 ✅ · Step 2a ✅ · §1 (normalizer) ✅ · §2b (storage) ✅ · §3 (GraphFold wired & live) ✅ · old-fold removal ✅ (EconFold deleted) · Step 4 (§4.B heuristic port) ✅ — all 8 fields graph-derived · Step 5 (retire residual) ✅ — zero-residual reached; close-out `docs/plan/2026-09-01-step5-zero-residual-closeout.md`. INTEGRATION COMPLETE.**
Sub-plans: combined `docs/plan/2026-08-30-combined-normalizer-graph-fold.md`; old-fold removal
`docs/plan/2026-08-31-old-fold-removal.md`; Step-4 §4.B port `docs/plan/2026-08-31-step4-heuristic-port.md`.

---

## Terms

- **Unified normalizer** — the single `collector/normalizer/claude_code.py` after the merge: integ base + github git/PR fields.
- **GraphFold** — a new `SessionFold` implementation that builds/loads the graph and computes `SessionInfo` from graph APIs. Replaces `EconFold`.
- **Residual fold** — the temporary shim inside GraphFold that still reads raw events for not-yet-ported §4.B fields. Target: removed by Step 5.
- **Differential runner** — the harness that deep-diffs two `SessionInfo` objects per field against an allowlist (feasibility §7 Layer 3).

---

## The seams this plan uses (evidence)

```
SessionFold (Protocol)        src/server/worker/fold_api.py:90   fold(state, events)→SessionFoldResult; rollup_subagents(...)→SessionInfoDelta
EconFold (impl to replace)    src/server/worker/fold/driver.py:96
  instantiated at             src/server/ingest/app.py:56 (arrival fold) · src/server/worker/cli_work.py:77 (worker)
persist_session_info(...)     src/server/worker/persistence.py:49
load_children_info(...)       src/server/worker/persistence.py:84 (rollup input)
build_graph(session)→dict     src/analysis/causal_graph/builder.py (one normalized sessions[] member → graph dict)
GraphContext.load_self/tree   src/analysis/causal_graph/context.py (master + subagent family via a resolver)
named APIs / select / query   src/analysis/causal_graph/named_api.py, query.py
session_info shape contract   tests/sample-data/session-info-shape-contract.json (the oracle keyspace)
```

`fold(state, events)` and `build_graph` consume the **same** normalized event list — no second parse.
Corpus measured for this plan: `~/.claude/projects`, **80 sessions** (used for prevalence numbers in
the companion; reused as the differential corpus).

### Builder model — ground truth (verified in `cli_graph.py` / `family.py` / `context.py`)

**Each session has its own graph.** `build_graph(session)` (`builder.py`) consumes exactly **one**
`sessions[]` member `{vendor, session_id, parent_session_id, events[]}`, does **no I/O and no
discovery**, and returns one graph dict. A **`Family`** is an *assembly* of independent per-session
graphs, not one monolithic graph:

```
master   = the session with parent_session_id == null
children = every subagent session, its OWN graph, keyed by session_id
links    = master Spawn nodes carry child_session_id in refs; load_tree(resolver) walks them
```

Two ways to assemble a family, and **the difference is the whole HTTP story**:

| | `build_family(envelope)` — CLI | per-session `build_graph` + `load_tree(resolver)` — server |
|---|---|---|
| Input | one **inlined** envelope `{sessions:[master, sub1, …]}` (normalizer read a completed folder) | one session at a time, arriving as separate HTTP batches over time |
| Assembly | all members built together, in one call | each built on arrival + persisted; family stitched at read time by the resolver |
| Assumption | whole family present at once | **none** — partial family is valid (`resolve` returns None for a not-yet-arrived child) |
| Used by | `cli_graph build` / `build-all` (skips `subagents/` inputs to avoid double-building) | **this integration** |

So `build_family` is a CLI-only convenience. The server **must not** use it (it never holds an inlined
family). It uses `build_graph` per arrival + the DB resolver — which is exactly what the public API
was designed for.

### Pipeline wiring — when the graph JSON is built (the answer)

There is **no separate "build the graph" pipeline stage.** The build is the first act of
`GraphFold.fold()`, which runs at the two existing `SessionFold` call sites — and the roles split
exactly along the two protocol methods:

```
ARRIVAL  (ingest handler, ingest/app.py → make_handler, per batch — master AND each subagent
          each arrive as their OWN batch, keyed by session_id, linked by parent_session_id)
  GraphFold.fold(state, events):
    1. session = {session_id, parent_session_id, vendor, source, events}   # ONE session, from the full deduped blob
    2. graph = build_graph(session)                                         # self only; NOT build_family
    3. UPSERT session_graph(session_id, parent_session_id, graph_json, schema_version, built_at)
    4. load_self(graph); derive SELF-scoped session_info via named APIs / ports
    5. return SessionFoldResult                                            # same contract EconFold returns

WORKER   (rollup topic, cli_work.py → make_rollup_handler)
  GraphFold.rollup_subagents(parent, children):
    1. load_self(parent_graph); load_tree(resolver)
         resolver(ids) := SELECT session_id, graph_json FROM session_graph WHERE session_id = ANY(ids)
         # load_tree walks the master's Spawn.child_session_id refs breadth-first; a not-yet-arrived
         # child simply resolves to None → a PARTIAL family, which is valid (context.py resolve)
    2. compute TREE-scoped fields (inclusive_tokens, subagents map, delegation_ratio)
       via budget(scope="tree"/"subagent") over the loaded family
    3. return SessionInfoDelta
```

So **each session's own graph is built when its batch arrives and folds** — a subagent's graph is
built by the subagent's arrival fold; the master's by the master's. The worker's rollup never builds
a graph; it only **reads** the family the arrival folds already persisted, via the DB resolver. This
resolves the earlier "arrival vs worker" ambiguity: **arrival builds (self, per session), worker reads
(tree, via resolver).**

**Ordering / partial families.** The master's rollup may run before every subagent has arrived —
the resolver returns a partial family (missing children → `None`), and the rollup is **re-triggered**
when a late child lands (the existing dispatch coalesce + re-trigger the fold already relies on). Each
pass recomputes from the then-current `session_graph` rows, so it is idempotent and self-correcting —
the same discipline as `EconFold`'s recompute-from-full-blob. No child is ever double-counted: one row
per `session_id`, upserted.

### The tree is a reusable read surface — `GraphService`, decoupled from rollup

Tree **assembly** and subagent **rollup** are different operations and must not be coupled:

```
load_self + load_tree(resolver)   READ: assemble the in-memory Family from session_graph rows.
                                  A few batched SELECTs (one per nesting depth) + object assembly.
                                  No rebuild, no recompute. Available to ANY consumer.
GraphFold.rollup_subagents        ONE consumer: materializes a few tree-scoped fields
                                  (inclusive_tokens, subagents, delegation_ratio) into session_info
                                  for the existing dashboard.
```

**Source-of-truth model:**
- `session_graph` rows (per-session graphs) are the **source of truth** — written once on arrival,
  independent of the fold path.
- The assembled `Family` is the **reusable read surface**, reconstructable on demand from those rows.
- `session_info` is a **derived view** (what today's dashboard reads), not the source of truth. New
  features query the graph directly and never touch the rollup.

**`GraphService.load_family(session_id) -> Family`** — a thin accessor wrapping `load_self` +
`load_tree(resolver)`. The **single entry point** every consumer uses (dashboard, future analytics,
ad-hoc queries); `rollup_subagents` becomes just another caller. Loading a tree therefore **never**
requires a rollup.

### Storage — FROZEN (2026-08-30)

**Two tables. Per-session rows are the write-safe source of truth; one flat per-family row is the
single-fetch read cache.** Each subagent graph is stored *twice, deliberately*: authoritatively in
its own row, and as a copy inside the family's flat tree blob.

```
session_graph   -- WRITE path / source of truth: one row per session (master OR subagent)
  session_id        text primary key,      -- a subagent writes its OWN row on arrival, order-free
  parent_session_id text null,             -- null on masters; immediate parent for a subagent
  graph_json        jsonb not null,        -- build_graph output for THIS session only
  schema_version    text not null,         -- graph contract version, for migration
  built_at          timestamptz not null
  -- index on parent_session_id; db-core named queries only (no ORM)

session_tree    -- READ path / derived cache: one row per FAMILY (keyed by the ROOT master)
  master_session_id text primary key,
  tree_json         jsonb not null,        -- the FLAT map below (master + ALL descendants, any depth)
  member_count      int not null,          -- lets a reader detect "not all children present yet"
  built_at          timestamptz not null
```

**`tree_json` is a FLAT map keyed by `session_id` — never nested.** The `Family` is deliberately flat
(`Family.children` is a dict keyed by session_id; the module *"flattens descendants rather than nesting
per parent"* — `_cli_store.py:40-41`). Nesting depth does **not** change the storage shape: a
grandchild is just another sibling entry.

```json
{
  "master_session_id": "M",
  "graphs": {
    "M":  { "session_id": "M",  "parent_session_id": null, "nodes": [...], "roots": [...], "meta": {...} },
    "S1": { "session_id": "S1", "parent_session_id": "M",  "nodes": [...], "roots": [...] },
    "S2": { "session_id": "S2", "parent_session_id": "M",  "nodes": [...], "roots": [...] },
    "G1": { "session_id": "G1", "parent_session_id": "S1", "nodes": [...], "roots": [...] }   // grandchild — flat, not nested under S1
  }
}
```
Each value is one `Graph.to_dict()` (the persisted graph envelope). **The hierarchy lives in
references, not JSON structure:** each graph's `parent_session_id`, plus the `Spawn.child_session_id`
refs inside graphs (`M`→`S1`,`S2`; `S1`→`G1`). `load_tree` reconstructs the tree by BFS-walking those
refs against the flat map — at any depth.

**Why not write each subagent straight into the one tree row on arrival:** incremental HTTP arrival
breaks it three ways — (1) a child can arrive before its master (no tree row yet); (2) concurrent
siblings read-modify-write the same row → lost updates; (3) a **grandchild knows only its immediate
`parent_session_id`, not the root master**, so it can't address the right family row without a
parent→root walk. Per-session rows sidestep all three: each session writes its own row; the tree is
assembled later by walking refs.

**The three flows:**
```
ARRIVAL     (per session, write-safe, no coordination)
  build_graph(session) → UPSERT session_graph(session_id, parent_session_id, graph_json, …)

MATERIALIZE (at rollup / when the family is complete-enough; idempotent, re-triggered on late children)
  BFS from the master over Spawn.child_session_id refs, one batched
    SELECT graph_json FROM session_graph WHERE session_id = ANY(:ids)   -- per depth level
  → flat {session_id: Graph.to_dict()} → UPSERT session_tree(master_session_id, tree_json, member_count, built_at)

READ        (GraphService.load_family — the single fetch)
  SELECT tree_json FROM session_tree WHERE master_session_id = :m       -- ONE row
  load_self(tree_json.graphs[m]); load_tree(in-memory resolver over tree_json.graphs)  -- zero further DB reads
  fallback: session_tree stale/absent → assemble from session_graph rows (DB resolver), then re-materialize
```

**DB fetch ≠ in-memory assembly** (the resolver is the seam — `load_tree` always stitches dicts in
memory; only its resolver's *source* changes):

| Load path | DB round-trips | resolver reads dicts from |
|---|---|---|
| per-session rows (`session_graph`) | **N rows** (~1–2 batched `SELECT`s) | the database |
| materialized tree blob (`session_tree`) | **1 row** (whole family) | **memory** (the flat `graphs` map) |
| in-process `Family` cache | 0 | n/a (object reused, no re-assembly) |

"Single fetch" = one DB read of the flat tree row; the in-memory stitch still runs unless the `Family`
object itself is cached. **Correctness needs only `session_graph`; `session_tree` is a regenerable
cache** (evictable, or even Redis) — never a second source of truth.

**Cadence & idempotency:** the graph is **rebuilt from the full deduped blob every fold pass**, not
patched incrementally — `build_graph` is pure (dict→dict), so a rebuild is idempotent and the persist
overwrites the session's prior graph. This mirrors `EconFold`'s "recompute from the full blob every
pass" discipline (`fold/driver.py`), so it inherits the same replay/resend safety and comparable cost
profile. (If arrival-latency ever matters, the build can move to a dedicated worker `graph` topic
without changing the seam — not the default.)

---

## Steps

### Step 0 — Freeze the oracle + build the differential harness (FIRST) — ✅ DONE 2026-08-30

Landed under `tests/graph_migration/`:
- `harness.py` — the oracle: `load_session_source` → real `ClaudeCodeNormalizer` → self-fold every
  session → bottom-up per-parent `rollup_subagents` → serialized `session_info`. Deterministic (fixed
  `sent_at`/row time; `EconFold` recomputes timestamps from events regardless). Verified against the
  `session-subagents` fixture: `inclusive_tokens > tokens_total`, subagents map populated.
- `differential.py` — the runner: `flatten` → per-field-path `diff` (`removed`/`added`/`changed`) →
  `regressions` filtered by an `AllowRule` allowlist. Firm entries: `info.errors*`, `error_count`
  (§4.C), `info.cache.writes_5m`/`writes_1h` (§4.D). Provisional entries: `models.*`, `tokens_total`,
  `info.tokens_total`, `info.inclusive_tokens` (§4.A token-collapse — refine in Step 3 vs observed diffs).
- `test_differential_runner.py` — **committed CI test** on the *sanitized* recorded fixtures only
  (golden `baseline_fixtures.json`; regen `GRAPH_ORACLE_REGEN=1`). Self-tests: flags an injected
  1-field diff, flags an added field, honors the dropped allowlist, honors token-collapse *scoped*
  (a sibling non-token change still fails; a model *removal* still fails). 11 tests green.
- `cli_baseline.py` — generates the **private** local-corpus baseline into `.local-baseline/`
  (gitignored; real paths/titles never committed) for the Step 3-5 A/B. **Ran clean over the full
  local corpus: 51/51 sessions, 0 errors.**

Corpus coverage (manifest): single 45 · git 40 · api_error 6 · compaction 5 · multi_subagent 6.
**Gap:** `resume` and `otel_trueup` are **not derivable from a single JSONL family** — before Step 5
sign-off, cover them from the OTel fixtures (`tests/fixtures/otel/`) + a resume fixture (see Open Q).
- **Not doing:** changing any extraction; committing private baselines; the A/B run itself (Step 3+).

### Step 1 — Unified normalizer (integ base + github git/PR) — *sub-plan at execution*

- Port onto github: integ's `collector/normalizer/{claude_code,config,identity,tokens}.py` +
  `src/common/events/` (schema contract). Re-layer github's **`pr_link` event** (CC `pr-link` record)
  and **path-based `repo_id`** (`cwd`, §2a). Drop nothing github's consumers read from the JSONL.
- `repo_id` ladder near-term: **`pr_link` > `path`** (no remote-scrape rung, no hook — §2a).
- **Gate (Layer 1, the hardest):** the unified normalizer's event/payload keyset is a **superset** of
  what github emits today — every key github's `fold`/`pr_class`/`git`/`otlp` reads still present
  (assert against `tests/sample-data/normalizer-shape-contract.json`). Graph's
  `test_normalized_contract.py` passes. A spot corpus normalizes with no schema error.
- **Not doing:** the SessionStart-hook plugin (deferred, §2a); the webhook stack (orthogonal).
- **Risk to budget here:** hidden regressions in `pr_class`, `git_map`, `otel_trueup` that read
  normalizer output. Their suites (Step gate) are the detector.

### Step 2 — Land the causal_graph engine + build/persist/resolve

**2a — engine landing ✅ DONE 2026-08-30.** Copied from integ (all additive, no collisions):
`src/analysis/causal_graph/` (22 modules), `src/common/contracts/causal_graph/`, `src/common/events/`
(schema contract), `src/common/metrics.py`, `config/causal_graph/{query,reasoning}.yaml`,
`utils/graph.sh` + `utils/gen_graph_interface.py`, and `tests/analysis/causal_graph/` +
`tests/fixtures/causal_graph/` + `examples/causal_graph/`. New deps in `requirements.txt` and
installed: `lark`, `google-re2`, `tree-sitter`, `tree-sitter-bash`, `tiktoken`. **Engine suite: 488
passed, 2 skipped; existing branch suites unaffected (314 passed).** `test_normalized_contract` green
— the schema and the builder's required fields are coherent, so the normalizer landing has a valid target.

**2b — build/persist/resolve (deferred into the combined Step 1+3 landing, needs the normalizer):**
- Copy `src/analysis/causal_graph/` + `src/common/contracts/causal_graph/` + its tests onto github.
- Add the **two tables** (schema frozen in *Storage — FROZEN*) + their db-core named queries:
  - `session_graph`: `upsert_session_graph`, `session_graphs_by_id` (`WHERE session_id = ANY(:ids)` resolver).
  - `session_tree`: `upsert_session_tree`, `session_tree_by_master`.
- Implement `graph_build.py`: `build_graph(session)` (per-session, **not** `build_family`) → **upsert**
  into `session_graph`; the **DB resolver** wrapping `session_graphs_by_id`; and **`materialize_tree(master_session_id)`**
  (BFS from the master over `Spawn.child_session_id` refs → flat `{session_id: Graph.to_dict()}` →
  `upsert_session_tree`).
- Implement **`GraphService.load_family(session_id) -> Family`** — the single reusable tree accessor:
  **fresh `session_tree` → one-fetch + in-memory resolver; stale/absent → row-assembly (DB resolver)
  then re-materialize.** Decoupled from fold/rollup; `GraphFold` and every future feature go through it.
- Exercise it via an **offline corpus runner** (build+persist+materialize for the 80 sessions) — the
  engine is validated but **not yet in the live fold path** (that wiring is Step 3).
- **Gate (Layer 2):** graph builds for every corpus session; loaded family topology == known spawn
  tree (incl. a **nested grandchild** case — flat map keyed by session_id, refs walked); every tool
  node with a github-side outcome has non-empty `attrs.result`; graph unit tests green; zero extraction
  warnings on clean sessions. **Equivalence checks:** (a) per-session `build_graph` + DB resolver ==
  CLI `build_family` over the inlined envelope; (b) **`session_tree` one-fetch load == `session_graph`
  row-assembly** (same `Family`) — proves the read cache matches the source of truth.
- **Not doing:** wiring the graph into `session_info` / the live path yet (Step 3).

### Step 3 — GraphFold: structural cutover (§4.A) behind `SessionFold`

- New `GraphFold` implementing the `SessionFold` protocol, per the *Pipeline wiring* above:
  `fold()` calls `graph_build` (build + persist the self graph) as its **first act**, then computes
  the **§4.A** fields from named APIs — `models`/`tokens_total`←`budget`, `tools`←`list_tools`,
  `files`←`goods`, `turns`←`turns`+`budget(by=turn)`, `pr_list`/`git_commands`←`gitops`,
  `mcp_servers`, `title`←meta, `compactions`←`select("Compaction")`; `rollup_subagents()` **is the
  `materialize_tree` point** — it (re)builds the `session_tree` blob and then obtains the family via
  **`GraphService.load_family`** (one consumer of it, not the owner) to compute tree-scoped
  `subagents`/`inclusive_tokens`/`delegation_ratio` from `budget(scope="tree"/"subagent")`. **Reminder
  (query engine):** a `scope="tree"` API includes a subagent only if its graph is loaded; an unloaded
  child makes the result partial with a `child_graph_unloaded` warning — so tree APIs run *after*
  `load_family`, never on a bare `load_self`.
- Keep a **residual fold** inside GraphFold for §4.B/§4.C fields (still reads events).
- **This is the step that wires the builder into the live pipeline:** swap `EconFold`→`GraphFold`
  behind the protocol at `ingest/app.py:56` (arrival — where the build now happens) and
  `cli_work.py:77` (worker rollup — reads the family). `cost_usd` stays owned by `otel_trueup` —
  GraphFold never writes it (as `EconFold` never did).
- **Gate (Layer 3):** differential over the corpus shows **only allowlisted diffs** — the §4.A
  token-collapse correction is the expected one; everything else in §4.A byte-identical.
- **Not doing:** porting §4.B; touching pricing (reused unchanged from `common.pricing`).

### Step 4 — Port heuristics §4.B/§4.D + the one enrich — *sub-plan at execution*

- Port field-by-field, each behind its own green differential: `segments`/`beats`, `session_type`,
  `story`, `practices`, `interrupted`, `carry`, `resume_cost`, `read_then_discard`, `mcp_servers`
  bytes — rewriting each heuristic to read graph nodes (`select`/`query`), **not** raw events.
- **Enrich (one item):** lift path-based `repo_id` from `session_start` into `graph.meta` (mirrors
  `title`), so `repo`/`repo_id` are graph-derived. No assistant-node attr additions (TTL split
  dropped, §4.D).
- **Gate:** each ported field byte-identical (or allowlisted) before its residual code is deleted.

### Step 5 — Retire the residual fold (zero residual) — ✅ DONE (close-out: `docs/plan/2026-09-01-step5-zero-residual-closeout.md`)

- ✅ EconFold + `fold/` + `fold_stub.py` deleted (`802c75d`); GraphFold is the sole `SessionFold`. No
  parallel event-summing fold remains.
- **Gate — refined at close-out:** the literal *"no field reads the event list"* is not the end-state
  and was never achievable — a few signals aren't graph nodes (`pr_link`, the interrupt sentinel, raw
  per-message fields, the per-record story curve, beat patterns), so GraphFold makes **targeted,
  documented event reads** for those, enumerated in the close-out doc. The gate that holds: **nothing
  recomputes from raw events a quantity the graph already models** — every graph-modeled field routes
  through `budget()`/the node surface. A/B punch-list empty; `pytest -m "not slow"` 1830 passed; DB-gated
  289 passed.
- **Not doing:** removing the normalized event stream (the graph builds from it); the `repo_id →
  graph.meta` builder enrich (wouldn't eliminate `repo_info`'s pr_link/file-ref reads).

---

## Order & dependencies

```
Step 0   first — the oracle everything is judged against
Step 1   blocks 2 (graph needs the unified normalizer's shape)
Step 2   needs 1; blocks 3
Step 3   needs 2; blocks 4
Step 4   needs 3; per-field, internally parallel
Step 5   needs 4 complete
```
Steps are strictly ordered 0→5; only Step 4's per-field ports are independent of each other.

---

## Files

```
NORMALIZER (Step 1)
src/collector/normalizer/claude_code.py          merge: integ base + github pr_link + path repo_id
src/collector/normalizer/{config,identity,tokens}.py  port from integ
src/common/events/**                             schema contract (new on github)
tests/sample-data/normalizer-shape-contract.json update: superset assertion
GRAPH ENGINE (Step 2)
src/analysis/causal_graph/**                     copied from integ
src/common/contracts/causal_graph/**             copied from integ
src/server/storage/schema/…                      new: session_graph + session_tree tables (Storage — FROZEN)
config/queries/graph.yaml                         new: upsert_session_graph, session_graphs_by_id, upsert_session_tree, session_tree_by_master
src/server/storage/…/versions/…                  migration for both tables
src/server/worker/graph_build.py                 new: build_graph + upsert session_graph + DB resolver + materialize_tree
src/server/…/graph_service.py                     new: GraphService.load_family (session_tree fetch → fallback row-assembly)
EXTRACTOR (Steps 3–5)
src/server/worker/graph_fold.py                  new: GraphFold(SessionFold), §4.A + shrinking residual
src/server/ingest/app.py                         swap EconFold→GraphFold (:56)
src/server/worker/cli_work.py                    swap EconFold→GraphFold (:77)
src/server/worker/fold/**                        shrink then remove (Step 5)
src/server/worker/persistence.py                 persist graph json alongside session_info
TESTS
tests/…/differential_runner.py                   new: per-field session_info diff + allowlist
tests/…/graph_build/**, tests/…/graph_fold/**    new
DOCS (amend when they go stale)
docs/design/2026-08-28-causal-graph-github-integration-feasibility.md  status → in-progress
docs/modules/normalizer.md                       unified event/payload catalog
docs/modules/… (worker)                          fold → graph-backed extraction
```

---

## Verification

Per step (each names an assertion that fails if reverted):
```
0  baseline session_info captured for all corpus categories; runner flags an injected 1-field diff
1  unified normalizer keyset ⊇ github's today; pr_class/git/otlp suites green; graph test_normalized_contract green
2  build_graph succeeds for 80/80 corpus sessions; family topology == spawn tree (incl. nested grandchild); tool result non-empty where github had an outcome; session_tree one-fetch load == session_graph row-assembly
3  §4.A differential: only the token-collapse diff (§4.A) present; all other §4.A fields byte-identical
4  each ported field byte-identical/allowlisted before its residual code is deleted; repo_id present in graph.meta
5  no raw-event read remains for session_info (grep); full corpus differential = allowlist only
```
Whole-suite bar: `PYTHONPATH=src venv/bin/python -m pytest` green across `tests/collector/normalizer`,
`tests/analysis/causal_graph`, `tests/server/{api,ingest,storage}`, worker. Carry forward the
JSONL–OTel baseline (deduped JSONL matches OTel per-request; `cost_usd` co-write via `otel_trueup`
untouched). Idempotency re-asserted: refold/rebuild on a resend is a no-op; `rollup_subagents` still
fills `subagents`/`inclusive_tokens`/`delegation_ratio`.

---

## Open questions

| Question | Working assumption |
|---|---|
| ~~Where/how is the graph stored?~~ **FROZEN** (see *Storage — FROZEN*): `session_graph` per-session rows (write truth) + `session_tree` per-family **flat map** `{session_id: graph_dict}` (single-fetch read cache); nested depth = more flat entries, hierarchy by `parent_session_id`/`Spawn.child_session_id` refs. | Confirm the jsonb columns + `WHERE session_id = ANY(:ids)` + the `session_tree` upsert fit db-core's one-statement-per-transaction rule at Step 2. |
| ~~Does arrival build the graph, or the worker?~~ **Resolved** (see *Pipeline wiring*). | **Arrival builds each session's own graph inside `GraphFold.fold()`; the worker only reads the family in `rollup_subagents`.** Rebuilt from the full blob every pass. Move to a worker `graph` topic only if arrival latency later demands it. |
| Resolver query for the subagent family — one batch `session_id IN (...)` per depth (as `load_tree` expects)? | Yes; a single named query returning `{session_id, graph_json}` rows. |
| `repo`/`repo_id` — graph.meta (Step 4 enrich) or leave on a thin residual read? | graph.meta, so the residual truly reaches zero. |
| Corpus coverage — the 51-master local corpus (Step 0) has **no `resume` or `otel_trueup` families** (not derivable from one JSONL). | Cover both from fixtures before Step 5 sign-off: OTel true-up from `tests/fixtures/otel/`; author/record a resume fixture. api_error(6)/compaction(5)/multi_subagent(6) are thin but present — expand if a diff proves flaky. |
| Normalizer `redact.py` divergence — does integ's redaction strip anything `pr_class` needs? | Assume not; the Step 1 Layer-1 gate would catch it. Verify explicitly during the Step 1 sub-plan. |
```
