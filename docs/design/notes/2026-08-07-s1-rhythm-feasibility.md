# Feasibility — S1 rhythm tiles + activity heatmap

Concept: `docs/design/mockups/dashboard-s1-rhythm-concept.html`.
Scope of this note: can the five rhythm tiles (active days, current streak, longest streak,
peak hour, favorite model) and the 52-week heatmap be built on the API and datasource design
as they stand today, and at what cost.

**Verdict: feasible, with one element degraded in one mode and two blocking prerequisites.**
Nothing here needs a new datasource, a refold, or a schema migration. The two prerequisites
(timezone, subagent filtering) are both small, and one of them is a bug that already affects
a shipped tile.

---

## 1. Element-by-element

| Element | Verdict | Cost | Rests on |
| :---- | :---- | :---- | :---- |
| Active days | Feasible, all modes | 1 new query | `sessions.started_at` |
| Current / longest streak | Feasible, all modes | same query, Python | `sessions.started_at` |
| Heatmap (52w) | Feasible, all modes — **see F7** | same query | `sessions.started_at` |
| Favorite model | Feasible, all modes | **zero new queries** | `_model_split()`, already computed |
| Peak hour | Feasible in `jsonl_only`/`both`; **degraded in `otel_only`** | **zero new queries** | `session_info.turns[].started` |

The two "zero new queries" rows are the useful surprise: if peak hour and favorite model are
scoped to the **selected range** rather than to all time, both fall out of rows `overview()`
has *already fetched* (`_fetch_sessions_in_range`). Only the three calendar facts — which are
lifetime-scoped by definition — need anything new. See §5.

---

## 2. F1 — `started_at` is real session time, not ingest time (holds)

The entire zone rests on one assumption: that a session's `started_at` is when the work
happened, not when the upload landed. If it were ingest time, a backfill would stack six
months of history onto one heatmap cell and every streak would be fiction.

**It holds, and in both modes, deliberately:**

- `config/queries/ingest.yaml:20` — `ensure_session` stamps `started_at` to `now()` at row
  creation. That is a *placeholder*.
- [driver.py:114](src/server/worker/fold/driver.py:114) — every fold recomputes
  `session.started_at = min(event_timestamps)` over the full deduped blob, never seeded from
  the previous value. The comment there names the backfill case as the exact bug it exists to
  prevent.
- [otel_trueup.py:638](src/server/worker/otel_trueup.py:638) — the `otel_only` stub row sets
  `started_at = earliest_event_ts`, "never `now()`", for its own (different) reason.

So both writers converge on real event time. **Verified, not assumed.**

One residual: between `ensure_session` and the first fold, a row carries `now()`. A session
that has ingested but not yet folded lands on today's cell and corrects itself on the next
fold. Not worth engineering around — but it does mean the heatmap's most recent cell is
mildly unstable, which argues against animating it.

## 3. F2 — Peak hour is degraded in `otel_only` (the one real gap)

The per-request ledger that the true-up writes into `session_info.otel.requests[]`
([otel_trueup.py:235-249](src/server/worker/otel_trueup.py:235)) carries `request_id`,
`sources`, `model`, four token fields, `cost_usd_micros`, `query_source`, `cost_est_usd_micros`
and `cost_basis` — **and no timestamp**. `otel.event_ts` is read one line later (`:246`) to
price the request against the right pricing epoch, then discarded.

So in `otel_only` there is no sub-session timing in `session_info` at all. Three ways out:

| | Approach | Cost | Downside |
| :---- | :---- | :---- | :---- |
| a | Add `event_ts` to the ledger record | 1 line + a refold of every session | Grows every `session_info` row by one timestamp per request |
| b | Query `otel_api_requests.event_ts` directly | New query + **a new index** | Table is indexed on `(vendor, session_id)` and `received_at` only ([otel_api_requests.py:51](src/server/storage/schema/otel_api_requests.py:51)); a per-user 365-day histogram is a full scan without one |
| c | Fall back to session `started_at` | Free | One data point per session instead of per request — coarse |

**Recommendation: (c) for v1, structured as a precedence chain**, exactly like
`ACTIVE_TIME_CHAIN` in [precedence.py](src/server/api/precedence.py) — which already solves
this identical shape of problem (exact OTel counter → looser JSONL fallback, with the winning
source reported so the UI can caption it). The chain is `turn_started → session_started`:

- `jsonl_only` / `both` → `turns[].started`, one point per turn. Good signal.
- `otel_only` → session start only. A 4-hour session starting at 9 PM contributes only to 9 PM.

That is honest and captionable ("from session start times") rather than wrong, it reuses a
pattern the codebase already has, and (a) or (b) can be added as a higher rung later without
touching the UI. Note this is *not* the `unavailable` state — the tile still renders a real
number; it is a precedence fallback, and MODES §5's rules do not apply.

## 4. F3 — Subagent sessions inflate every session count (pre-existing bug)

Subagent sessions get their own full `sessions` row: `fold_handler.py:57` passes
`parent_session_id` through `ensure_session`, and the same envelope carries the same
`user_id`. `sessions_in_range` (`config/queries/dashboard.yaml`) filters on `user_id` and the
time window only — **there is no `parent_session_id IS NULL` predicate**.

Consequences:

- The heatmap's *sessions* metric would count a fan-out day as N+1 sessions.
- **Today's shipped Z1 "sessions" tile already has this bug** — `_z1_headline` sets
  `"sessions": len(rows)` ([service.py:94](src/server/api/service.py:94)) over the unfiltered
  row set. A session that spawned 12 sub-agents reads as 13.
- Active days and streaks are *unaffected* — a subagent runs on the same calendar day as its
  parent, so the distinct-day set is identical either way.
- `subagents_spawned` is unaffected (it sums the parents' `subagents` maps, not row counts).

**Corrected 2026-08-07 — the fix is NOT a query predicate.** Adding
`AND s.parent_session_id IS NULL` to `sessions_in_range` would be a regression, not a fix:
`session_info.tokens_total` and `models` are **own-session only**
([driver.py:175](src/server/worker/fold/driver.py:175) — `inclusive_tokens` is a separate
`info` key computed by `rollup_subagents`, and the promoted column never carries it). Today's
totals are correct precisely *because* every child row is summed alongside its parent;
filtering children out would silently drop their tokens and cost from every Z1/Z2 figure.

The correct fix is narrower: keep every row, and count *sessions* over roots only —
`sum(1 for r in rows if r.parent_session_id is None)`. That requires adding
`s.parent_session_id` to the `sessions_in_range` SELECT list, where it is **not currently
selected**. Same rule for the heatmap: sum tokens over all rows, count sessions over roots.

`_files_touched` and `_distinct_repos` are unaffected either way (both own-only per row, so
summing across all rows is already right). `_top_repos` counts sessions per repo and inherits
the same inflation as Z1 — same one-line treatment.

**The near-miss that makes this confusing** (reviewed 2026-08-07): the fold *does* roll
sub-agent tokens up, into `info["inclusive_tokens"]`
([driver.py:177](src/server/worker/fold/driver.py:177), asserted in `test_driver.py:192`). So
"children are already folded into the parent" is half true — but that key is not what the
dashboard reads, and **no equivalent exists for cost**: the `subagents` map carries tokens,
duration, purpose, agent_type, origin, depth and model, and `cost_effective_usd_micros` is
owned by the true-up worker rather than `rollup_subagents`. Tools, files, skills, MCP and
active time are likewise own-only and never merged upward. Filtering child rows out would
therefore drop real dollars and real tool calls with no key to recover them from.

Decision: **keep every row, count roots only.** Recorded in the plan's R0 with the full
reasoning, because this is easy to re-derive wrongly.

> Not measurable locally: the only database present is `econ_test` with 2 rows (1 of them a
> subagent) and no `user_id` set, so the real subagent share of session counts is unknown.
> Structurally the inflation is certain; its magnitude is not.

## 5. F4 — Timezone is unmodeled end to end (blocking)

Every fact in this zone is a **calendar** fact, and nothing in the system carries a user
timezone: no column on `sessions` or `session_info`, no key in `config/api.yaml`, no query
param on any endpoint. All timestamps are UTC.

Untreated, an IST user (+5:30) gets wrong active days, wrong streak boundaries, wrong heatmap
cells and a wrong peak hour. The half-hour offset also rules out the cheap fix of returning
UTC buckets and rotating them client-side.

There is no user table to hang a preference on — `user_id` is a plain indexed column with no
FK ([sessions.py:12](src/server/storage/schema/sessions.py:12)) — so storing it would mean
inventing one. **Recommendation: a `tz` query param**, sent from
`Intl.DateTimeFormat().resolvedOptions().timeZone`, bucketed with `AT TIME ZONE` in SQL.
Exact, stateless, DST-correct, no migration. Validate the IANA name and reject unknowns as a
400 — same discipline as `_SORT_QUERIES` rejecting an unknown `sort`
([service.py:292](src/server/api/service.py:292)) rather than silently defaulting.

## 6. F5 — Scope conflict: this zone is not range-scoped

Every existing dashboard endpoint takes `from`/`to`. Streaks and a 52-week heatmap are
lifetime-scoped by definition, and a 7-day window structurally cannot report a 16-day streak.
Two consequences:

**Backend.** Putting them in `/overview` yields one response carrying two different time
scales, and its `aggregate_mode_block` would describe the range while the heatmap describes a
year. Use a separate `GET /v1/users/{id}/rhythm?tz=&weeks=52` with its own mode block over its
own window.

**Frontend.** [app.mjs:88](src/server/api/static/js/app.mjs:88) re-dispatches the router on
every state change, so switching 7d→30d re-runs `renderOverview` and refetches everything. A
heatmap mounted inside the overview screen would refetch 52 weeks of data on every timeframe
toggle. It needs either a cache in `store.mjs` or a fetch not invalidated by timeframe.

**This is what motivates the split in §1.** Scope the two *habit* facts (peak hour, favorite
model) to the selected range and the three *calendar* facts to the year:

- `/overview` gains `z0.peak_hour` + `z0.favorite_model` — both from rows already fetched;
  `_model_split()` is already computed for Z2 and just needs calling once instead of twice.
- `/rhythm` serves active days + streaks + heatmap — one new query, timeframe-independent,
  cacheable.

Scoping peak hour to the range is also the better product answer: "your peak hour" as a
current habit beats a lifetime average that can never move.

## 7. F6 — Performance

- `sessions.user_id` is indexed; `started_at` is not. The daily rollup
  (`WHERE user_id = ? AND started_at >= now() - interval '365 days' GROUP BY day`) is an index
  scan on `user_id` then a filter. Fine at v1 single-user scale; add a composite
  `(user_id, started_at)` before fleet scale.
- The `GROUP BY` output is ≤365 rows. Trivial.
- **Do not** compute the hour histogram over 365 days from `turns` — that JSONB blob is per
  session and pulling a year of them into Python to bucket would be the one expensive thing
  here. Scoping peak hour to the selected range (§6) avoids it entirely; the rows are already
  in memory and `service.py` already buckets in Python everywhere else (`_top_tools` et al.).

## 7b. F7 — There is no mode-independent token column (added 2026-08-07)

Found while planning R1. The heatmap needs tokens-per-day in SQL, and **there is no column that
carries tokens in every mode.** `session_info.tokens_total` is written only by the JSONL fold
([persistence.py:74](src/server/worker/persistence.py:74)); the true-up deliberately never
writes it, so it is `0` on every `otel_only` row. A naive `sum(i.tokens_total)` would render an
empty heatmap for an OTel-only deployment — killing the feature's best argument (§11).

Cost does not have this problem: PM promoted `cost_effective_usd_micros` to a real column for
exactly this reason. Tokens never got the same treatment.

Three options:

| | Approach | Cost |
| :---- | :---- | :---- |
| a | Resolve tokens in Python via `precedence.resolve_aggregate` | Correct, but pulls a year of `otel` ledger blobs into memory to sum four numbers — the one genuinely expensive thing in this feature |
| b | Two-rung token expression in the named query (JSONB read, not sort) | Cheap, auditable; duplicates the top of the precedence chain in SQL |
| c | Promote `tokens_effective` to a column, true-up-written | Structurally right, mirrors `cost_effective_usd_micros`; needs a migration + backfill |

**Recommendation: (b) now, (c) later.** The `otel` block already carries pre-summed
`otel.tokens.{tokens_input,tokens_output,tokens_cache_read,tokens_cache_creation}` at the top
level, so the query reads four scalars, not the ledger. Values are plain decimal strings
(`_fmt` = `format(value.normalize(), "f")` — no exponent, [otel_trueup.py:148](src/server/worker/otel_trueup.py:148)),
so they cast via `::numeric`, not `::bigint`. Availability is keyed on `otel->'measures'` being
non-empty, **not** on `otel->'tokens'` existing — that key is always present with zero defaults,
which is the trap `precedence._otel_measures_tokens` documents.

(b) omits the `otel_ledger` rung, which matters only for a ledger-only fleet with no metrics
pipeline. Document that omission in the query rather than letting it be discovered.

## 8. Visual system

No new color tokens. The heatmap is a sequential/magnitude encoding, so it draws from
`--seq250 → --seq600`, which BLUE §7 reserves for exactly this ("models and other ranked,
non-categorical lists") and forbids drawing from `--s1…--s6` (pinned globally to provenance
origins). The existing ramp already inverts per theme — light→dark on the light surface,
dark→light on the dark one — so it works in both without retuning.

Level steps must be **quantiles of non-zero days, not linear**: tokens/day is heavy-tailed
enough that a linear split paints most of the year level-1.

Z3's four-cards→tabs change is nearly free: `.s4tabs`/`.tabpane` in `components.css` is
already generic. `MetricsTabs` itself is not reusable (its panes are S4-specific), so this
means extracting a small generic tab shell, not writing one.

## 9. Effort

| | Work | Estimate |
| :---- | :---- | :---- |
| Prereq | `parent_session_id IS NULL` predicate (§4), separate change | 0.5d incl. tests |
| Backend | 1 named query, `/rhythm` endpoint, streak/hour bucketing, tz validation, `z0` on overview | 1–1.5d |
| Frontend | `Heatmap` component, tile restyle, generic tab shell, store caching | 1d |
| Docs | BLUE §2 zone order + §8 inventory (`Heatmap`, `RhythmTiles`), MODES capability matrix | 0.5d |

**~3–3.5 days**, no migration, no refold.

## 10. Open decisions

1. **Streaks.** The lexicon bans grades because "scores are verdicts wearing numbers."
   A streak is not a verdict, but `Current streak: 0` greeting someone returning from a break
   sits close to one. Keep both / longest-only / active-days alone.
2. **Heatmap default metric** — tokens or sessions. (If sessions, §4 becomes blocking rather
   than merely advisable.)
3. **Peak-hour fallback caption** in `otel_only` — needs wording that names the source, not
   the user, per MODES §5 rule 1.

## 11. Doc changes this implies

BLUE §2 (S1 zone order and Z1's tile list), BLUE §8 (component inventory gains `Heatmap` and
`RhythmTiles`, both "all modes"), and the MODES capability matrix. Worth landing with the
change, not after — the current blueprint is what the next implementation session builds from.

Note for the matrix: this zone renders in **every** mode including `otel_only`, which today
collapses to Z1+Z2. That is an argument for the feature independent of aesthetics.
