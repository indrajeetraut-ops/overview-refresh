# Plan: remove the old EconFold fold (Postgres-verified)

**Status:** DONE (2026-08-31) · **Branch:** `feat/causal-graph-integration`

> **Outcome:** EconFold removed. Steps 1-4 complete and Postgres-verified. `src/server/worker/fold/`,
> `fold_stub.py`, and `tests/server/worker/fold/` deleted; the `SessionFold` seam now declares
> `rollup_from_family(parent, family)` (GraphFold's shape). The three DB-gated tests migrated
> (`test_fold_handler`, `test_ingest_to_fold`, `test_rollup` — the last a full rewrite seeding
> `session_graph` rows). Dead `_load_pricing` helpers + imports dropped from `ingest/app.py` and
> `cli_work.py`; stale `EconFold`/`rollup_subagents` comments repointed. `config/worker.yaml`'s `fold:`
> section **kept** (heuristic thresholds the §4.B port reuses). Full suite green: `pytest -m "not slow"`
> = 2143 passed (after fixing one *pre-existing, unrelated* stale API test — placeholder `"P3"` →
> `<div id="app">`); the 4 DB-gated tests green under `TALIUM_TEST_DSN`. `cost_usd` true-up untouched.

---

**Original plan (Draft · 2026-08-31):** · **Branch:** `feat/causal-graph-integration`
**Builds on:** §3 wiring (`3a361e4`, GraphFold live) · `4f33f7f` (oracle harness off EconFold)
**Parent:** `docs/plan/2026-08-30-combined-normalizer-graph-fold.md` (§3 tail)

---

## Nutshell

Delete the now-dead EconFold fold and migrate the DB-gated pipeline tests to GraphFold. GraphFold is
already the live `SessionFold` (ingest + rollup handlers); `src/server/worker/fold/` and
`fold_stub.py` are unreferenced by the live path — only the harness (already migrated) and three
DB-gated tests still touch them. **This step needs a real Postgres (`TALIUM_TEST_DSN`)**: two of the
three tests are DB-gated and `test_rollup` is a *full rewrite* against the new graph-based rollup, so
it must be run, not edited blind. Nothing here changes runtime behavior — it removes dead code and
re-points its tests.

Load-bearing decision: **`test_rollup` is rewritten to seed `session_graph` rows and exercise the real
rollup handler**, not adapted from its old `SessionInfo`-seeding shape (the new rollup reads graphs,
not child `session_info`).

---

## Terms

- **fold module** — `src/server/worker/fold/` (`driver.py`=EconFold, `metrics/turns/beats/practices/session_type/story/extract_claude_code/config`).
- **the seam** — `fold_api.py` (`SessionFold` Protocol, `SessionState`, `SessionInfo`, `SessionFoldResult`, `SessionInfoDelta`). **Kept** — GraphFold uses it.

---

## Evidence

```
Live path uses GraphFold        ingest/app.py + cli_work.py construct GraphFold(); rollup.py imports graph_fold
Still reference fold/ or EconFold (grep, minus graph_fold/harness which are done):
  src/server/api/config.py      a COMMENT only ("not worker.fold.config.WasteConfig") — no import
  src/server/worker/fold_stub.py  legacy StubFold (pre-EconFold); check refs, likely also removable
  src/server/worker/fold_api.py   the SEAM — SessionFold.rollup_subagents(parent, children) signature
                                  is EconFold's; GraphFold uses rollup_from_family(parent, family)
  tests/server/test_ingest_to_fold.py       EconFold() in the e2e (DB-gated, @slow)
  tests/server/ingest/test_fold_handler.py  EconFold() in _fold_one tests (DB-gated)
  tests/server/worker/test_rollup.py        EconFold() + seeds child SessionInfo rows (DB-gated, @requires_db)
  tests/server/worker/fold/**               the fold's own unit tests (deleted with it)
```

---

## Steps

### Step 1 — Reconcile the `SessionFold` seam

- `fold_api.py`: the `SessionFold` Protocol's `rollup_subagents(parent, children)` was EconFold's;
  GraphFold implements `fold()` + `rollup_from_family(parent, family)`. Update the Protocol to match
  GraphFold (rename/drop `rollup_subagents`; the handlers already call `rollup_from_family`). Keep
  `SessionState`/`SessionInfo`/`SessionFoldResult` (with `graph`)/`SessionInfoDelta`.
- **Not doing:** removing `fold_api.py` — it is the live seam.
- **Verify:** `ingest/app.py`, `cli_work.py`, `rollup.py`, `fold_handler.py` typecheck/import; a grep
  shows no live import of `rollup_subagents`.

### Step 2 — Migrate `test_ingest_to_fold` + `test_fold_handler` to GraphFold (DB)

- Replace `EconFold()` with `GraphFold()`. `create_app` already defaults to GraphFold, so the ingest
  path needs no fold arg; the explicit `make_rollup_handler(EconFold())` → `make_rollup_handler(GraphFold())`.
- Assertions: the e2e's `inclusive == own + Σ child tokens` relationship holds under GraphFold;
  `test_fold_handler`'s exact token assertions **will move** (token-collapse) — update expected values
  to the graph numbers, or assert the relationship, not the literal.
- **Verify (needs DB):** both green under `TALIUM_TEST_DSN`.

### Step 3 — Rewrite `test_rollup` for the graph-based rollup (DB)

The new rollup reads `session_graph` rows, not child `SessionInfo`. Rewrite:
```
seed sessions rows (P, C1, C2) + persist their graphs (build_and_persist / GraphFold.fold on real events)
run make_rollup_handler(GraphFold())(s, catalog, {"session_id": "P"})
assert session_info["P"].subagents == {C1, C2 ...}; info.inclusive_tokens == own + Σ child inclusive
assert a childless session rings nothing; a child rings its parent (ring_rollup propagation)
```
Use a small real fixture (or the `session-subagents` recorded fixture) so the graphs are real.
- **Verify (needs DB):** green under `TALIUM_TEST_DSN`.

### Step 4 — Delete the fold module + its tests

- `git rm -r src/server/worker/fold/` and `tests/server/worker/fold/`.
- Remove `src/server/worker/fold_stub.py` iff Step-0 grep confirms nothing live imports it (else leave
  + note). Remove any now-dead helpers (`_load_pricing` in `ingest/app.py`/`cli_work.py` if unused
  after the swap; the `config/worker.yaml` `fold:` section if nothing else reads it — check `api/config.py`).
- Fix the `api/config.py` comment referencing `worker.fold.config.WasteConfig`.
- **Verify:** full suite (below).

---

## Order & dependencies

```
Step 1  first (seam) — Steps 2-4 build on it
Step 2, 3  need a DB; independent of each other
Step 4  last — delete only after 2+3 prove the live path is covered without the fold
```

---

## Files

```
src/server/worker/fold_api.py                 SessionFold protocol -> GraphFold shape
src/server/worker/fold/**                     DELETE
src/server/worker/fold_stub.py                DELETE (if unreferenced)
src/server/ingest/app.py, cli_work.py         drop now-unused _load_pricing/fold config if dead
src/server/api/config.py                      fix stale comment
config/worker.yaml                            drop `fold:` section if unread
tests/server/worker/fold/**                   DELETE
tests/server/test_ingest_to_fold.py           EconFold -> GraphFold
tests/server/ingest/test_fold_handler.py      EconFold -> GraphFold (+ token expectations)
tests/server/worker/test_rollup.py            REWRITE for graph-based rollup
docs/plan/2026-08-30-combined-normalizer-graph-fold.md   mark §3 complete
```

---

## Verification

```
1  no live import of rollup_subagents; ingest/worker import clean
2  test_ingest_to_fold + test_fold_handler green under TALIUM_TEST_DSN
3  test_rollup (rewritten) green under TALIUM_TEST_DSN: subagents map + inclusive from graph rows
4  `grep -rn "worker.fold\b\|EconFold\|fold_stub"` returns only fold_api (seam) + this plan
```
Whole-suite bar: `pytest -m "not slow"` green (fold tests gone); and **with Postgres**,
`pytest -m slow` green (the three migrated DB tests + `test_graph_persistence`). `cost_usd` co-write
via `otel_trueup` untouched.

---

## Open questions

| Question | Working assumption |
|---|---|
| Does `config/worker.yaml`'s `fold:` section feed anything besides EconFold (e.g. `api/config.py`)? | Check at Step 4; if `api` reads `fold.waste.*`, keep those keys, drop the rest. |
| `fold_stub.py` still referenced? | Grep at Step 4; delete if not, else leave with a TODO. |
| Exact `test_fold_handler` token expectations under the collapse. | Recompute from a GraphFold run against the fixture and freeze; don't hand-edit. |
```
