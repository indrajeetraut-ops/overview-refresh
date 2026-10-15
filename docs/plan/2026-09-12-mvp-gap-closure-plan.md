# MVP gap-closure plan — post-launch backlog

**Context.** The MVP plan (`2026-09-09-mvp-backend-implementation.md`) shipped complete per
`2026-09-10-mvp-implementation-progress.md`: all phases, U7/U8 front-end wiring, 349+ backend
tests green. This doc is the next layer down — every `TODO(...)` marker left in source, plus a
set of gaps found by diffing the live app against the frozen mockup on `feat/mvp-ux`
(`docs/design/mockups/figma/src/screens/*.tsx`). Nothing here blocks what shipped; this is the
backlog for what comes next.

**Correction after landing QW1-QW3 (2026-09-12):** `src/webui/AGENTS.md`'s own U5/U6 change log
(not read before this doc's first draft) already documents G1/G2/G3 below as *deliberate*
Phase-1 simplifications with stated reasons, not undocumented oversights — only QW1's
per-model list turned out to be a genuine wiring gap dressed up as a strip-rule call. G1-G4's
entries below are corrected accordingly; treat "silent" in the original framing as "not
tracked as a TODO," not "nobody knew."

**How to use this doc.** Items are ordered to attack one at a time, cheapest/highest-leverage
first. Each has: what's wrong, why (root cause, not symptom), the files involved, a suggested
approach, and a rough size (S = a sitting, M = a day or two, L = needs its own mini-plan).
Check an item off (`- [x]`) and remove its `TODO(...)` marker from source per CLAUDE.md rule 4
when it lands. Items in the same track share a root cause — landing the first in a track often
makes the rest cheaper.

---

## Quick wins — attack these first

### QW1 — Surface `session_info.models` on session/PR detail (closes silent gap)
**What's wrong:** The mockup's "Models used" token-share bars (per-model % of tokens, with a
tier color) appear on both `SessionScreen` and `PRScreen`. Neither renders it live — not even
as an `UnavailableNote`. It looks like unbuilt work; it isn't.

**Root cause:** The data already exists. `worker/graph_fold.py::models_of`/`_models_for` folds
every assistant node's usage into `{model: {input, output, cache_read, cache_write}}` and
writes it to `session_info.models` on every session, unconditionally. But
[`single_user/service.py::session_detail`](src/server/api/single_user/service.py:895) never
reads `row.models` into its response dict (`service.py:912-932`) — the column is populated,
the API just never promotes it. This is a wiring gap, not a data gap.

**Files:** `src/server/api/single_user/service.py` (`session_detail`, `_session_economics_block`
for the PR-aggregate case), `src/server/api/schemas/*.py` (add the field to the response
schema), `src/webui/src/screens/SessionScreen.tsx`, `src/webui/src/screens/PRScreen.tsx`
(copy the mockup's token-share-bar block verbatim, it's already lexicon-clean), `src/webui/src/data/api-types.ts` (regen from the updated OpenAPI schema).

**Approach:** Add a `models` block to `session_detail`'s result (per-model token totals +
computed % share); for `PRScreen`, aggregate across the PR's linked sessions the same way
`_session_economics_block` already aggregates cost. No new fold, no new worker pass — pure
API + front-end.

**Size:** S.

- [x] Land QW1 — `aggregate.model_split`/`model_usage_breakdown` (moved from
  `single_user/service.py`, now shared), wired into `session_detail`'s `economics.models` and
  `pr_detail`'s `models`; `ModelUsageBars` (`ui.tsx`) renders it on both screens. Backend
  (349) + frontend (typecheck/lexicon/vitest/build) all green; verified live in the browser.

---

### QW2 — Restore "avg time to PR" on Overview + TeamScreen (closes silent gap)
**What's wrong:** Overview's and TeamScreen's `StatStrip` dropped "avg time to PR" (and
Overview also dropped the Opus/Sonnet/Haiku model-mix stat) with no substitute.

**Root cause:** `aggregate.avg_time_to_merge_days(pr_rows)` already exists and is already
wired into two endpoints — `/v1/teams` (`service.py:271`) and `/v1/projects`
(`service.py:319`) — but never into `org_overview`'s `highlights` block or `team_detail`'s
`highlights` block, which is what `Overview.tsx`/`TeamScreen.tsx` actually read from.

**Files:** `src/server/api/manager/service.py` (`org_overview`, `team_detail` — thread
`aggregate.avg_time_to_merge_days` through, same call already used elsewhere in this file),
`src/server/api/schemas/manager.py` (add the field to `OrgOverview.highlights`/
`TeamDetail.highlights`), `src/webui/src/screens/Overview.tsx`, `TeamScreen.tsx` (`StatStrip`).

**Size:** S.

- [x] Land QW2 — threaded `aggregate.avg_time_to_merge_days(pr_rows)` into both highlights
  blocks; verified live ("2.6d avg time to PR" on Overview, "2.4d" on Platform's TeamScreen).

---

### QW3 — Org/team-wide model-mix stat (stretch on QW1)
**What's wrong:** Overview's mockup StatStrip shows a model-mix percentage
("Opus 18% / Sonnet 61% / Haiku 21%") org-wide. No live equivalent.

**Root cause:** Same underlying field as QW1 (`session_info.models`), just aggregated across
every session in scope instead of one. No aggregate for "share of tokens by model across N
sessions" exists yet in `manager/aggregate.py`.

**Files:** `src/server/api/manager/aggregate.py` (new small aggregate, same shape as
`_RECORD_TOKEN_FIELDS`-style folding already used in `otel_trueup.py` for inspiration),
`service.py::org_overview`, `Overview.tsx`.

**Size:** S, do right after QW1 since the field is now familiar.

- [x] Land QW3 — `aggregate.model_usage_breakdown(rows)` reused org-wide (it was already
  generic over any row list); `OrgHighlights.model_mix`, rendered in Overview's `StatStrip` as
  `model mix: opus-4.8 / sonnet-5 / haiku-4.5` with the matching `68% / 22% / 10%` value.
  Verified live.

---

## Front-end scope gaps — found by mockup diff, mostly already documented

`src/webui/AGENTS.md`'s own U5/U6 change log (read only after the first draft of this doc) 
already names G1-G3 as *deliberate* Phase-1 phasing decisions, each with a stated reason — not
undocumented drift the way QW1 was. Keeping them here because they're still open, just
correcting the framing.

### G1 — PersonScreen "Session history" table
**What's wrong:** The mockup's per-person recent-sessions table (date / repo / duration / cost
/ PRs / quality chip, clickable into `SessionScreen`) is gone entirely on live `PersonScreen`.

**Documented reason (`AGENTS.md` U5):** "Person screen drops the session-history table and the
static spend-breakdown donut — both need session-level (D1) data, out of Phase 1 scope." D1
(session detail deep-dive) has since shipped (Phase 3/U7) — this is now buildable, just never
revisited after its stated blocker cleared.

**Root cause:** `service.py::person_detail` (A5) never queries a person's recent sessions —
it was scoped to PRs/velocity/peer-compare only. The underlying session rows are queryable
today (same `dashboard` catalog session-list query the sessions table screen already uses,
filtered by `user_id`); this is a missing rollup, not missing capture.

**Files:** `src/server/api/manager/service.py::person_detail`, `schemas/manager.py`
(`PersonDetail` — add `recent_sessions: list[SessionSummary]`), `PersonScreen.tsx` (restore
the table from the mockup, wire `onNavigate("session", …)`).

**Size:** M — needs a new query + a `QualityChip` label per session (reuse `quality.py`'s
per-PR quality classification, joined back to the session that produced it).

- [x] Land G1 — `_person_recent_sessions` (`manager/service.py`) joins a person's recent
  sessions to whichever PR they fed via `session_git_links` (new batched
  `pr_class.links_for_sessions` query) and that PR's `quality.quality_label`; new
  `RecentSession` schema, `recent_sessions` on `PersonDetail`. New regression test
  (`test_recent_sessions_carries_the_linked_prs_quality`). 350 backend tests green; frontend
  clean; verified live (table renders, row click navigates to the session).

### G2 — PersonScreen "Where the spend went" breakdown
**What's wrong:** The Building / Rework / Reading-context spend-share pie is gone. Live added
a "Habits" section instead (X3 recoverable breakdown) — useful, but a different question
("what patterns cost you money" vs. "where did the hours go").

**Documented reason (`AGENTS.md` U5):** same line as G1 — "the static spend-breakdown donut
... need[s] session-level (D1) data, out of Phase 1 scope; the habits list (X3) replaces the
donut." That's a stated, deliberate substitution, not an oversight — treat the question below
as "do we still want both" rather than "this was missed."

**Root cause:** Nothing in `person_detail` computes a per-person channel-cost split. The
building block exists at the session level — `graph_fold.py::channel_cost_of` — but nothing
rolls it up per-person the way `habits_from_recoverable` rolls up recoverable spend.

**Files:** `src/server/api/manager/aggregate.py` (new rollup over `channel_cost_of` across a
person's sessions), `service.py::person_detail`, `PersonScreen.tsx`.

**Size:** M. Consider whether this duplicates or should replace the "Habits" card — worth a
quick product call before building, since both answer "why did this person cost what they
cost" from different angles.

- [x] Land G2 — reused `aggregate.channel_totals` (already built for D2's `PRCost.channels`)
  over a person's own sessions rather than one PR's linked ones; `Person.channels`
  (`ChannelTotals`). Kept alongside Habits rather than replacing it (different questions --
  "where did the hours go" vs. "what patterns cost money"). New regression test; 352 backend
  tests green; frontend clean; verified live (donut renders between Recent PRs and the
  Quality/Peer grid, matching the mockup's original position).

### G3 — UsageScreen "Who's using their seat" per-person table
**What's wrong:** The mockup nests a per-person seat-utilization list under one sample team on
the org-wide Usage screen. Live drops it.

**Documented reason (`AGENTS.md` U5):** "Usage screen drops the old 'who's using their seat in
Platform' person list — A2 doesn't return per-person data (that's Team/A4's job)." Team/A4
shipped and does show this (`TeamScreen`'s seat-usage chart) — the open question is genuinely
"is UsageScreen's own copy of it redundant now," not "was it forgotten."

**Root cause:** `org_usage` (A2) rolls up to team granularity only; it never descends to
person-level token usage the way `team_detail` does. The per-person data
(`tokens_total`/`allowance`) already flows into `TeamScreen`'s own seat-usage chart — this
would be the same shape, just surfaced one level up for a "worst seats across the whole org"
view. Consider whether it belongs on `UsageScreen` at all or whether TeamScreen's version
already covers this and the mockup section is redundant — worth confirming intent before
building.

**Files:** `src/server/api/manager/service.py::org_usage`, `schemas/manager.py`,
`UsageScreen.tsx`.

**Size:** M.

- [x] Land G3 — decided not redundant with TeamScreen (that needs a click; this surfaces the
  flagged team directly on the org-wide screen). New `_person_usage`/`PersonUsage`/
  `SpotlightTeamUsage`; `OrgUsage.spotlight_team` = the highest-utilization *over*-allowance
  team expanded to its roster, `None` when nothing is over (never a guessed top team). New
  regression test; 354 backend tests green; frontend clean; verified live.

### G4 — ProjectScreen "All pull requests" flat list
**What's wrong:** The mockup lists every PR across every feature in a project. Live replaced
it with "Who's contributing" (a contributor rollup) — there is now no way to see the raw PR
list at the project level at all, only per-feature (`FeatureScreen`) or per-person
(`PersonScreen`).

**Documented reason, now stale (`AGENTS.md` U6):** "Dropped from Project detail (not in A6's
`ProjectSummary`/`Feature` schemas): the 'all pull requests' list ... (D2/D3, Phase 3)." This
one *did* have a stated path back — D2/D3 shipping was supposed to unlock it — but D2/D3
shipped in Phase 3 (PRScreen/FeatureScreen both live) and the list was never revisited. This is
the one G-item that's a genuine drop-through, not a still-open deliberate call.

**Root cause:** `project_detail` (A6) never assembled a flat PR list — `features: None` is
cold-start per the A6 progress note, and the PR list was implicitly features-shaped in the
mockup (`project.features.flatMap(f => f.prs)`). Once F4's feature-boundary logic lands
(already a tracked dependency for `features`), this list falls out for free from the same
data. Until then it can be built directly off `git_artifacts` filtered by repo, independent of
F4.

**Files:** `src/server/api/manager/service.py::project_detail`, `PRScreen`-shaped list schema
reuse, `ProjectScreen.tsx`.

**Size:** M, S if done as an interim repo-filtered list rather than waiting on F4.

- [x] Land G4 — built as the interim repo-filtered list rather than waiting on F4. New
  `repo_prs_in_range` query (any state, mirroring `user_prs_recent`'s shape scoped by repo
  instead of user), new `ProjectPRRef` schema, per-PR cost via the already-existing
  `pr_costs_for_ids` query. New regression test; 354 backend tests green; frontend clean;
  verified live.

### G5 — session title missing from manager-side session references (found, not from the mockup diff)
**What's wrong:** Every manager-surface place a session appears by itself -- `PRDetail.sessions`
("Contributing sessions" on PRScreen) and G1's new `PersonDetail.recent_sessions` -- showed
the raw `session_id` (`s2`) with no readable label.

**Root cause:** The causal graph fold already does the full job: the normalizer backfills the
vendor's `ai_title` onto `session_start` once Claude Code auto-titles a session
(`test_ai_title_lifted_onto_session_start`), `analysis/causal_graph/builder.py` lifts it into
`graph.meta["title"]`, and `worker/graph_fold.py::info_of` writes it to
`session_info.info["title"]` -- confirmed live: 52 of 75 real sessions in the stack DB carry a
title today. Both manager queries already select the whole `info` JSONB column; nothing ever
read `.get("title")` out of it. Exactly the QW1 pattern (data fully computed and stored,
never promoted) -- caught because reviewing G1 surfaced raw session ids in the UI.

**Fix:** `title: str | None` added to `PRSessionRef` and `RecentSession`; `pr_detail` and
`_person_recent_sessions` both now read `(r.info or {}).get("title")`. `None` stays honest for
a session the vendor never auto-titled (23 of 75 today), never guessed.

- [x] Land G5 — new regression tests in both `test_manager_service_pr_detail.py` and
  `test_manager_service_person_detail.py`; 351 backend tests green; frontend clean; verified
  live (PRScreen's "Contributing sessions" now shows real titles instead of raw session ids).

---

## TODO backlog, by track

### Track TZ — user-local time (done, 2026-09-13)
All of `rhythm-tz` closed in one pass: [`precedence.py`](src/server/api/precedence.py) and
[`single_user/service.py`](src/server/api/single_user/service.py)'s bucketers now take a
`ZoneInfo` param instead of hardcoding `timezone.utc`; `activity_by_day`
(`config/queries/dashboard.yaml`) buckets days via `timezone(:tz, s.started_at)::date` instead
of a bare `::date` cast, same fix at the SQL layer.

`users.timezone` (nullable IANA string, migration `a050131eb86f`) is the new preference column
-- outside directory sync's own field set (`config/queries/directory.yaml`'s
`update_user_fields` never names it), so a re-sync can't clobber it, no carve-out needed.
`GET`/`PUT /v1/users/{id}/timezone` (single_user's second write, after feedback) let the
browser read and set it; validated against the real IANA database (`zoneinfo`), not a
hand-maintained list. Detection is the browser's own job -- a new site-wide banner
(`webui/src/shell/timezone.tsx`) compares `Intl.DateTimeFormat().resolvedOptions().timeZone`
against the stored value and offers to update on a mismatch, dismissible per detected zone
(`localStorage`, keyed by zone so a later trip to a *different* zone still prompts). No manual
override picker this pass -- auto-detect-only, deliberately deferred (a manual picker is just
another way to call the same `PUT`, so it's free to add later without rework).

Backfill note: an existing user with no timezone set reads as UTC everywhere, byte-for-byte the
same behavior as before this column existed -- this was a strictly additive rollout, no
migration of historical bucketing needed.

- [x] Add timezone to the user/directory record
- [x] Thread it through `precedence.py`'s bucketers
- [x] Thread it through `single_user/service.py`'s day-boundary/streak walks
- [x] Remove all `TODO(rhythm-tz)` markers

### Track churn/reopen (git-capture gap)
`X1-churn`, `X1-reopen` ([`quality.py:9-12`](src/server/api/manager/quality.py:9)),
`A6-reopens` ([`service.py:576`](src/server/api/manager/service.py:576)). Post-open push churn
and reopen counts aren't capturable today because commit-artifact rows from push webhooks
carry empty `stats`, and no PR state-transition history is persisted (only current state).

**Root fix:** capture full stats on push-webhook commits (upstream of this repo's ingest, may
need a GitHub API backfill call per commit rather than relying on the webhook payload), and
persist PR state transitions (a small `pr_state_events` table, written on every webhook state
change) instead of overwriting `git_artifacts.state` in place.

**Size:** L — this is an ingest-side capture change, not just an API rollup. Do this before
attempting X2's "full churn+rounds+reopen composite" the design describes; today's X2 uses
review_rounds alone precisely because of this gap.

**Implemented as its own mini-plan:** `docs/plan/2026-09-13-churn-reopen-capture-implementation.md`
(Landings 1-4 landed 2026-09-13/14; Landing 3 = the X2 composite, still open below).

- [x] Capture full commit stats on push webhooks (or backfill via API) — `git_commit_stats`
  worker via the compare API (Landing 2) + `cli_backfill_commit_stats` for history (Landing 4b)
- [x] Persist PR state-transition history — `git_artifact_events` table, written from every
  `pull_request` transition (Landing 1) + `cli_backfill_reopens` for history (Landing 4a)
- [x] Wire `X1-churn`/`X1-reopen`/`A6-reopens` off the new data — PR-detail `churn_post_lines`/
  `rework_ratio`/`reopens` + a live "Reopened" flag, and A6 `reopened_prs`
- [x] Revisit X2's quality matrix to use the full composite once available — Landing 3: the
  acceptance-cost axis is now an OR-composite over review_rounds / reopens / rework_ratio, with
  thresholds in `config/api.yaml` (`quality:`), closing `X2-config`. Defaults preserve prior
  behavior; reopen/rework cutoffs are the new product-tunable knobs.

**Track churn/reopen is fully landed** (Landings 1-4). Only downstream consumers remain open:
`A5-peer-task-mix` (below) can now use X2's quality axis, and the historical backfills
(`cli_backfill_reopens` / `cli_backfill_commit_stats`) are ready to run against the live stack.

### Track forecast/budget (feeds Overview's biggest gap)
`X5-trend`, `X5-config`, `E1-budget-period`
([progress notes](docs/plan/2026-09-10-mvp-implementation-progress.md)). Forecast is a flat
rate off today's own average, not a real fit; budget burn projects to the requested window's
own end rather than a rolling period. This is what backs Overview's "Unit economics"
`UnavailableNote` — the single biggest visible hole in the live app.

**Size:** L — needs an actual trend model decision (what fit, what window) before code; treat
as its own mini-plan rather than a single PR.

**Update (2026-09-15): the two model-independent halves landed; the trend fit is owned
elsewhere.** Digging into the code (this session) showed the track is not one L but four
pieces of very different size — most of the machinery already exists and works. `X5-trend`
(the real trend-fit model) is being reworked separately; `X5-config` splits, and its
still-open half (`_CONE_WIDEN_RATE`, [`forecast.py:47`](src/server/api/forecast.py:47)) is
deliberately left to the trend-model owner rather than configuring a knob that fit is about to
replace. The two pieces that don't depend on the fit are done:

- **E1-budget-period done.** `budget_burnup` itself was correct; the bug was entirely in
  `economics()`'s caller, which passed `days_in_period = window length = days reported`, so the
  projection leg was always empty (the "forecast" never extended past the handoff). New
  [`budgets.period_bounds`](src/server/api/manager/budgets.py) resolves `config.budget_period`
  ("monthly") to the calendar period containing the window's end (`to` = the deterministic,
  testable "as-of" anchor); `economics()` now fetches period-to-date actuals and projects to
  the period's own last day. `TODO(E1-budget-period)` markers removed (rule 4). New tests:
  `period_bounds` unit (3) + `test_budget_burn_forecasts_to_the_month_end_not_the_window_end`;
  full `tests/server/api` green after regenerating `openapi.json`/`api-types.ts`.
- **Overview "Unit economics" re-enabled (interim).** `Overview.tsx`'s hardcoded
  `UnavailableNote` is replaced by a compact `UnitEconomics` block — its own org-scoped read of
  the same E1 endpoint `EconomicsScreen` already renders, showing the cost-per-PR median/band +
  forecast cone, with a link to the full screen. **Deliberately interim:** it surfaces today's
  flat-rate cone and will be re-worked once the better trend model lands. typecheck / lexicon /
  build green.

- [ ] Decide the trend-fit approach (product call, not just engineering) — owned separately
- [ ] Move thresholds to `config` (`X5-config`) — `_FORECAST_PERIODS_AHEAD` still movable now;
      `_CONE_WIDEN_RATE` left to the trend-model rework to avoid touching `forecast.py` twice
- [x] Fix budget-period projection (`E1-budget-period`) — 2026-09-15
- [x] Re-enable Overview's "Unit economics" section end to end — 2026-09-15 (interim wiring
      against the current model; to be reworked with the new fit)

### Track segment-level pricing (ties to the `pricing_config=None` gap flagged earlier)

**Update (2026-09-13): the channel/carry half is done.** `graph_fold.py::channel_cost_of`/
`carry_of` priced `usd_micros` per model at the session's own date (never "today" — a real
rate change only ever prices sessions from that date forward, so this is correct forever
regardless of when it runs). `GraphFold(pricing=...)` is opt-in (bare `GraphFold()` stays
unpriced on purpose, so no existing test's behavior changed just because `config/pricing.yaml`
happens to exist on the CWD); `ingest/app.py`/`worker/cli_work.py` now pass
`load_pricing_survivable()`. This is what G2's donut (and Session/PR/Feature's own pre-existing
"Where the cost went" donuts, which had the identical `$0`-forever symptom in any real
deployment, not just G2 — same root cause, same fix, for free) actually reads. New
`tests/server/worker/test_graph_fold_pricing.py`; 1161 backend tests green (the same one
pre-existing, unrelated `test_otel_e2e.py` failure as before).

**Not yet backfilled**: like the session-title fix, this only prices sessions folded from now
on. Already-ingested sessions keep `usd_micros: null` in `channel_cost`/`carry` until a
backfill re-derives them — unlike titles, the raw ingredients (persisted graph + `models`) are
already in the DB (`session_graph`/`session_info.models`), so this doesn't need a re-ingest at
all, just a script that loads the persisted graph and re-runs `channel_cost_of`/`carry_of`
against it with pricing, then merge-patches the two fields into `session_info.info`. Not built
yet — ask if you want it.

**Still open** — the per-*segment* pricing (`worker/segments/__init__.py::compute_segments`
still calls `build_segments(..., pricing_config=None)`), which is what F3/X3-phase2/
X4-per-segment-model below actually need. Channel/carry pricing doesn't touch this at all;
segments are a separate breakdown (by phase/beat, not by input/output/cache channel).

`F3-segment-split` ([`aggregate.py:79`](src/server/api/manager/aggregate.py:79)),
`X3-phase2` ([`aggregate.py:169`](src/server/api/manager/aggregate.py:169)),
`X4-per-segment-model` ([`aggregate.py:303`](src/server/api/manager/aggregate.py:303)). All
three trace back to `worker/segments/__init__.py::compute_segments` calling
`build_segments(..., pricing_config=None)` — segments/beats are unconditionally unpriced.
Fixing that one call site is the actual unlock for all three:
- F3: a mixed session's cost can't be split per-repo at the segment level without segment-level
  prices.
- X3-phase2: resume overhead / read-then-discard / retry burn / model-oversizing are shown as
  raw token estimates because pricing them needs the same missing segment prices.
- X4-per-segment-model: can't attribute more than one model per segment without segment-level
  granularity existing at all.

**Size:** M for the `pricing_config` wire-through itself; L in aggregate to actually consume
per-segment prices in all three call sites once available.

**Superseded 2026-09-15** by
[`docs/plan/2026-09-15-segment-level-pricing-implementation.md`](2026-09-15-segment-level-pricing-implementation.md),
which re-sized this from a direct read of the code (the wire-through was XS, not M; F3 turned out
gated on a missing consumer rather than needing L of aggregate work; X3-phase2 split into a free
half, a clean pricing thread, and two estimate-buckets blocked on a product call). Phases 1/3a/3b
of that plan are done; see it for current status of F3/X3-phase2/X4.

- [x] Wire `pricing_config` into `compute_segments`/`build_segments` (Phase 1, done 2026-09-15)
- [ ] F3: per-repo segment cost split (Phase 2 — investigated 2026-09-15, deferred: no consumer)
- [ ] X3-phase2: price the four EFF §2 recoverable buckets properly (Phase 3 — rework/resume done
      2026-09-15; read-then-discard/retry still gated on a ground-rule-3 product decision)
- [x] X4-per-segment-model: allow >1 model per segment (Phase 4 — done 2026-09-16, its own
      commit; see `2026-09-15-segment-level-pricing-implementation.md`'s Phase 4 for detail)

### Track benchmark distributions (done, 2026-09-15)
`D3-benchmark`, `D3-envelope-scope`. Implemented per
[`docs/plan/2026-09-14-d1-d3-implementation.md`](2026-09-14-d1-d3-implementation.md) (T2/T3),
commit `deccf09`. Real data was checked first (the research note,
`docs/design/notes/2026-09-14-d1-d3-research.md`): the one connected repo has 23 merged PRs but
18 of its features are single-PR, so the old feature-local envelope was a degenerate one point —
the concrete case for widening.

- `D3-envelope-scope`: `feature_detail` now draws the cost-to-finish envelope from the repo's
  trailing merged-PR distribution (`_repo_cost_envelope` over `repo_merged_prs_in_range` +
  `pr_costs_for_ids`), gated by a new `ForecastConfig` (`envelope_trailing_days=90`,
  `envelope_min_prs=5`) with a feature-local fallback below the floor. Same one-cost-source path,
  no second cost path. `CostEnvelopeChart`/`cost_to_finish` already rendered this shape.
- `D3-benchmark`: `aggregate.feature_benchmark` over `quality.list_features_for_repo` gives the
  feature's cost percentile + peer `Dist`; new `FeatureBenchmark` schema (unpinned
  `FeatureDetail.benchmark` from `None`), rendered on `FeatureScreen` via `PercentileBar` + a
  subject-position caption. Cost axis only this pass; a lead-time axis is `TODO(D3-benchmark-time)`
  (needs per-peer lifecycle timestamps `list_features_for_repo` doesn't compute).

**Caveat:** real numbers on the live stack await the session↔PR re-correlation (the impl plan's
T0 — a sha-correlation query-path bug fixed this session in `config/queries/pr_class.yaml`, plus
an ops step to re-run the `git_correlate` ring on the stack). Until then `session_git_links` is
empty there and all PR costs read `$0`. Verified end-to-end on seeded data + in the browser instead.

- [x] Check real repo PR volume is enough for a repo-wide distribution (research note)
- [x] Build cross-feature benchmark aggregate (`aggregate.feature_benchmark`)
- [x] Widen envelope scope from feature-only to repo-wide (`_repo_cost_envelope` + `ForecastConfig`)

### Track lifecycle & context curve (done, 2026-09-15)
`D1-lifecycle`, `D1-context-curve`. Implemented per
[`docs/plan/2026-09-14-d1-d3-implementation.md`](2026-09-14-d1-d3-implementation.md) (T1/T4),
commits `deccf09` + `cb129f1`. Both were cheaper than the "L each" estimate — see the research
note.

- `D1-context-curve`: **not** a new extraction — the per-request context series
  (`cache_read + cache_write`) was already computed inside `graph_fold.carry_of` and discarded
  except for its last point. New `graph_fold.context_curve_of` keeps the whole per-turn series
  (+ a `compaction` flag), written into `info` in `info_of` (root-only, next to `segments`);
  `_session_economics_block` promotes it, `SessionScreen` renders it via the already-built
  `ContextCurve`. Backfillable from `session_graph` with no re-ingest (deferred, optional).
- `D1-lifecycle`: `_session_lifecycle` joins the session's linked PR(s) (`links_for_session`) to
  their open / first-review / merge timestamps (`pr_basic_facts`, `pr_review_children`) →
  build → review → ship milestones on `SessionScreen`'s timeline. **Rework loops added**
  (`cb129f1`) once churn/reopen's `git_artifact_events` landed: a new `pr_reopen_events` query
  anchors a rework span per reopen. `TODO(D1-lifecycle)`/`TODO(D1-context-curve)` markers removed
  from source (rule 4). New A/B allowlist entry `info.context_curve*`.

**Caveat:** `D1-lifecycle` shows real spans on the live stack only after the session↔PR
re-correlation (same `session_git_links`-is-empty blocker as the benchmark track above);
`context_curve` is live for any session folded from now on. Both verified in the browser (mock)
+ against real persisted graphs.

- [x] D1-context-curve: persist a per-turn context-size series in the fold (`context_curve_of`)
- [x] D1-lifecycle: build lifecycle spans from `session_git_links` + PR events (build→review→
      rework→ship, `_session_lifecycle` + `pr_reopen_events`)

### Track reasoning rollup (done, 2026-09-13)
`F5-worker-gap` closed: [`graph_fold.py::reasoning_tokens_of`](src/server/worker/graph_fold.py)
sums each assistant node's own `estimated_reasoning_tokens`
(`analysis/causal_graph/reasoning.py`, already attached per-node at build time -- a pure
summation, no new extraction needed) into `session_info.info.reasoning_tokens`. Confirmed live:
`aggregate.reasoning_share` lit up with zero API/query-shape changes, exactly as scoped --
`GET /v1/org/usage` now returns a real `reasoning_share` block on a freshly re-folded session.
A/B differential allowlist gained one entry (`info.reasoning_tokens`, a wholly new field with
no EconFold/baseline equivalent to diff against).

Same re-ingest caveat as the other fold-level fixes this pass: an already-ingested session
folded before this rollup shipped has no `reasoning_tokens` key at all (not `0` -- `None`,
correctly read as "never estimated" by `reasoning_share`'s own `seen` gate) until it's
re-folded, which the positional-dedup wall in `fold_handler.py` means only a wipe+reingest (or
this session's own next new event) actually does.

**Frontend surfacing (done, 2026-09-13):** `OrgOverview.highlights.reasoning_share` (a new
`StatStrip` entry on Overview), `OrgUsage.reasoning_share` (a stat next to "produced no code"
on Usage), and two new `RecentSession` fields (`reasoning_tokens`/`tokens_total`, a "Reasoning"
column on Person's session table) -- each a small, reusable-as-is backend addition (the
existing `aggregate.reasoning_share()` call or a two-line row addition) plus the matching
webui tile/column. Verified in the browser against mock data on all three screens.

Deliberately still out: My-usage. It doesn't call its backend endpoint at all today
(`TODO(U5-myusage)`, headline numbers still read `mock.ts`) -- wiring reasoning tokens there
means unwinding that separate, larger mock-to-live gap first, not a reasoning-rollup task.

- [x] Roll up per-node reasoning estimate into `session_info.info` in the fold
- [x] Confirm `reasoning_share` populates without further API change

### Track repo identity unification
`repo-identity-unification` ([`aggregate.py:573`](src/server/api/manager/aggregate.py:573)).
`session_info.repo_id` (git_ident scheme) and `git_artifacts.repo_id` (git-source numeric
scheme) are two unreconciled namespaces; `resolve_git_repo_id(s)` is the interim bridge via
`git_repos.full_name`. The real fix is one canonical repo identity end to end.

**Size:** L — touches ingest, storage, and every query joining sessions to git artifacts by
repo. Do this deliberately, not as a drive-by; the bridge function works today.

- [ ] Design one canonical repo-id scheme
- [ ] Migrate `git_artifacts`/`session_info` onto it
- [ ] Remove `resolve_git_repo_id(s)` bridge once both sides agree

**Related, separate bug found 2026-09-13:** `resolve_git_repo_ids`
([`aggregate.py:640`](src/server/api/manager/aggregate.py:640)) only ever bridges a session
`repo_id` that starts with `github:` — a `local:<cwd-basename>` id (the floor of
`worker/graph_fold.py::repo_info`'s evidence ladder, used whenever a session has no `pr_link`
and no resolvable remote) is silently dropped and never joined to `git_repos`, even when a real
GitHub App connection exists for that repo. This broke both the Features section and G4's new
PR list identically on the live stack.

Interim fix shipped this pass (source-side, not the bridge): the upload script
(`utils/upload_claude_sessions.sh`) now resolves each session's real `git remote get-url
origin` on the developer's machine and ships it as a `.talium-git-remotes.json` sidecar in the
same tarball; the collector engine (`engine.py::_apply_git_remote`) matches it onto the
session by `cwd` and backfills `session_start.git_remote`, which `repo_info`'s `remote` tier
now prefers over the `path`/cwd-basename floor. This fixes it correctly for *new* sessions
going forward (no heuristics — a real git remote, not a guess) but does nothing for already-
ingested `local:` sessions, which the positional-dedup wall in `fold_handler.py` prevents a
re-ingest from reaching.

- [x] Source fix: upload script → collector → `session_start.git_remote` → `repo_info`'s
      `remote` tier (2026-09-13)
- [ ] Backfill fix: a basename-matching fallback in `resolve_git_repo_id`, scoped to repos
      connected under the same org, for `local:` sessions already ingested before this fix
      existed (heuristic — deferred, not yet built)

### Track product-logic upgrades (independent, smaller)
- `A1-initiatives` ([`aggregate.py:459`](src/server/api/manager/aggregate.py:459)): "initiative
  started" is proxied by issue created/closed weekly; a true feature-level start needs F4's
  boundary-key logic first.
- `A2-per-seat-allowance` ([`aggregate.py:532`](src/server/api/manager/aggregate.py:532)): team
  budget is even-split per seat; needs a real per-user allowance concept (schema + admin UI to
  set it — currently doesn't exist anywhere).
- `A5-peer-task-mix` ([`aggregate.py:444`](src/server/api/manager/aggregate.py:444)): "cheapest
  peer" compares $/PR only; a real task-mix match needs X2's quality axis, which itself is
  gated on Track churn/reopen above.
- `H5` ([`features.py:19`](src/server/ingest/git/features.py:19)): no branch/label clustering
  for PRs with no issue link at all.

**Size:** M each, independent — pick whichever's most valuable next; none block each other.

- [ ] A1-initiatives (blocked on F4)
- [ ] A2-per-seat-allowance (needs a new allowance concept, not just a query)
- [x] A5-peer-task-mix (2026-09-16 — switched to $/KLOC (volume-normalized), not a task-mix
      match; see `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 1. The
      real task-mix match TODO in `aggregate.py::cheapest_peer` stays open, still gated on X2)
- [ ] H5 branch/label clustering

### Track config extraction (mechanical, do in one sweep)
`X2-config` (done, Landing 3), `X4-config`, `A2-config`, `A4-config` — quality-matrix,
budget-tier, and cutoff thresholds are hardcoded constants pending product sign-off on the
actual numbers, not missing logic. Move each to `config/api.yaml` once the numbers are
confirmed; no behavior change if the same constants are used as defaults.

**Size:** S total, do the remaining three together.

**Update (2026-09-15): all threshold values are now externalized; only the product number
sign-off remains.** `X4-config` moved as part of the model-fit reasoning-demand rework
(`model_fit:` block — tier mapping, demand weights, tier-downgrade savings ratio). `A2-config`
(seat-utilization `over`/`under` bands) and `A4-config` (`room_to_save_usd_micros`) moved into a
new `utilization:` block, with the aggregate functions (`usage_status`/`seats_under_threshold`/
`person_status`) now taking `config.utilization`; defaults reproduce the old constants exactly,
so no behavior change. Every value in `config/api.yaml` is still a product-tunable default, not a
confirmed number — that sign-off is the one open item.

- [ ] Confirm threshold values with product (the numbers themselves — logic/config now done)
- [x] Move X2 thresholds into `config/api.yaml` (`quality:` block — Landing 3)
- [x] Move X4/A2/A4 thresholds into `config/api.yaml` (X4 via model-fit rework; A2/A4 →
      `utilization:` block, 2026-09-15)

### Track front-end loose ends
- `U5-myusage` ([`MyUsage.tsx:27`](src/webui/src/screens/MyUsage.tsx:27)): headline
  spend/PRs/sessions numbers still read `mock.ts`, not the live API.
- `U7-econ-picker` ([`EconomicsScreen.tsx:31`](src/webui/src/screens/EconomicsScreen.tsx:31)):
  the scope picker fetches a heavier endpoint than it needs; a dedicated names-only endpoint
  was deferred.

**Size:** S each.

- [x] U5-myusage: wire MyUsage headline numbers to the live API (2026-09-16 —
      `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 3)
- [x] U7-econ-picker: add a lightweight names-only endpoint for the scope picker (`GET /v1/scopes`,
      2026-09-15 — `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 4)

---

## Suggested attack order

1. QW1 → QW2 → QW3 (quick wins, same afternoon)
2. G1 → G4 → G3 → G2 (silent gaps; G2/G3 need a short product confirmation first — flagged above)
3. Track config extraction (mechanical, clears four TODOs fast)
4. ~~Track reasoning rollup~~ (done, 2026-09-13)
5. Track segment-level pricing (unlocks three TODOs at once)
6. ~~Track TZ~~ (done, 2026-09-13)
7. Track churn/reopen (unlocks A5-peer-task-mix and X2's real composite downstream)
8. ~~Track benchmark distributions~~, ~~Track lifecycle & context curve~~ (both done, 2026-09-15
   — see `docs/plan/2026-09-14-d1-d3-implementation.md`); Track repo identity, Track forecast/budget
   remain — each is its own mini-project; sequence by product priority, not dependency (none
   block each other).
9. Track product-logic upgrades — pick up opportunistically as their blockers clear.
