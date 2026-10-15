# GraphFold fidelity ledger

Every place the graph-based `session_info` (GraphFold) intentionally differs from the frozen
Step-0 baseline (github normalizer + EconFold = "today's dashboard behavior"). Policy (2026-08-30):
**accept the graph's signal, allowlist the diff, log it here**; revisit the ones worth enriching at
the end. Each row = an allowlist entry in `tests/graph_migration/differential.py`.

`kind`: **improvement** (graph is more correct) · **coarser** (graph is a different/blunter signal) ·
**dropped** (not produced) · **semantic** (different definition).

| # | Field(s) | Difference | kind | Enrich worth it? |
|---|---|---|---|---|
| 1 | `models.*`, `tokens_total`, `info.tokens_total`, `info.inclusive_tokens` | graph collapses assistant records by `message_id` and takes usage from the first-block record; EconFold summed every per-record event (≈2× over-count) | improvement | no — the graph number is correct |
| 2 | `info.errors`, `error_count`, `retried_tokens_est` | dropped — api_error not modeled (decided non-actionable/server-side; `retried_tokens_est` is phantom cost) | dropped | no |
| 3 | `info.cache.writes_5m`, `writes_1h` | TTL split not tracked (cache read/write totals kept) | dropped | no (user: not high-fidelity) |
| 4 | ~~`tools.*.bytes_read_total`, `info.mcp_servers.by_server.*.bytes_read_total`~~ | **CLOSED 2026-08-30 (restored).** `bytes_read` was already on the tool_call payload; carried onto the tool node (builder `_merge_tool`, with `mcp_server`/`mcp_tool`). Now **exact** — allowlist removed. | ~~coarser~~ → resolved | done |
| 5 | ~~`files.*.lines_added`, `files.*.lines_removed`~~ | **CLOSED 2026-08-30 (enriched).** Ported `_lines_added_removed` into the normalizer (counts `+`/`-` from structuredPatch before the body is dropped); builder carries them onto file entries (`_FILE_FIELDS`). Now **exact** — matches the baseline, allowlist removed. | ~~dropped~~ → resolved | done |
| 6 | `files.*.created` | integ distinguishes a Write that created a file from one that overwrote an existing file; github marked every Write created | improvement | no |
| 7 | `turn_count`, turn boundaries | graph segments by `promptId` runs; EconFold by text-based human-message detection (differs by ~1 turn on one fixture) | semantic | tbd — decide which turn definition the dashboard wants |

| 8f | ~~`info.segments`~~ | **PORTED 2026-09-01 (graph-native).** Computed from the graph turn model (`turns_of`) + event stream via a vendored, behavior-preserving beat/classification library (`server/worker/segments/`, from the retired fold's `beats.py`+`session_type.py`). Driven by graph turns, the boundaries/counts/labels are **graph-native** and differ from the github-normalizer baseline (the beat detectors are normalizer-granularity sensitive: measured 1→0, 6→5, 31→29 segments) — accepted wholesale like the turn model itself (#7). segments is now PRESENT; its exact shape is graph-native, not golden. cost/carry usd null (no pricing). | graph-native | done |
| 8e | ~~`info.story`~~ | **PORTED 2026-08-31.** Event-stream per-record token curve + shape archetype (root-session only). shape + curve token columns byte-identical; only the curve's `t_rel` column drifts uniformly by the normalizer's first-event `t0` (event-timing, cf. #10). `anchors.superlatives` empty (EconFold pinned no rule). | improvement/timing | done |
| 8d | ~~`info.practices`~~ | **PORTED 2026-08-31.** Event-stream scan (root-session only, gated like turns), same as EconFold's `compute_practices`. 14/15 fields byte-identical; `delegation_ratio` (= child_tokens/own_tokens, patched at rollup) is the §4.A collapse. `plan_phase`/`clear_used` stay null (EconFold F6 deferral, preserved). | improvement | done |
| 8c | ~~`info.resume_cost`~~ | **PORTED 2026-08-31.** From assistant nodes (per-request context) + turn gaps. `count`/`gap_s`/`context_before`/`projected.context_tokens` byte-identical; `events[].turn` = graph-native turn index (ledger #7), `events[].reload_tokens` = §4.A collapse, `ttl_s` assumed 1h (graph has no `cache_write_detail` — accepted TTL-coarsening, cf. #3). `usd_micros` null (no pricing). | improvement/coarsening | done |
| 8a | ~~`info.interrupted`, `info.read_then_discard`~~ | **CLOSED 2026-08-31 (ported).** `interrupted` reads the interrupt sentinel from the event stream (graph doesn't model it as a node); `read_then_discard` from Compaction nodes + tool `bytes_read` within the 600s window. Both **byte-identical** to the baseline — no allowlist. | ~~dropped~~ → resolved | done |
| 8b | `info.carry.*`, `info.cache.hit_ratio` | **PORTED 2026-08-31.** Produced from assistant nodes; values differ from the per-record baseline only by the §4.A `message_id` collapse (same as #1 — the graph is the correct per-request figure). `carry.peak_context` matches exactly; `carry_usd_micros`/`cache` TTL split stay null/dropped (no pricing / no `cache_write_detail`). | improvement | no — the graph number is correct |
| 9 | `info.event_count`, `info.own_event_count` | integ normalizer emits per-record events (assistant split, separate tool_result/attachment), so the raw event count differs from github's | coarser | no — a size metric; use node count if wanted |
| 10 | `duration_ms` (`turns[]`, `subagents.*`, `info`) | graph measures the node-ts span; EconFold measured the event-ts span (nodes exclude some events), so it's a few ms–seconds shorter | coarser | no — negligible |

**Status 2026-09-01: ALL 17 top-level `session_info` fields — including every §4.B `info` heuristic —
are now graph-derived** (`tests/graph_migration/test_ab_graphfold.py` green; the A/B punch-list is
empty). §4.B port (rows 8a–8f) complete: interrupted/read_then_discard byte-identical; carry/cache.
hit_ratio/practices.delegation_ratio/resume_cost the §4.A collapse (graph is correct); story a
normalizer-`t0` timing drift; segments graph-native (accepted like the turn model). The only decided
drops remaining are §4.C `api_error` (#2) and the cache TTL split / `*_usd_micros` (no pricing table).
This is the zero-residual state (main-plan Step 5): every field graph-derived, nothing recomputed from
a residual event fold.

More rows appended as the punch-list is worked.
