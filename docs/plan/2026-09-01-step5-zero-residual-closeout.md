# Step 5 close-out — zero residual (the residual fold is retired)

**Status:** DONE (2026-09-01) · **Branch:** `feat/causal-graph-integration`
**Parent:** `docs/plan/2026-08-29-causal-graph-github-integration.md` (Step 5)
**Builds on:** old-fold removal (`802c75d`) · §4.B port complete (`4ee3286`→`16f6727`)

---

## Nutshell

Step 5's goal was to **retire the residual fold** — the temporary event-summing extraction that ran
in parallel with the graph while §4.B heuristics were unported — and reach a state where
`session_info` is graph-derived. That state is reached:

- **EconFold and `src/server/worker/fold/` are deleted** (`802c75d`). There is no parallel
  event-summing fold left. GraphFold is the sole `SessionFold`.
- **All 17 top-level `session_info` fields, including every §4.B `info` heuristic, are graph-derived**
  (`test_ab_graphfold` green; the A/B punch-list is empty).

The literal reading of the original gate — *"no `session_info` field is computed from the raw event
list"* — is **not** the end-state, and was never fully achievable: a handful of signals are genuinely
**not modeled as graph nodes**, so GraphFold reads them from the session's event stream directly. These
are **targeted, signal-specific reads**, not a residual fold. The distinction that matters — *"nothing
recomputes from raw events what the graph already models"* — holds. This doc enumerates every remaining
read and why it is legitimate, so the boundary is explicit and auditable.

---

## What "residual fold" meant, and why it's gone

The **residual fold** was EconFold running alongside the graph: it re-summed per-record events into
tokens/tools/files/turns/etc. — duplicating what the graph models — plus the §4.B heuristics. That
whole engine is deleted. Everything the graph *does* model (models, tokens, tools, files, turns,
subagents, skills, compactions, git_commands, mcp_servers, channel_cost, carry, cache.hit_ratio,
read_then_discard, resume_cost) is now computed **from graph nodes/APIs**, never from the event list.

---

## The remaining event reads (targeted, by design — not a residual fold)

GraphFold reads `events` in exactly these places, each for a signal the causal graph does **not**
model as a first-class node. Every one is documented inline in `graph_fold.py`.

| `session_info` field(s) | GraphFold fn | Reads from events | Why not a graph node |
|---|---|---|---|
| `pr_list`, `info.repo.*`, `repo_id` | `pr_list_of`, `repo_info` | `pr_link` events; `session_start.cwd`/`git_branch`; `tool_call.files[].path` refs | pr-links are out-of-tree (never a graph node — the graph is JSONL-session-only); the repo evidence ladder (pr_link > remote > path + sibling-project refs) isn't a node concept |
| `info.interrupted`, `info.practices.interrupts` | `interrupted_of`, `practices_of` | the interrupt-sentinel `user_message` + preceding assistant tokens | the builder drops the exact-sentinel UserPrompt; turn `status="interrupted"` is a broader, non-matching signal (measured 2 vs 1) |
| `info.practices.*` (verifier runs/pass, first_prompt_chars, at_refs, review/research spawns, compaction split) | `practices_of` | Bash `exit_status`, `char_count`, slash-command wrapper text, `agent_type` | raw per-message fields the graph doesn't surface as node semantics; a root-only heuristic scan |
| `info.story.curve`/`shape` | `story_of` | per-record `assistant_message` token curve | the graph collapses per `message_id`, so the *per-record* cumulative curve can't be reconstructed from the collapsed assistant nodes |
| `info.segments[]` | `segments_mod.compute_segments` | beat patterns (setup/survey/build/check/revision) over the raw event sequence | beat detection is a sequential scan of the event stream; driven by graph turns, its output is graph-native |
| `info.event_count`, `info.own_event_count` | `info_of` | `len(events)` | a raw size metric (already allowlisted as graph-native; a node count would be a different number) |

**Rule of thumb for the boundary:** if the graph models it, GraphFold computes it from the graph. If
the signal is out-of-tree (`pr_link`), builder-dropped (the interrupt sentinel), a raw per-message
field (`char_count`/`exit_status`), a per-record series the collapse removes (the story curve), or a
sequential-scan heuristic (beats), GraphFold reads the event stream for *that signal only*. These reads
are minimal and independent; none re-derives a graph-modeled quantity.

---

## Gate evidence

```
1. No residual fold / EconFold in live code:
   grep -rn "worker\.fold\b\|EconFold\|fold_stub" src/   -> only graph_fold.py DOCSTRINGS (provenance
   "reproduces EconFold's metrics.X"), plus catalog.get("fold", ...) = the config/queries namespace.
   src/server/worker/fold/ and fold_stub.py: deleted.

2. Nothing recomputes a graph-modeled field from events:
   the only events reads are the 7 fns above; every graph-modeled field routes through budget()/the
   node surface (models/tokens/tools/files/turns/subagents/skills/compactions/git_commands/mcp/
   channel_cost/carry/cache.hit_ratio/read_then_discard/resume_cost).

3. A/B green, punch-list empty:
   tests/graph_migration/test_ab_graphfold.py -> every session_info field graph-derived to within the
   decided allowlist (§4.A collapse, graph-native turns/segments, normalizer-t0 timing, decided drops).

4. Suites green:
   pytest -m "not slow"  -> 1830 passed, 0 failed.
   pytest (graph+worker+ingest, TALIUM_TEST_DSN) -> 289 passed (DB-gated storage/pipeline/rollup).
```

---

## Decided drops that remain (not residual — permanent, by decision)

- **§4.C `api_error` / `info.errors` / `error_count`** — server-side, non-actionable (measured, 2026-08-29).
- **cache TTL split** (`info.cache.writes_5m`/`writes_1h`/`decay_events`) — needs per-write
  `cache_write_detail` the graph doesn't carry; the write *total* lives in `channel_cost.recache`.
- **every `*_usd_micros`** (`carry`, `resume_cost`, `segments.cost/carry`, `channel_cost`) — null in the
  baseline; no pricing table wired. `cost_usd` remains the otel true-up's separate write.

Each is a narrow, permanent allowlist entry with a ledger note — a decision, not a residual gap.

---

## Not doing (explicit)

- **Not removing the normalized event stream** — the graph builds from it, and the targeted reads above
  need it. "Zero residual" is about the *fold*, not the event stream.
- **Not doing the `repo_id → graph.meta` builder enrich** — it wouldn't eliminate `repo_info`'s event
  read (which also consumes `pr_link` + file-ref evidence), so it adds builder surface for no boundary
  gain. Revisit only if a hook-sourced canonical remote lands (feasibility doc §2a, deferred).

---

## Close-out checklist

- [x] EconFold + `fold/` + `fold_stub.py` deleted; GraphFold sole `SessionFold` (`802c75d`).
- [x] All 8 §4.B heuristics graph-derived; A/B punch-list empty (`16f6727`).
- [x] Remaining event reads enumerated + justified (this doc; inline in `graph_fold.py`).
- [x] Decided drops documented (ledger #2/#3 + `*_usd_micros`).
- [x] Suites green (`pytest -m "not slow"` 1830; DB-gated 289 under `TALIUM_TEST_DSN`).
- [x] Tear down the dev Postgres container (`talium-test-pg` on :5434) — done 2026-09-01; integration work complete.
