# Sub-plan: unified normalizer + graph build/persist + GraphFold (combined Step 1 + 2b + 3)

**Status:** Draft · **Date:** 2026-08-30 · **Branch:** `feat/causal-graph-integration`
**Parent plan:** `docs/plan/2026-08-29-causal-graph-github-integration.md` (Steps 1, 2b, 3; storage design is FROZEN there)
**Builds on:** Step 0 (`e9a7be1`, the oracle + differential) · Step 2a (`3b6a14f`, engine landed)

---

## Nutshell

Land the three coupled pieces together (per the approved resequencing — the normalizer swap breaks the
old fold, and the old fold is being replaced anyway): **(1)** a unified JSONL normalizer = integ's
normalizer + github's `pr-link` event + path-based `repo_id`; **(2)** graph build/persist/resolve on
two tables (`session_graph` rows, `session_tree` flat-map cache) behind a reusable `GraphService`;
**(3)** `GraphFold` — a `SessionFold` that builds the graph on arrival and computes `session_info`
from graph APIs — replacing `EconFold` at the two seams.

Load-bearing decisions:
- **The old fold is retired here, not adapted.** It can't consume integ's per-record assistant events
  without multi-counting; propping it up is throwaway. `EconFold` and `fold/` are removed at the end
  of this step (the graph is the only extractor).
- **§4.A + compaction + repo + pr_list/gitops come from the graph now.** The **§4.B heuristics**
  (segments/story/practices/cache/read-then-discard/interrupted/carry/resume_cost) are **temporarily
  not produced** — they were the "residual fold," but a residual fold would also need integ's events,
  so instead they are dropped now (tracked as *pending-port* allowlist entries) and restored in the
  parent plan's **Step 4** via graph queries. No throwaway heuristic-on-new-events code.
- **The Step 0 golden stays frozen** as "today's dashboard behavior." The A/B test compares GraphFold
  output against it, allowlist-filtered.

Honest scope note: this is the largest step. Its §1 (normalizer merge) and the §4.B ports (parent
Step 4) are each big; this sub-plan lands §1–§3 and leaves §4.B to Step 4.

---

## Terms

- **Unified normalizer** — the single `collector/normalizer/*` after the merge (integ base + pr_link + path repo_id).
- **GraphFold** — new `SessionFold`: builds/persists the graph, derives `session_info` from graph APIs.
- **pending-port drop** — a §4.B field temporarily absent from `session_info`, allowlisted until Step 4 restores it from the graph.

---

## Evidence / seams (established this session)

```
Only the fold consumes normalized events         grep: src/server/worker/fold/*, collector/engine.py (passthrough), cli_normalize.py
  → pr_class / ingest.git / ingest.otlp DO NOT    they read webhook / OTEL sources, unaffected by the swap
integ normalizer emits PER-RECORD assistant       claude_code.py:770 first_block = message_id not in seen; graph collapses, old fold SUMS → multi-count
git/PR re-layer needed onto integ = SMALL         only `pr-link`→pr_link event (_make_pr_link) + path-based repo_id; git ops ride tool results (gitops), line deltas ride hunks
SessionFold seam                                  fold_api.py:90 · EconFold at ingest/app.py:56 + cli_work.py:77 · persist_session_info persistence.py:49
build_graph(session)→dict / GraphContext          per-session; family via load_self+load_tree(resolver); scope="tree" needs the family loaded
Storage design                                    FROZEN in parent plan: session_graph rows (truth) + session_tree flat-map {sid: Graph.to_dict()} (read cache)
```

---

## Steps

### §1 — Unified normalizer (integ base + github git/PR) — ✅ DONE 2026-08-30

Landed: integ's normalizer (`claude_code/config/models/redact/local_loader/__init__` + new
`identity/tokens`), its tests (`tests/collector/normalizer/*`, integ set ⊇ github's), `corpus.py`,
`sessions.py`, `cli_normalize.py`, and `config/normalizer/claude_code.yaml` — recorded fixtures left
untouched (github's `session-queue-op-first` preserved). **Re-layered:** the `pr-link` → `pr_link`
event (`_make_pr_link` + dispatch), added to the published contract (schema/payload/descriptions
regenerated → 12 event types) with a corpus session + `test_pr_link.py`.

**Refinement vs the draft:** `repo_id` is **not** derived in the normalizer — github's normalizer
also emits `repo_id: None` on session_start (it's a fold-time/consumer derivation on *both* branches),
so that work moves to §3/GraphFold. Nothing to change in §1 for repo_id.

Results: normalizer suite green (266); graph builds end-to-end from the unified normalizer (154
per-record assistant events → 68 assistant nodes = the 68 message_ids); graph contract green;
**server suite 448 passed, api/ingest/storage/pr_class/git/otlp unaffected** — swap blast radius = the
fold only, as predicted. The old-fold `test_shape_contract` is the one expected break (repo cwd/
git_branch now nullable) — skipped, removed in §3. **§3 NOTE: repo_id derivation must tolerate null
cwd/git_branch.** All touched suites green (725 passed) after skipping the two expected-red/retired
tests. Token multi-count by the old fold is a *value* issue (not shape) → surfaces in the §3 A/B.

Original draft steps (for reference):

- Replace this branch's `collector/normalizer/*` with integ's: `claude_code.py`, `config.py`,
  `models.py`, `redact.py`, `local_loader.py`, `__init__.py`, and add integ-only `identity.py`,
  `tokens.py`. (integ already carries `cwd`/`git_branch`/`slash_command` on `session_start`.)
- **Re-layer github's git/PR (the only additions):**
  - `pr-link` record → `pr_link` event: port `_make_pr_link` + the `rtype == "pr-link"` dispatch
    (github `claude_code.py:338,659`). Add `pr_link` to `src/common/events` schema + the graph's
    `_REQUIRED_PAYLOAD_FIELDS`/contract (a graph node is *not* required — pr_list reads the event, or
    it rides `graph.meta`; decide in §3).
  - **Path-based `repo_id`** (§2a): derive `repo_id`/`repo_id_provenance="path"` from `cwd` on
    `session_start` (replace integ's `repo_id = None` TODO). No remote-scrape rung, no hook (deferred).
- **Not doing:** git-remote scraping, `lines_added/removed` pre-summing (rides `hunks`), the SessionStart hook.
- **before → after (event vocabulary):**
  ```
  before (github)  assistant_message pre-collapsed · results folded into tool_call · pr_link · (no tool_result/attachment, no message_id)
  after (unified)  assistant_message per-record (+message_id/first_block) · tool_result · attachment · pr_link · compaction · path repo_id
  ```
- **Gate (Layer 1):** the unified normalizer's event/payload keyset ⊇ **every key the graph builder
  reads** (`test_normalized_contract` green) **and** every key github emits that the new pipeline
  still needs (`pr_link.*`, `session_start.repo_id/cwd/git_branch`, `slash_command`). `pr_class` / git /
  otlp suites green (they don't read events — a fast confirmation, not a risk). **The old fold's tests
  break here — expected; the fold is removed in §3.**

### §2 — Storage + build/persist/resolve (parent Step 2b) — ✅ DONE 2026-08-30

Landed (commit after `3e8d272`): `storage/schema/graph.py` (`session_graph` rows + `session_tree`
flat-map cache) + migration `b7f2a9c14e30` + `config/queries/graph.yaml` (`session_graphs_by_id`
resolver, `session_tree_by_master`, `root_master_of` recursive CTE). `worker/graph_build.py`
(`build_and_persist`, `db_resolver`, `materialize_tree`) and `worker/graph_service.py`
(`GraphService.load_family` — session_tree fast path, row-assembly fallback). **Gate met:**
`test_tree_roundtrip` proves the flat blob reconstructs the *same* family + `session_info` as
row-assembly (in-memory, runs everywhere); `test_graph_persistence` is the live-DB e2e (gated on
TALIUM_TEST_DSN). 461 passed.

**Remaining (the §3 wiring):** make `GraphFold` a `SessionFold` (its extraction fns are done in
`graph_fold.py`), persist the graph on arrival, load the family via `GraphService` in
`rollup_subagents`, swap `EconFold`→`GraphFold` at `ingest/app.py:56` + `cli_work.py:77`, and remove
the old fold. This reshapes the fold seam (GraphFold needs DB access build/persist has not needed) and
touches the live ingest path — the risky part, best done as its own focused pass.

**Original draft:**

- Add tables + db-core named queries (`config/queries/graph.yaml`): `session_graph`
  (`upsert_session_graph`, `session_graphs_by_id`) and `session_tree`
  (`upsert_session_tree`, `session_tree_by_master`); one migration.
- `graph_build.py`: `build_graph(session)` → `upsert_session_graph`; the DB resolver over
  `session_graphs_by_id`; `materialize_tree(master_session_id)` (BFS over `Spawn.child_session_id`
  refs → flat `{sid: Graph.to_dict()}` → `upsert_session_tree`).
- `graph_service.py`: `GraphService.load_family(session_id)` — fresh `session_tree` → one-fetch +
  in-memory resolver; else row-assembly then re-materialize.
- **Gate:** build+materialize for the corpus (via the Step 0 harness, now on the unified normalizer);
  family topology == spawn tree incl. a nested grandchild; **`session_tree` one-fetch load ==
  `session_graph` row-assembly** (same `Family`); the offline runner matches CLI `build_family`.

### §3 — GraphFold + wire (parent Step 3) — ✅ WIRED 2026-08-30 (removal pending)

**GraphFold is live in the pipeline** (commits `3a361e4`, `4f33f7f`). `GraphFold` implements the
fold seam: `fold()` builds this session's graph + self `session_info` (via
`graph_fold.build_session_info`, the canonical extraction the A/B validates) and returns the graph;
`rollup_from_family()` recomputes tree-scoped fields over a family `GraphService` loaded.
`SessionFoldResult` carries the graph; the **ingest handler persists it** (`persist_graph`); the
**rollup handler materializes the tree + loads the family + recomputes**. `EconFold`→`GraphFold`
swapped at `ingest/app.py` + `cli_work.py`. `subagents_of` is now per-session (correct for nested
rollup). New GraphFold fold→rollup e2e reproduces the baseline in-memory; full non-slow suite green
(1218). The oracle harness is migrated off EconFold.

**Remaining — the old-fold removal (needs Postgres to verify):** delete `src/server/worker/fold/`
+ `EconFold`, and migrate the DB-gated pipeline tests (`test_ingest_to_fold`, `test_fold_handler`,
`test_rollup`) to GraphFold. `test_rollup` in particular is a full rewrite (the new rollup reads
`session_graph` rows, not child `session_info`), so it wants a live DB to verify rather than a blind
edit. Deferred as the final, isolated step.

**Original draft:**

**§3 EXTRACTION COMPLETE — all 17 top-level fields reproduce the baseline (commits `4632a08`→`d3e5cd1`).**
The A/B punch-list is empty: `GraphFold` produces every `session_info` field from the causal graph to
within the fidelity ledger (`docs/design/notes/2026-08-30-graphfold-fidelity-ledger.md`). §4.B
heuristics (segments/story/practices/cache/carry/interrupted/read_then_discard/resume_cost) are the
one deliberate gap — pending-port to parent Step 4, allowlisted absent. **Remaining in this sub-plan:
§2b (DB persistence + GraphService + wiring GraphFold into ingest/worker + removing the old fold).**

**Earlier progress note (14/17):**
`server/worker/graph_fold.py` computes session_info from the graph; `test_ab_graphfold.py` is the
per-field A/B vs the frozen Step-0 baseline (in-memory family, no DB yet). Done: `models`,
`tokens_total`, `tools`, `compaction_count`, `subagent_count`, `pr_list`, `files`, `turns`,
`turn_count`, `long_turn_count`, `cost_usd`, `error_count`, `repo_id`, `repo_id_provenance`.
**Remaining fields:** `subagents` (tree rollup — needs the loaded family), `skills` (rich
attribution reconstruction), `info` (nested dict: §4.A title/mcp_servers/git_commands/channel_cost/
compactions/repo/scalars + §4.B pending-port drops + §4.C errors drop). Then §2b DB + wiring.
Normalizer fix landed en route: `_backfill_session_facts` now backfills `cwd`/`git_branch` (repo_id
was collapsing to null). Fidelity ledger: `docs/design/notes/2026-08-30-graphfold-fidelity-ledger.md`.

**Earlier note (first 6 fields):**
- Allowlist grew: `bytes_read_total` — integ normalizer sets `bytes_read=None`; the graph tracks
  `num_lines`/`total_lines` instead, so byte totals are an intended difference (lines-based metric
  can replace it later — **product decision to confirm**, like the api_error/cache-TTL drops).
- `pytest.ini` set to `--import-mode=importlib` (fixes the src/tests `analysis` namespace collision;
  full non-slow suite green, 2056 passed).

**Punch-list (remaining):** `files`, `turns`/`turn_count`/`long_turn_count`, `subagents` (tree
rollup), `skills`, `repo_id`/`repo_id_provenance` (from graph.meta + cwd, §3 note: tolerate null cwd),
the `info` sub-dict (title/mcp_servers/git_commands/channel_cost = §4.A; segments/story/practices/
cache/read_then_discard/interrupted/carry/resume_cost = §4.B pending-port; errors = §4.C dropped);
`cost_usd` (otel_trueup, not GraphFold). Then §2b DB persistence + ingest/worker wiring + old-fold removal.

Original draft steps (for reference):

- `graph_fold.py` implementing `SessionFold`:
  - `fold(state, events)`: `build_graph` → `upsert_session_graph`; `load_self`; compute **§4.A**:
    `models`/`tokens_total`←`budget`, `tools`←`list_tools`, `files`←`goods`, `turns`←`turns`+
    `budget(by=turn)`, `pr_list`←`pr_link` events (or gitops), `git_commands`←`gitops`/`list_tools`,
    `mcp_servers`, `title`+`repo`/`repo_id`←`graph.meta` (lift repo_id in the builder), `compactions`
    ←`select("Compaction")`. **§4.B fields: omit (pending-port).**
  - `rollup_subagents(parent, children)`: `materialize_tree`; `GraphService.load_family`; tree-scoped
    `subagents`/`inclusive_tokens`/`delegation_ratio` from `budget(scope="tree"/"subagent")`.
- Wire `EconFold → GraphFold` at `ingest/app.py:56` and `cli_work.py:77`. `cost_usd` stays with `otel_trueup`.
- **Remove** `EconFold` + `fold/` heuristic modules and their now-broken tests (the extractor is the graph).
- **Gate (Layer 3 — the A/B):** `regressions(step0_golden, graphfold_output, allowlist) == []`, where the
  allowlist = firm (api_error §4.C, cache-TTL §4.D) + token-collapse (§4.A, refine to the exact token
  paths here) + **pending-port** (the §4.B field subtrees, temporarily). Every non-allowlisted field
  byte-identical.

---

## Order & dependencies

```
§1 normalizer   first — everything downstream needs its event shape
§2 storage/build needs §1 (build_graph consumes the unified events)
§3 GraphFold     needs §2; removes the old fold last
Within §3, wire the two seams only after the A/B on a spot session is clean.
```

---

## Files

```
NORMALIZER (§1)
src/collector/normalizer/{claude_code,config,models,redact,local_loader,__init__}.py   replace with integ + re-layer pr_link/repo_id
src/collector/normalizer/{identity,tokens}.py                                          add (integ-only)
src/common/events/**                                                                    already on branch (Step 2a); add pr_link if absent
STORAGE + BUILD (§2)
src/server/storage/schema/session_graph.py, session_tree.py                             new tables
src/server/storage/dbm/alembic/versions/*                                               migration
config/queries/graph.yaml                                                               named queries
src/server/worker/graph_build.py                                                        build+upsert+resolver+materialize_tree
src/server/worker/graph_service.py                                                      GraphService.load_family
EXTRACTOR (§3)
src/server/worker/graph_fold.py                                                         GraphFold(SessionFold)
src/server/ingest/app.py, src/server/worker/cli_work.py                                 EconFold → GraphFold
src/server/worker/fold/**, fold/driver.py (EconFold)                                    REMOVE (+ their tests)
TESTS / VALIDATION
tests/graph_migration/test_ab_graphfold.py                                              new: regressions(golden, graphfold) == [] over the corpus
tests/graph_migration/test_differential_runner.py                                       retire test_oracle_matches_frozen_baseline (old fold gone); keep the runner self-tests
DOCS
docs/plan/2026-08-29-causal-graph-github-integration.md                                 mark §1/2b/3 done
docs/modules/normalizer.md, worker docs                                                 update to unified normalizer + graph extraction
```

---

## Verification

Per step:
```
§1  test_normalized_contract green; unified keyset ⊇ github's needed keys (pr_link, repo_id, slash_command);
    pr_class/git/otlp suites green; (old fold tests red — expected, removed in §3)
§2  session_tree one-fetch load == session_graph row-assembly (same Family), incl. nested grandchild;
    offline build == CLI build_family
§3  A/B: regressions(step0_golden, graphfold_output, allowlist) == [] over the corpus; the only
    diffs are token-collapse (§4.A), firm drops (§4.C/§4.D), and pending-port §4.B subtrees
```
Whole-suite bar: `pytest -m "not slow"` green across `tests/analysis/causal_graph`, `tests/collector/normalizer`,
`tests/server/{api,ingest,storage}`, `tests/graph_migration` — minus the removed fold tests. `cost_usd`
co-write via `otel_trueup` untouched. Idempotency: refold/rebuild on resend is a no-op; late child → rollup re-trigger re-materializes.

---

## Open questions

| Question | Working assumption |
|---|---|
| §4.B temporarily dropped vs adapted residual fold on integ events? | **Drop + pending-port allowlist**, restore in Step 4 via graph queries. Adapting throwaway heuristic code to the new events is wasted effort; the dashboard shows fewer fields on this branch until Step 4. Revisit only if a §4.B field is needed live before Step 4. |
| `pr_list` from the `pr_link` *event* (residual read) or promote to a graph node / `graph.meta`? | Read the event in GraphFold.fold for now (cheapest); consider `graph.meta.pr_links` when the builder is next touched (so pr_list is graph-derived and the event read disappears). |
| Does integ's `redact.py` strip anything the new pipeline needs (e.g. from `pr-link` or tool results)? | Assume not; the §1 Layer-1 gate + a spot check on a PR/git corpus session confirms. |
| `test_oracle_matches_frozen_baseline` after the old fold is removed. | Retire it; the golden lives on as the A/B reference (`test_ab_graphfold`). The runner self-tests stay. |
| Alembic migration vs the branch's existing schema tooling. | Follow the branch's existing `storage/dbm/alembic/versions/*` pattern (git-source/otel migrations there); one additive migration, no changes to existing tables. |
```
