# Plan: port the §4.B heuristics to the graph (Step 4 → zero residual)

**Status:** DONE (all 8 fields landed 2026-08-31 / 2026-09-01) · **Branch:** `feat/causal-graph-integration`

> **Complete.** All eight §4.B heuristics are graph-derived and the A/B punch-list is empty:
> `interrupted` + `read_then_discard` byte-identical; `carry` / `cache.hit_ratio` /
> `practices.delegation_ratio` / `resume_cost.reload_tokens` the accepted §4.A collapse (graph is the
> correct per-request figure); `story` a normalizer-`t0` timing drift; `resume_cost.ttl_s` the accepted
> TTL coarsening; `segments` graph-native (computed via the vendored, behavior-preserving beat/
> classification library `server/worker/segments/`, driven by the graph turn model — boundaries/labels
> differ from the github baseline the way the turn model does, ledger #7, accepted wholesale). Only the
> decided drops remain (§4.C `api_error`; cache TTL split; every `*_usd_micros`, no pricing table).
> This is the zero-residual state — main-plan Step 5.

---

**Original plan (Draft · 2026-08-31):** · **Branch:** `feat/causal-graph-integration`

> **Landed (commit pending):** `interrupted`, `carry`, `read_then_discard`, `cache.hit_ratio`.
> `interrupted` + `read_then_discard` are **byte-identical** to the baseline (allowlist entries
> removed). `carry` + `cache.hit_ratio` are produced from assistant nodes and differ only by the
> §4.A `message_id` collapse (graph is the correct per-request figure) — narrowed to a `_VALUE`
> collapse waiver. `carry_usd_micros` stays null (no pricing); `cache.writes_5m/1h`/`decay_events`
> stay dropped (no per-write `cache_write_detail`). **Implementation note:** `interrupted` reads the
> event stream (the interrupt sentinel isn't a graph node; turn `status="interrupted"` is a broader,
> non-matching signal — 2 vs 1 on `session-turns`), the same targeted event-read as `pr_list`.
> `resume_cost` (5) landed: `count`/`gap_s`/`context_before`/`projected.context_tokens` byte-identical;
> `events[].turn` (graph-native index), `reload_tokens` (§4.A collapse), and `ttl_s` (assumed 1h — no
> `cache_write_detail` on the graph) are allowlisted. `practices` (6) landed: an event-stream scan
> (root-only, gated like turns); 14/15 fields byte-identical, only `delegation_ratio` (§4.A collapse)
> allowlisted. `story` (7) landed: event-stream per-record token curve + shape (root-only); shape +
> curve token columns byte-identical, only the curve's `t_rel` column drifts by the normalizer `t0`
> (allowlisted, cf. #10). `segments` (8) landed graph-native: a feasibility probe showed even the
> original EconFold code over integ events can't byte-reproduce the baseline (beat detectors are
> normalizer-granularity sensitive: 1→0, 6→5, 31→29 segs), so per the decision it is computed from the
> graph turn model via the vendored `server/worker/segments/` library and accepted wholesale like the
> turn model (ledger #7). **All 8 §4.B fields done.**

---

**Original plan (Draft · 2026-08-31):** · **Branch:** `feat/causal-graph-integration`
**Builds on:** §3 (GraphFold live; the §4.B `info.*` subtrees are allowlisted-absent)
**Parent:** `docs/plan/2026-08-29-causal-graph-github-integration.md` (Step 4) · fidelity ledger #8

---

## Nutshell

`GraphFold.info_of` produces the §4.A parts of `info`; the **§4.B heuristics are not produced yet** and
their absence is waived by the A/B allowlist (`info.cache*`, `info.carry*`, `info.interrupted*`,
`info.practices*`, `info.read_then_discard*`, `info.resume_cost*`, `info.segments*`, `info.story*`).
This step ports each one to compute from the causal graph, **removing its allowlist entry when it
matches the frozen baseline**. When the last lands, the A/B has no §4.B waivers and `session_info` is
fully graph-derived (zero residual, main-plan Step 5).

Each field is independent — implement in `graph_fold`, add it to `info_of`, drop its allowlist entry,
A/B must stay green. Ordered easiest-first; `segments`/`story`/`practices` are large heuristic ports
and may each warrant their own sub-plan.

Load-bearing fact (measured): **every §4.B signal is already on the graph** — compaction nodes
(trigger/is_after_compact/resumed_from), assistant cache tokens, tool outcomes/result, turn
status/plan_mode, `bytes_read` (restored). The two genuine source-gaps stay dropped: cache
`decay_events`/`writes_5m`/`writes_1h` (CC v1 gap / accepted TTL drop) and any pricing `usd_micros`
(null in the baseline — no pricing table).

---

## Field map (old source → graph signal)

| # | `info` field | EconFold source | Graph signal / approach | Notes |
|---|---|---|---|---|
| 1 | `interrupted` `{count,events,total_tokens}` | `beats.interrupted_cost_rollup` (interrupt sentinel + adjacent tokens) | UserPrompt nodes whose text == the interrupt sentinel (or turn `status=="interrupted"`); tokens from the turn's assistants | small |
| 2 | `carry` `{peak_context,mean_context,cache_read_tokens,requests,carry_usd_micros}` | `metrics.carry_rollup` (per-request cache_read high-water) | assistant nodes: `cache_read_tokens` per request → peak/mean/sum/count; `carry_usd_micros=None` (no pricing) | tokens exact; usd null |
| 3 | `read_then_discard` `{events,total_bytes_read,total_est_tokens}` | `metrics.read_then_discard_rollup` (a Read then no reuse within a window) | Tool Read nodes (`bytes_read` restored) + the config window over node ts; a later use of the path cancels | window param from config |
| 4 | `cache` `{hit_ratio,decay_events,writes_5m,writes_1h}` | `metrics.cache_rollup` | `hit_ratio` from assistant `cache_read`/(`cache_read`+`input`); **`decay_events`/`writes_5m`/`writes_1h` stay dropped** (source-gap / accepted) | partial — keep those 3 allowlisted |
| 5 | `resume_cost` `{count,events,projected,total_usd_micros}` | `metrics.resume_reload_rollup` | Compaction nodes + turn `is_after_compact`/`resumed_from_compaction_ids`; projected context from assistant `cache_read`; `usd=None` | tokens/counts exact; usd null |
| 6 | `practices` `{verifier_pass_rate,verifier_runs,auto_compactions,manual_compactions,interrupts,clear_used,plan_phase,exitplan_count,at_refs,fresh_session,first_prompt_chars,review_subagent,subagent_research,delegation_ratio}` | `practices.compute_practices` | tool outcomes (verifier pass rate), Compaction `trigger` (auto/manual), interrupts (#1), `/clear` slash command, turn `plan_mode`/ExitPlanMode tool, `@`-refs in UserPrompt, spawn agent_type (review/research), delegation_ratio from subagent tokens | medium; mostly on nodes |
| 7 | `story` `{curve,shape,anchors}` | `story.compute_story` (per-turn token curve → shape + superlatives) | per-turn tokens (`budget(by=turn)` / the turn walk) → the same curve/shape math | rewrite the math on turn data |
| 8 | `segments` `[{t0,t1,turns,label,beats,confidence,files,repo_key,cost_usd_micros,carry_usd_micros}]` | `beats.detect_beats`+`build_segments` + `session_type.classify_segments` | beats from turn boundaries + tool outcomes + Compaction (context_squeeze) + interrupt; Tier-0 label from the same scoring over graph signals; `*_usd_micros=None` | **largest** — likely its own sub-plan |

---

## Steps

One step per field, all independent, in the table order (small → large). Each:

1. Add the compute fn to `graph_fold.py` (from graph nodes/APIs), wire into `info_of`.
2. Remove its A/B allowlist entry (or narrow it — e.g. `info.cache*` → keep only `decay_events`/`writes_5m`/`writes_1h`).
3. A/B green: the field is byte-identical to the baseline (or the residual sub-fields stay explicitly allowlisted with a ledger note).

**Not doing:** the accepted source-gaps — cache `decay_events`/`writes_5m`/`writes_1h` (ledger #3, CC
v1 gap) and every `usd_micros` (no pricing table; null in the baseline). These keep narrow allowlist
entries permanently; they are not "residual", they are decided drops.

Optional follow-on (separate): if a real pricing table is wired, fill the `*_usd_micros` fields via
`common.pricing` over the graph token outputs — that is a cost-correctness pass, not a §4.B port.

---

## Order & dependencies

```
1 interrupted, 2 carry, 3 read_then_discard, 4 cache.hit_ratio   — independent, any order (small)
5 resume_cost        — reuses the compaction-node linkage
6 practices          — reuses #1 (interrupts) + compaction trigger; medium
7 story              — needs the per-turn token curve (turns already ported)
8 segments           — largest; beats + session_type classification; its own sub-plan
```

---

## Files

```
src/server/worker/graph_fold.py                    add interrupted/carry/read_then_discard/cache/
                                                   resume_cost/practices/story/segments compute fns; info_of
tests/graph_migration/differential.py              remove each §4.B allowlist entry as it lands
tests/graph_migration/test_ab_graphfold.py         (no change — it already compares the whole info dict)
docs/design/notes/2026-08-30-graphfold-fidelity-ledger.md   close ledger #8 rows as they land
docs/plan/2026-08-29-causal-graph-github-integration.md     Step 4 done -> Step 5 (retire residual)
```

---

## Verification

Per field: the A/B (`test_ab_graphfold`) stays green with that field's allowlist entry **removed** —
i.e. `info.<field>` is byte-identical to the frozen baseline across the corpus. Add a fixture that
actually exercises the field where the sanitized corpus is thin (a compaction-heavy session for
`resume_cost`/`carry`; a session with an ExitPlanMode + `/clear` for `practices`; a multi-phase
session for `segments`).

Whole-step bar: after all rows, `grep` the allowlist — the only remaining §4.B entries are the
decided drops (cache `decay_events`/`writes_5m`/`writes_1h`, `*_usd_micros`). That is the zero-residual
state (main-plan Step 5): every other field graph-derived and byte-identical.

---

## Open questions

| Question | Working assumption |
|---|---|
| Does the corpus exercise `segments`/`story`/`practices` richly enough to validate them? | No — the 3 sanitized fixtures are thin. Add targeted fixtures (compaction-heavy, plan-mode, multi-phase) before porting 6–8. |
| `segments` beat detection depends on EconFold thresholds (`config.beats`/`verifiers`). Reuse the config, or re-tune on graph signals? | Reuse the existing `config/worker.yaml` thresholds verbatim so the port is behavior-preserving; re-tuning is a separate product decision. |
| `read_then_discard` window + `est_tokens` heuristic — reproduce EconFold's exactly? | Yes — copy the window and the bytes→tokens estimate from `metrics.read_then_discard_rollup`; the graph now carries `bytes_read`, so it is a faithful port. |
| Is porting `segments`/`story`/`practices` worth it vs accepting them dropped? | Port — they are real dashboard features (ledger #8 marked "YES"). Revisit per-field only if a port proves disproportionate. |
```
