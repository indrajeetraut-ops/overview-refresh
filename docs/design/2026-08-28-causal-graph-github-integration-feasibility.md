# Feasibility: integrating the causal graph (`integ`) into `feat/github-acquisition-poc`

**Date:** 2026-08-28 · **Author:** Lead Architect · **Status:** Feasibility study → **IMPLEMENTED (2026-09-01)**
**Implementation plan:** `docs/plan/2026-08-29-causal-graph-github-integration.md` (operationalizes §§4–7 below)

> **Update 2026-09-01 — integration complete.** All six steps landed on `feat/causal-graph-integration`:
> unified normalizer, graph engine + `session_graph`/`session_tree` storage, GraphFold wired live,
> EconFold removed, all 8 §4.B heuristics graph-derived (A/B punch-list empty), and the residual fold
> retired. Every `session_info` field is now graph-derived. Close-out:
> `docs/plan/2026-09-01-step5-zero-residual-closeout.md`.

> **Update 2026-08-29 — compaction landed; `api_error` is being dropped from scope.**
> (1) The graph builder now promotes `compaction` events to a first-class **`compaction` node**
> (`ccdff4e`, `9c086b1`, schema 4.3.0), queryable via `select("Compaction")` — so
> `compaction_count`, `info.compactions[]` are graph-derivable and `carry`/`resume_cost` are
> unblocked.
> (2) **`api_error` (`info.errors`, `retried_tokens_est`) is dropped as a metric** — decision
> below, grounded in a real-corpus measurement (§4.C). It is **not** a parity gap to close: neither
> the graph nor the residual fold needs to produce it. This removes the last §5 residual.
> (3) A separate `memory_usage` named API + memory-file classification also landed (a *bonus* over
> fold, not a parity item). Changes are folded into §4.C, §5, §7, §8 below, marked *(upd 2026-08-29)*.

Goal restated: bring the `causal_graph` engine from `integ` onto `feat/github-acquisition-poc`,
and move that branch's JSONL→`session_info` extraction (the `fold` worker) from reading the raw
event stream to calling the causal-graph APIs — **without regressing any feature currently on the
github branch.**

---

## 0. TL;DR verdict

**Feasible, but it is a two-layer integration, not a one-layer swap — and the harder layer is the
normalizer, not the extractor.**

1. The two branches diverged at `44dfa0c` and evolved **two different normalizers**. The causal
   graph is tightly coupled to `integ`'s normalizer (schema `3.0.0`, per-record assistant events,
   a real `tool_result` event, `attachment` events). The github branch's normalizer is a
   *different* shape (pre-collapsed assistant events, results folded into `tool_call`, git/PR
   enrichment, a `pr_link` event, **no** `src/common/events/` schema). **Neither normalizer is a
   superset of the other.** The unified normalizer must be **integ's as the base** (the graph
   builder requires its assistant/`tool_result` granularity), with github's JSONL git/PR fields
   layered on and its fold re-pointed at it. **Normalizer reconciliation is the prerequisite and
   the main risk.**

2. The causal-graph named APIs cleanly cover **~60%** of the github `session_info` contract
   (models/tokens, tools, files, turns, subagents, pr_list, git commands, title, mcp) — and cover
   some of it *more correctly* than fold does today (token double-counting; see §4).

3. The remaining **~40%** is a heuristic engine that must be **rebuilt on top of** the graph's
   `select()`/`query()` node surface (segments/beats, story, practices, cache, read-then-discard).
   The former out-of-band blocker is resolved: `compaction` is now a node *(upd 2026-08-29 — schema
   4.3.0)*, and **`api_error` is dropped from scope** (§4.C) rather than modeled — so there is no
   remaining lossy-graph gap. The §5 decision is settled.

4. The causal graph is **not wired into any pipeline on `integ`** — it is a standalone library +
   CLI. `integ` still produces `session_info` with an *older, leaner* fold. So there is no
   ready-made "graph → session_info" path to copy; it has to be built here.

---

## 1. What actually diverged (the real shape of the problem)

Merge base: `44dfa0c`. Both branches are children of it; neither contains the other's work.

| Layer | `integ` | `feat/github-acquisition-poc` |
|---|---|---|
| **Normalizer** | Rewritten. Schema contract `src/common/events/normalized_schema.json` **v3.0.0**; adds `identity.py`, `tokens.py`. Emits **per-record** `assistant_message` (with `message_id` + `first_block_of_message`), a separate **`tool_result`** event, **`attachment`** events. Rich attrs: `estimated_visible_tokens`, `reasoning_tokens`, `content_kind`, `attribution_skill/plugin/agent`, `mcp_server/mcp_tool`, `tool_denial_kind`, `hunks`, `artifact_class`, `ai_title`. | Different rewrite. **No** `src/common/events/` schema. Emits **pre-collapsed** `assistant_message` (one per message, tokens set once), folds results **into** `tool_call`, emits **`pr_link`** (integ has none). Git/PR enrichment: `repo_id`, `git_branch`, `sha`, `pr_number/url`, `lines_added/removed`, `slash_command`. |
| **Analysis** | `src/analysis/causal_graph/` — full engine (builder + GraphContext + query engine + named APIs + CLI). **Not imported by `src/server` or `src/collector`.** | None. |
| **Extraction (`fold`)** | Lean: `driver`, `metrics`, `turns`, `extract_claude_code`. | Rich: adds `beats`, `practices`, `session_type`, `story`; plus `pr_class/` worker. `metrics.py` alone is 827 lines. |
| **Product features** | None beyond the above. | Dashboard API + static UI, OTel intake (`ingest/otlp`), GitHub App acquisition (`ingest/git`), PR work-classification, deploy stack, OTel + git storage schema & migrations. |

The `EventIn` carrier type (`event_type` / `payload` / `event_ts` / `record_id` / `parent_id`) is
**identical** on both branches. The divergence is entirely in the **event vocabulary and payload
schema** the two normalizers emit — which is exactly what the graph builder is contractually
coupled to (its `_REQUIRED_PAYLOAD_FIELDS` guard).

---

## 2. The foundational blocker — normalizer reconciliation

The reconciliation question is **which normalizer becomes the single unified base** — because after
integration one JSONL normalizer feeds both the graph and the residual fold. The answer is **integ's**,
and the reason is a hard requirement, not a preference: the graph builder (`builder.py`) consumes the
normalizer's event stream directly and asserts a contract (`test_normalized_contract.py`) against
`normalized_schema.json` v3.0.0. Two structural properties of that contract are things **only integ's
normalizer produces** — so the unified normalizer must keep integ's shape here, and github's leaner
shape could not stand in as the base:

- **Assistant granularity.** The builder needs *per content-record* assistant events with
  `message_id` + `first_block_of_message`, which it collapses by `message_id` (usage from the
  `first_block_of_message=true` record — summing double-counts ~2×) and uses to estimate reasoning
  (summing `estimated_visible_tokens`/`tool_call_count` across the group). integ emits exactly this.
  github's normalizer emits *one pre-collapsed* assistant event per message with **no**
  `message_id`/`first_block_of_message` — a shape the builder's `_collapse_assistants` and reasoning
  estimation can't work from. So the unified normalizer must retain integ's per-record granularity.
- **Tool results.** The builder builds a `tool` node by pairing a `tool_call` with a **`tool_result`
  event** (`tool_result.payload.tool_use_id == tool_call.record_id`) and extracts `attrs.result`
  (exit_status, is_error, hunks, **git ops**) from it. integ emits the separate `tool_result` event;
  github's normalizer folds outcomes into `tool_call` and emits none. The unified normalizer must
  keep integ's separate `tool_result` event, or graph tool nodes carry empty `result` and
  `tool_usage(outcome=…)`, `goods` line-totals, and `gitops` all break.

(Neither point is about feeding github's output to the builder — nothing does that; the graph is
always built by the one integ-based normalizer. They are the reasons **integ's normalizer is the
base** and github's fold is re-pointed at it, not the reverse.)

**Invariant: the causal graph is built only from session (JSONL) files, always.** The GitHub
*webhook* stream (`ingest/git` → `pr_class`) is a **separate ingest path** that correlates to
sessions downstream; it must **never** be fed into the graph builder, and this plan does not do so.
Any earlier wording implying "roll github's event stream into the graph" was wrong — struck.

What *is* in scope is narrower: after integration, **one JSONL normalizer (integ-based) feeds both
the graph and the residual fold.** So it must carry every JSONL-*derived* field the fold path still
needs — and integ's normalizer drops two of them: the **`pr-link` record** (→ `pr_link` event) and a
**derived `repo_id`** (integ leaves it `None`, an explicit TODO). Everything else github's fold
reads from the JSONL, integ already carries (see §2a). So `integ`'s normalizer is the base (schema
contract + the finer granularity the graph needs); the only git/PR additions to layer on are the
`pr-link` event and repo-identity derivation — **not** the webhook stream, which is untouched.

The merge is still real work (two hand-rewritten `claude_code.py` files, reconciled with integ as
base), but the git/PR delta to carry is small and JSONL-local, not a webhook integration.

> There is a prior reconciliation record, `docs/design/2026-07-14-branch-to-v2-reconciliation-
> handoff.md`, but it concerns an older v1→v2 *design-doc* merge (api_error/compaction/cost-detail
> signals), not this code-level integ↔github merge. Useful as signal-provenance background only.

### 2a. Fidelity lost when the JSONL normalizer drops git/PR fields *(upd 2026-08-29)*

What integ's normalizer already carries from the session file (so the graph keeps it):

```
cwd, git_branch (gitBranch)   on session_start           80/80 real sessions carry both
in-session git operations     tool-result gitOperation    21/80 sessions, 65 commits → graph gitops
file line deltas              structuredPatch → hunks     net_delta derivable via goods._line_totals
slash_command                 parsed from user text       carried
```

What it drops or does not derive:

| Signal | integ behavior | Real prevalence (80 sessions) | Fidelity lost |
|---|---|---|---|
| `pr-link` record → `prNumber`/`prUrl`/**`prRepository`** | falls through to `unknown` passthrough → builder drops it (not a node) | **0 sessions** carry a pr-link record | The graph has no "PRs opened this session" list, and loses `prRepository` (fully-qualified `owner/name`, the highest-provenance repo fingerprint). **Zero impact in this corpus; matters only for workflows that use CC's PR-open feature.** |
| derived `repo_id` / provenance | `repo_id = None` (TODO) — ingredients (cwd, git_branch) *are* carried, the derivation is not | repo_id null in **80/80** | The graph can place work on a *branch name + local path* but not a canonical cross-machine repository. fold's `repo`/`repo_id`/`repos[]`/`repo_key` and multi-project attribution have no graph equivalent. **Universal, but a derivation gap, not a raw-signal loss.** |
| per-record `cwd`/`git_branch` (vs session_start only) | only on session_start | mid-session repo switches: rare | A session that changes repo mid-way is attributed to its first. Minor. |

**Conclusion: the graph loses repository/PR *identity*, not repository *activity*.** It records what
git work happened (commits, edits with line deltas, branch/dir context) at high fidelity; it cannot
canonically *name* the repo or PR that work belongs to, because the two identity signals — a derived
`repo_id` and pr-link's `prRepository` — aren't carried. The raw ingredients for `repo_id`
(`cwd`/`git_branch`) survive, so closing the universal gap is a **repo_id derivation step** (in the
normalizer, or as post-graph enrichment), bounded by what CC actually exposes — CC JSONL carries no
git remote here, which is exactly why integ's TODO stalled; github's own `repo_id` ladder degrades
to `cwd`/`git_branch` heuristics whenever `prRepository` is absent (i.e. in 80/80 of these sessions).
The pr-link gap is real but 0-prevalence in this corpus — carry the `pr-link` event in the unified
normalizer (cheap, additive) so PR-heavy workflows don't silently lose it, but it is not blocking.

**Decision (2026-08-29) — near-term repo/PR fidelity:**

- **`repo_id`: path-based derivation now.** Derive `repo_id` from `cwd` (the `path` rung of the
  `pr_link > remote > path` ladder) in the unified normalizer. Accept the known limitation — a local
  path is **not a cross-machine-stable fingerprint** — as the working baseline. This replaces integ's
  `repo_id = None` TODO with the always-available signal; no remote scraping added on integ for now.
- **`pr-link`: extract it.** Carry the `pr-link` record forward as a `pr_link` event (integ drops it
  today → `unknown`). When present it yields `prRepository`, which is a **better** identity than the
  path and, in the ladder, outranks it — so extracting pr-links both restores the PRs-opened list and
  upgrades `repo_id` for those sessions at no extra cost. 0-prevalence in this corpus, but additive
  and cheap.
- **Hook-based plugin: deferred.** A `SessionStart` hook emitting a definitive canonical remote
  (§ "repo_id via hook", packaged as the git-integration plan's Part C plugin) is the eventual
  upgrade to a stable cross-machine `repo_id`. Not now. The path baseline is designed to be the
  ladder's bottom rung so the hook slots in **above** it later without reworking consumers.

Net near-term ladder on integ: **`pr_link` (when present) > `path`** — deliberately omitting the
opportunistic `remote`-scrape rung and the hook until the plugin lands.

---

## 3. Where the causal graph is (and isn't) wired

`grep` confirms nothing under `src/server` or `src/collector` on `integ` imports `causal_graph`,
`build_graph`, or `GraphContext`. The engine is exercised only by its CLI (`cli_graph.py`) and
tests. So:

- There is **no existing "build graph → derive session_info" worker** to lift over. It must be
  written on the github branch: a per-session `build_graph(normalized_session)` step, persistence
  of the graph JSON, `GraphContext.load_self` + `load_tree(resolver)` for the subagent family, then
  the extraction calls.
- The graph is **per-session and storage-neutral**: `build_graph()` takes one `sessions[]` member
  and returns a dict; the family (master + subagents) is assembled at read time via a resolver you
  supply from the DB. This fits github's existing `SessionState`/`signal_blob` model well — the
  graph JSON can live beside (or instead of) the deduped event blob.

---

## 4. Extraction reconciliation — `fold` output → causal-graph API map

Every top-level field of `tests/sample-data/session-info-shape-contract.json`, and its coverage:

### A. Clean map to named APIs (structural, high fidelity — often *more* correct than fold)

| `session_info` field | Causal-graph source | Notes / risk |
|---|---|---|
| `models`, `tokens_total`, `info.tokens_total` | `budget(by="model")`, `budget` total | **Intentional numeric change.** fold's `token_totals` sums usage over every `assistant_message`; the graph collapses by `message_id` and takes usage from `first_block_of_message`. Where the *unified* normalizer emits per-record assistant events, the graph number is the correct one and fold's would double-count. This must be an **allowlisted diff**, not a regression, in validation. |
| `tools` | `list_tools` | Direct. |
| `files` | `goods` | `goods` dedupes by path and adds line-totals; richer than fold's `file_totals`. |
| `pr_list` | `gitops(operation="pr")` | Requires `tool_result`/git result extraction → depends on unified normalizer (§2). |
| `info.git_commands` | `gitops` / `list_tools` (command class) | Same dependency. |
| `turns`, `turn_count`, `long_turn_count` | `turns` API + `budget(by="turn")` | Composition needed: long-turn threshold, `files_changed`, `subagent_tokens`, `subagent_session_ids` are fold overlays on the per-turn primitive. |
| `subagents`, `subagent_count`, `info.inclusive_tokens` | `subagents` API + `load_tree` + `budget(by="subagent")` | Inclusive-token rollup and per-child `tools`/`files` need the loaded family + per-child `list_tools`/`goods`. |
| `info.mcp_servers` | query on `Tool` nodes (`tool_name STARTS_WITH 'mcp__'`, `mcp_server` attr); `turns` already emits `mcp_invocations` | `bytes_read_total` needs a result attr. |
| `info.title` | `graph.meta.title` (built from `ai_title`) | Direct. |
| `info.channel_cost` | `budget` usage × pricing | Pricing is fold's own layer (`common.pricing`), not the graph — unchanged. |
| `skills` | `select(Assistant)` on `attribution_skill` | **Partial.** fold's `skills[]` carry `started/ended/tokens/success/allowed_tools/args_gist` — richer than the attribution flag. Needs a custom query + composition. |

### B. Rebuild on the `select()`/`query()` surface (feasible, real work)

`info.segments[]` (beats + Tier-0 `session_type`), `info.story`, `info.practices`,
`info.read_then_discard`, `info.cache` (partially), `info.interrupted`. The graph exposes good
primitives (turn nodes with `duration_ms`/`permission_mode`/`plan_mode`/`status`, tool nodes with
`outcome`/`result`/`files`, spawn nodes), so these heuristics can be re-expressed as queries — but
this is net-new code equal in spirit to the 1000+ lines of `beats.py`/`session_type.py`/`story.py`
that already exist. It is not a wrapper; it is a port.

### C. Graph-model coverage of the out-of-band / identity signals *(upd 2026-08-29)*

The builder's turn loop creates turn-contained nodes only for `tool_call` and `sub_agent_spawn`.
Two out-of-band signals used to be dropped entirely; **compaction is now promoted to a node**
(schema 4.3.0), leaving `api_error` as the sole remaining loss.

| `session_info` field | Source signal | Status |
|---|---|---|
| `compaction_count`, `info.compactions[]` | `compaction` node | **Covered (upd 2026-08-29).** `select("Compaction", scope="tree", verbosity="full")` → `trigger`, `tokens_before`/`after`/`reclaimed`, `duration_ms`, `ts` — a 1:1 field match to fold's rollup. `usage_known:False` on the node is *correct parity*: fold doesn't price the summarization call either (issue #40). |
| `info.resume_cost` | compaction + resume boundary | **Mostly unblocked (upd 2026-08-29).** The node's turn linkage now exists: `turn.attrs.is_after_compact`, `refs.resumed_from_compaction_ids`, and `triggering_turn_id`/`resumed_turn_id` on the node. Priced context reloads become a query over those boundaries + assistant `cache_read_tokens`. |
| `info.carry` (peak/mean context, `carry_usd_micros`) | per-request context high-water | **Mostly unblocked.** Context high-water is an assistant-node walk (`cache_read_tokens` as the context proxy); compaction nodes now mark where it resets. Pricing stays fold's own layer. |
| `info.practices.auto_compactions` / `manual_compactions` | compaction `trigger` | **Covered** — `trigger` distinguishes `auto` vs `manual` on the node. |
| `info.errors` (`by_class`, `retry_count`, `retried_tokens_est`) | `api_error` events | **Dropped (upd 2026-08-29)** — not a graph node, and no longer a parity target. See the decision below. |
| `info.repo` / `repo_id` / `repo_id_provenance` | git enrichment fields (normalizer, not graph) | Orthogonal — keep deriving outside the graph (see §4-git in the follow-up). |

**Dropping `api_error` — decision (upd 2026-08-29).** Grounded in a measurement over the local
corpus (`~/.claude/projects`, 80 sessions):

- **Rarity + concentration:** 6/80 sessions (7.5%) had any api_error; 24 of 27 records sit in 3
  sessions. Total wall-clock lost to backoff across all 80 sessions: **~126 s**. 18/27 resolved on
  the first retry.
- **No lever.** Every record is server-side (HTTP 429/529/overloaded, plus a couple of
  `ECONNRESET`/sleep drops). Unlike compaction — which the developer *can* influence via session
  hygiene — retries are not user-actionable. A per-session error count is a metric with no decision
  attached to it.
- **`retried_tokens_est` is phantom cost.** Failed 429/529 requests are generally not billed, so
  `retry_count × context tokens` estimates spend that mostly never occurred. Drop it, don't port it.
- **The current metric captures nulls anyway.** The normalizer's `_make_api_error` reads
  `record.status`/`retryCount`/`retryAfterMs`/`message`; **real records carry none of those** —
  the fields are `error.status` (nested), `retryAttempt`, `maxRetries`, `retryInMs`. On real data
  every derived field is null (`error_class="other"`, rest `None`). *(Filed as a normalizer bug
  regardless of this decision.)*

**Verdict:** do not model `api_error` in the graph and do not carry `info.errors`/`retried_tokens_est`
into the graph-backed extractor. The only defensible future framing is **aggregate fleet reliability
/ backoff-time** (`retryInMs` + error class, org+time granularity) for tier/routing decisions — a
separate ops surface, not a per-session `session_info` field. The `api_error`-node implementation
plan (`docs/plan/2026-08-29-api-error-node-implementation.md`) is **shelved** on this basis.

**Bonus, not a parity item (upd 2026-08-29):** the graph also gained a `memory_usage` named API and
memory-file classification (Tool `memory_access`/`is_memory`/`memory_kind`; `goods` memory support).
Fold has no equivalent — this is new analytical surface the integration inherits for free, worth
wiring into the dashboard later but out of scope for `session_info` parity.

### D. The residual fold, itemized — and why it goes to ~0 *(upd 2026-08-29)*

The "residual fold" is a **temporary migration shim, not a permanent parallel extractor.** The goal
is a **zero-residual** end state: everything in `session_info` computed from the graph, nothing
reading the raw event stream. That is reachable because **the graph is effectively lossless over the
event stream** — every normalized event type becomes a node or is folded onto one:

```
user_message / assistant_message / tool_call+tool_result / sub_agent_spawn   → nodes
compaction                                                                   → node (schema 4.3.0)
file_change                                                                  → tool node attrs.files
attachment                                                                   → user_prompt.resources
session_start (ai_title; repo_id once lifted)                                → graph.meta
unknown/turn_duration                                                        → turn duration
api_error                                                                    → dropped by decision (§4.C)
```

So every §4.B field is a **PORT** (rewrite the heuristic to read graph nodes), not a raw-stream
dependency. Classify each residual field by *why* it isn't graph-derived yet:

| Field(s) | Reason it's residual today | Graph source once closed |
|---|---|---|
| `segments`/`beats`, `session_type`, `story`, `practices`, `interrupted`, `carry`, `resume_cost` | **PORT** — heuristic code still reads raw events; every signal it needs is already on nodes (turn `status`/`duration`/`plan_mode`, tool `outcome`/`result`, compaction node + `is_after_compact`, assistant tokens, spawn nodes) | `select`/`query` over those nodes; `budget(by="turn")` for per-turn tokens. No new data. |
| `read_then_discard` | **PORT** — needs read size, which is on the tool result facet (`result.file` `num_lines`/`total_lines`) | query Tool nodes + a time window |
| `mcp_servers` | **PORT** — `tool_name STARTS_WITH 'mcp__'` already drives `turns`' `mcp_invocations` | query Tool nodes; `bytes_read_total` from the read facet |
| `cache` write/read totals, `channel_cost.recache` | **PORT** — the assistant node already carries `cache_read_tokens` + `cache_write_tokens` totals | query assistant nodes; recache priced from the cache-write total |
| `cache.writes_5m` / `writes_1h` (TTL split) | **DROPPED (accepted, 2026-08-29)** — the TTL identity (5m vs 1h) is not high-fidelity; totals are what matter. Normalizer emits `cache_write_detail` but the builder need not carry it. | none — intentional loss; the cache write/read totals suffice |
| `repo_id` / `repo` | **ENRICH** — derived on `session_start` (path-based, §2a) but not lifted | lift into `graph.meta` (mirrors `title`) |
| `channel_cost`, `carry_usd_micros`, `resume_cost.usd`, `cost_usd` split | **PRICING** — a computation layer over token outputs, never a raw-event dependency | `budget`/token outputs × the price table (fold's own `common.pricing`, unchanged) |
| `cache_miss` / cache `decay_events`, `retried_tokens_est`, `info.errors` | **SOURCE-GAP / DROPPED** — not a graph-vs-fold gap: CC JSONL doesn't expose cache-miss (`cache_miss_tokens = None`, a v1 gap fold hits too); errors dropped (§4.C) | none — parity is "both omit it" |
| `info.event_count` / `own_event_count` | **META** — a raw-event count; nodes ≠ events (assistants collapse, tool pairs merge) | drop, or carry an event count in `graph.meta` |

**The path to ~0 residual is therefore short and enumerable:**

1. **Port** the §4.B heuristics to graph-backed code (the bulk of the effort — but a rewrite of
   *existing* logic against a richer surface, not new capability).
2. **Enrich** the builder — now down to **one required item**: lift `repo_id` into `graph.meta`
   (path-based, §2a; mirrors `title`), optionally an event count into `graph.meta`. The cache-TTL
   split is dropped (above), so `cache_write_detail` is no longer needed; `server_tool_use` is a
   *non-parity* cost-correctness enhancement (no current `session_info` field uses it) and can be
   added later if a server-tool cost channel is ever wanted.
3. After 1–2, **the residual fold is empty** — the only fields it can't reproduce are the
   SOURCE-GAP/DROPPED rows, which fold can't produce either, so removing it loses nothing.

Net: the graph-vs-residual gap is **not** a set of things the graph fundamentally can't do. It is (a)
heuristics not yet ported and (b) exactly **one** node-attribute addition (`repo_id → meta`). Both are
on the closable side of the line, so the doc's target end state is **zero residual**, with the
residual fold existing only across the Phase-3→Phase-4 window.

---

## 5. The decision this forces

*(upd 2026-08-29 — this decision is now settled.)* With `compaction` modeled and `api_error`
**dropped from scope** (§4.C), the graph has **no remaining lossy gap** against the fields we intend
to keep. What's left is a build-order choice, not a source-of-truth gap:

- **Option B — Hybrid extractor (chosen).** Move the **structural** extraction (§4.A) *and*
  compaction to the graph immediately; keep a **thin residual fold** only for the §4.B heuristics
  (segments/beats, story, practices, cache, read-then-discard) until each is ported. Both consume
  the *same unified normalized events* — no second parse. Once §4.B is fully ported, the residual
  fold disappears and the graph is the single source of truth. **No `api_error` promotion is needed
  to reach that end state**, because `info.errors` is no longer a target.
- **Option C — graph read-only** (land the engine as a new analysis surface, incl. `memory_usage`,
  without touching `session_info`) remains the lowest-risk fallback if the cutover is deferred.

The earlier "Option A — promote the out-of-band signals" is **complete for compaction and moot for
api_error** (dropped), so it no longer appears as pending work.

---

## 6. Recommended integration sequence

Each phase ends green; nothing merges to the github branch until its phase's validation passes.

- **Phase 0 — Freeze the contract & build the harness (do this first).** On the github branch,
  snapshot today's `session_info` for a corpus of real sessions (single + multi-subagent + resume +
  compaction-heavy + PR/git + OTel-true-up). This golden set is the regression oracle for
  everything after. Wire the differential runner (§7).
- **Phase 1 — Unified normalizer.** Merge integ's `claude_code.py`/`config.py`/`identity.py`/
  `tokens.py` + `src/common/events/` onto github, re-layering github's git/PR enrichment and
  `pr_link`. **Gate:** github's existing normalizer tests + `pr_class`/git/otel tests still pass;
  the graph's `test_normalized_contract.py` passes; a spot corpus normalizes without schema errors.
  *This is the phase most likely to surface hidden regressions in `pr_class`, git mapping, and OTel
  true-up — budget accordingly.*
- **Phase 2 — Land the graph engine + a build/persist path.** Copy `src/analysis/causal_graph/` and
  its contracts/tests. Add a per-session `build_graph` step and graph persistence; assemble families
  with a DB-backed resolver. **Gate:** graph builds for every corpus session; family load matches
  the known subagent topology; graph unit tests pass.
- **Phase 3 — Structural cutover (Option B).** Reimplement §4.A fields via named APIs behind the
  existing `SessionFold`/`fold_api.py` seam. Keep the residual fold for §4.B/§4.C. **Gate:** the
  differential runner shows only allowlisted diffs (§4.A token-collapse corrections).
- **Phase 4 — Port the heuristics (§4.B/§4.D) field-by-field**, each behind its own green diff.
  Includes the one **builder-enrich** step (§4.D): lift `repo_id` into `graph.meta` (cache-TTL split
  dropped, so no assistant-node attr additions are required), so the ports read everything from nodes.
- **Phase 5 — retire the residual fold (target: zero residual)** once §4.B/§4.D are ported. *(No api_error promotion:
  `info.errors` is dropped, §4.C; compaction is already a graph node.)*

---

## 7. Validation strategy (the emphasis)

The guarantee you want — *"features on the github branch keep working after integration"* — is
enforceable because `session_info` is a concrete, dumpable contract and the branch already ships
two shape contracts (`session-info-shape-contract.json`, `normalizer-shape-contract.json`). Validate
in four layers, from the raw signal outward, so a failure localizes to the layer that caused it.

**Layer 1 — Normalizer differential (Phase 1 gate).** For the corpus, diff the *unified* normalizer's
event stream against the *current github* normalizer's. Every event and payload key github emits
today must still be emitted (git/PR fields included). New keys from integ are additive and allowed.
Assert against `normalizer-shape-contract.json`. This is the single most important gate — if the
normalizer is wrong, everything downstream is wrong in ways that look like extractor bugs.

**Layer 2 — Graph invariants (Phase 2 gate).** Per session: node counts by type are sane; every
`tool` node with a github-side outcome has a non-empty `attrs.result`; turn count/ordinals match
the normalizer's promptId runs; the loaded family topology equals the known spawn tree; no
extraction warnings on clean corpus sessions.

**Layer 3 — `session_info` differential (the core gate, Phases 3–4).** Run **both** the legacy fold
and the graph-backed extractor over the same corpus and deep-diff the resulting `session_info`.
Maintain an **explicit allowlist** of intended differences with a written rationale each.
Entries so far: (1) the token-collapse correction (§4.A); (2) **`info.errors` and
`retried_tokens_est` are dropped** *(upd 2026-08-29, §4.C)* — omitted by design (`retried_tokens_est`
is phantom cost); (3) **`cache.writes_5m`/`writes_1h` are dropped** *(upd 2026-08-29, §4.D)* — the
cache-TTL split is intentionally not carried; cache write/read totals remain byte-identical. **Any
field not on the allowlist must be byte-identical.** Run per field so a diff names the exact path (e.g. `info.segments[2].label`). This
is a differential/characterization test, not an assertion of "correct numbers" — its job is to prove
the migration is behavior-preserving except where we *chose* to change it.

**Layer 4 — Feature/consumer contract tests (every phase).** The github branch's own suites are the
backstop that proves *product* features still work end to end:
`tests/server/api/*` (dashboard/service — the actual `session_info` consumers),
`tests/server/ingest/git/*` and `tests/server/storage/test_git_*` (acquisition + PR class),
`tests/server/ingest/test_otlp_*` (OTel true-up, which co-writes `cost_usd`). These must stay green
untouched. Add a couple of API-level golden responses for a session that exercises segments/story so
a UI-visible regression can't slip through a field the unit diff didn't weight.

**Corpus requirements.** Cover: a plain single session; a multi-subagent family (inclusive-token
rollup, `subagents.*`); a resumed session (`resume_cost`); a compaction-heavy session
(`compactions`, `carry`); an `api_error` session (`errors`); a PR/git session (`pr_list`,
`git_commands`, `pr_class`); and a session that also has an OTel true-up (guards the `cost_usd`
co-write seam). Store inputs + golden `session_info` as fixtures (CLAUDE.md: examples/goldens must be
generated by running, never hand-written), and make the differential runner a CI test.

**Idempotency & seams to re-assert.** fold is idempotent by positional dedup and recomputes from the
full blob every pass; the graph is rebuilt per session. Re-assert: refold/rebuild on a resend is a
no-op; `rollup_subagents` still fills `subagents`/`inclusive_tokens`/`delegation_ratio`; and
`otel_trueup`'s separate `cost_usd` write is untouched by the extractor swap (fold never owns
`cost_usd` — keep it that way).

---

## 8. Top risks & open questions

1. **Normalizer merge is the tail risk.** Two hand-rewritten 1000-line `claude_code.py` files must
   become one. Mitigation: integ as base, github git/PR as additive layer, Layer-1 differential as
   the gate. *Open:* does integ's redaction (`redact.py`) drop anything `pr_class` needs?
2. **Out-of-band signals** → resolved. *(upd 2026-08-29: compaction is a graph node, unblocking
   `compactions`/`carry`/`resume_cost`; `api_error`/`info.errors` is dropped, §4.C — no residual
   for it.)* Watch the compaction contract: the node ships `usage_known:False` by design (issue
   #40) — do **not** attempt to price the summarization call from it; parity with fold, which also
   doesn't, depends on this.
3. **Token semantics change** (`budget` collapse vs fold's sum) is a *deliberate* diff, not a bug —
   but it changes user-visible cost/token numbers, so it needs product sign-off and an allowlist
   entry, and any dashboard snapshot tests will move.
4. **Heuristic port surface is large** (`beats`/`session_type`/`story`/`practices` ≈ 1150 lines).
   Option B lets these migrate incrementally rather than in one risky cutover.
5. **`repo`/`repo_id` provenance** — per the 2026-08-29 decision (§2a): path-based `repo_id` +
   `pr_link` extraction in the unified normalizer now; hook-based definitive remote later. The
   derived `repo_id` lands on the `session_start` event, so for the **graph** to carry it the builder
   must lift it into `graph.meta` (a small addition, mirroring how `title` is already lifted);
   otherwise it stays a residual-fold field read off the event stream. Either is fine — decide with
   the Phase-3 cutover, don't force repo modeling into the node graph.
6. **No graph→session_info precedent on integ** — the build/persist/resolve worker is net-new; budget
   for it (Phase 2) rather than assuming it's a copy.

---

## 9. Bottom line

- **Yes, feasible**, via Option B and the phased sequence above.
- **The normalizer, not the extractor, is the critical path.** Reconcile it first, gate it hardest.
- **The extractor swap is safe** because `session_info` is a dumpable contract: a per-field
  differential over a representative corpus, plus the github branch's existing API/ingest/storage
  suites, gives a mechanical proof that features still work — with a small, explicitly justified
  allowlist for the fidelity improvements the graph brings.
