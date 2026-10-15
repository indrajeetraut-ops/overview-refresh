# Implementation plan — D1 (lifecycle + context curve) & D3 (benchmark distributions)

**Date:** 2026-09-14
**Supersedes the two open tracks in** `docs/plan/2026-09-12-mvp-gap-closure-plan.md`:
"Track lifecycle & context curve" (D1-lifecycle, D1-context-curve) and
"Track benchmark distributions" (D3-benchmark, D3-envelope-scope).
**Research basis:** `docs/design/notes/2026-09-14-d1-d3-research.md` (read it first — every
size and sequencing call below traces to a finding there, incl. live-stack data checks).

## What changed vs the gap-closure plan's sizing

The research downgraded three of four items and surfaced one hard prerequisite:

| Item | Old size | Now | Reason |
|---|---|---|---|
| **T0** fix sha-correlation query | (not tracked) | **S–M** | `session_git_links` empty on stack → blocks T2/T3/T4; a query-path bug, shas ARE captured |
| **T1** D1-context-curve | L | **S–M** | series already computed in `carry_of`; FE `ContextCurve` already built; backfill from `session_graph`, no re-ingest |
| **T2** D3-envelope-scope | M | **S–M** | query swap onto `repo_merged_prs_in_range`; `CostEnvelopeChart` already built |
| **T3** D3-benchmark | M | **M** | population = `list_features_for_repo`; `PercentileBar` already built |
| **T4** D1-lifecycle | L | **M** | PR-timeline capture already shipped by churn/reopen; this consumes it |

**Build order:** T1 → T0 → T2 → T3 → T4. T1 is first because it is the only item that touches
neither `session_git_links` nor `git_artifact_events`, so it lands value while T0 is chased.
T0 then unblocks the cost/link-dependent trio.

---

## T0 — Fix the sha-based session↔PR correlation query (prerequisite for T2/T3/T4)

**Problem.** `session_git_links` has **0 rows** on the stack though `pr_classification` has 25.
**Diagnosis (corrected 2026-09-14 — this is NOT a capture gap):** commit shas ARE captured
(25 sessions carry them; 123 `gitOperation` events total). The bug is in the correlator query.
A git commit is stored as a **`tool_result`** event with the sha at
`payload.raw.toolUseResult.gitOperation.commit.sha` (e.g. `"f1817e7"`). But `session_commit_shas`
([pr_class.yaml:42-48](config/queries/pr_class.yaml:42)) reads `event_type='tool_call'` and
`payload.commit.sha` — a shape the normalizer never produces. The query's cited source,
`claude_code.py::_git_commit_from_result`, **does not exist** (named in two comments, never
implemented — `_make_tool_result`/`_make_tool_call` flatten no commit field). So the sha join has
always read an empty path → 0 shas → 0 links. PRs are fine (25/25 carry full `commit_shas`).

**Consequence if skipped.** Every PR cost resolves to `$0` (all cost paths route through
`session_git_links → session_info.cost_effective_usd_micros`), so T2/T3 render distributions of
zeros and T4 has no PR to hang spans on — indistinguishable from a code bug.

**Fix — correct the query path; keep sha as the mechanism (no re-ingest, no branch fallback).**
Point the three affected query sites at the real path and preserve the already-designed prefix
match (CC captures git's abbreviated 7-char hash; GitHub stores the full 40-char sha):
```sql
-- session_commit_shas (and the two sibling sites): read the tool_result gitOperation
SELECT DISTINCT e->'payload'->'raw'->'toolUseResult'->'gitOperation'->'commit'->>'sha' AS sha
FROM sessions sess, LATERAL jsonb_array_elements(COALESCE(sess.signal_blob->'events','[]'::jsonb)) e
WHERE sess.session_id = :session_id
  AND e->>'event_type' = 'tool_result'
  AND e->'payload'->'raw'->'toolUseResult'->'gitOperation'->'commit'->>'sha' IS NOT NULL;
```
The stored `signal_blob` already holds the data, so **no re-ingest** is required — a re-run of
the `pr_classify`/`git_correlate` rings repopulates `session_git_links`.

**Files:**
- `config/queries/pr_class.yaml` — fix `session_commit_shas` ([:42](config/queries/pr_class.yaml:42))
  and the two sibling sites at [:99](config/queries/pr_class.yaml:99) and
  [:133](config/queries/pr_class.yaml:133) (same wrong `tool_call`/`payload.commit.sha` shape).
  Correct the stale comments naming `_git_commit_from_result`.
- No normalizer change strictly needed (the data is in `raw`). **Optional hardening:** flatten
  `gitOperation.commit.sha` onto the tool_result payload in `_make_tool_result`
  (`collector/normalizer/claude_code.py:1343`) so the query stops reaching into `raw` and future
  raw-shape drift can't silently re-break it — but that path needs re-normalization to benefit
  existing sessions, so treat it as a follow-up, not the fix.

**Verify.** Correlator test with a real tool_result gitOperation event shape (fixture from the
stack): the session's abbreviated sha prefix-matches the PR's full sha and a `method="commit_sha"`
link is written; a session that committed nothing links to nothing. Then on the stack: re-run the
rings and confirm `session_git_links` is non-empty and `pr_costs_for_ids` returns non-zero.
**Verified payoff:** corrected sha-prefix join links **19 of 82 sessions**.

**Residual (honest, deferred — not part of T0).** Sessions whose local commits were squash/
rebase-merged carry shas that never appear in the PR's commit list, so they won't sha-match.
That is a genuine smaller gap; a branch-match *fallback* tier is a possible future lower-confidence
add for it, but it is explicitly NOT the primary fix and not needed to unblock T2/T3/T4.

**Size:** S–M (a query-path fix + test + ring re-run; down from the M a new correlation tier
would have been).

- [x] Fix the three `pr_class.yaml` sha-extraction sites (`session_commit_shas`,
  `sessions_with_commit_sha`, `correlation_coverage`) to the real
  `tool_result`/`payload.raw.toolUseResult.gitOperation.commit.sha` path; comments corrected.
- [x] Root cause of why it slipped: `test_correlate.py::_session_with_commit_shas` built the same
  wrong shape as the buggy query — fixed to the real vendor shape, so all 12 correlate tests now
  exercise the corrected path. Added `test_query_sites_read_the_real_gitoperation_tool_result_shape`
  locking all three query sites. 65 pr_class tests green (+21 correlate/query).
- [x] Verified end-to-end on the live `econ` stack (read-only sim): the corrected join links
  **18 sessions → 10 PRs (22 links)**; 10 of 23 merged PRs now get a linked session → non-zero cost.
- [x] Fixed the identical sibling bug in `segments/beats.py::_wrap_up` (2026-09-15, own change):
  commit wrap_up beats now fire from `_commit_sha_from_tool_result` (the real tool_result
  `gitOperation.commit.sha` path), so a mid-session commit opens a segment boundary again. New
  `test_segments_beats.py` (6 tests, incl. the boundary case); A/B differential tolerated the
  segment shift (`info.segments*` allowlisted), worker (160) + api (386) green.
- [ ] **Ops step (needs go-ahead — mutates the running stack):** restart the worker/api
  containers so they load the new `pr_class.yaml`, then enqueue `git_correlate` to repopulate
  `session_git_links` on the live stack. Correctness is already proven above; this is the deploy.
- [ ] (Optional follow-up) flatten the sha in `_make_tool_result` to stop reaching into `raw`.

---

## T1 — D1-context-curve (persist + surface the per-turn context series)

**Problem.** `_session_economics_block` returns `"context_curve": None`
([single_user/service.py:882](src/server/api/single_user/service.py:882)); SessionScreen renders
an `UnavailableNote` ([SessionScreen.tsx:228-233](src/webui/src/screens/SessionScreen.tsx:228)).

**Key finding.** The series is already computed and discarded. `carry_of`
([graph_fold.py:705](src/server/worker/graph_fold.py:705)) builds
`contexts = [(ts, cache_read_tokens + cache_write_tokens) …]` per assistant node — the fold's
own definition of per-request context size ([graph_fold.py:694](src/server/worker/graph_fold.py:694))
— then keeps only `contexts[-1]` as `carry.projected.context_tokens`. `turns_of`
([graph_fold.py:295](src/server/worker/graph_fold.py:295)) supplies the per-turn frame for the
x-axis. **The FE component already exists**: `ContextCurve`
([charts.tsx:228](src/webui/src/components/charts.tsx:228)),
`{ data: { t: string; ctx: number; compaction?: boolean }[] }`.

**Approach.** Add a `context_curve` fold output — a per-turn series
`[{index, ts, context_tokens}]` — by attributing each assistant node's context value to its
turn (reuse `turns_of`'s descendant walk, or bucket `contexts` by turn boundary). Overlay
compaction turns from the existing `compactions` fold output so `ContextCurve`'s `compaction`
flag lights up (a compaction is exactly where the curve drops). Write it in `info_of`
([graph_fold.py:957](src/server/worker/graph_fold.py:957)) next to `segments`/`practices`.
Per-turn granularity is fine — max observed session is ~90 turns, no downsampling needed.

**Files:**
- `src/server/worker/graph_fold.py` — new `context_curve_of` (or extend `carry_of` to also
  return the full series); write `info["context_curve"]` in `info_of`.
- `src/server/api/single_user/service.py::_session_economics_block` — promote
  `info.get("context_curve")` instead of `None`; drop the `# TODO(D1-context-curve)` marker.
- `src/webui/src/screens/SessionScreen.tsx` — type `context_curve` in the hand-typed
  `SessionEconomics` dict (route still untyped per schemas/README; economics block is hand-typed
  per AGENTS.md U7), map to `ContextCurve`'s `{t, ctx, compaction}` shape, replace the
  `UnavailableNote` with `<ContextCurve>` (keep the note as the cold-start branch).
- A/B differential allowlist — add `info.context_curve` (wholly new field, no baseline to diff).

**Backfill (no re-ingest).** All 82 sessions have their graph in `session_graph`. A backfill
script loads each graph, re-runs the `contexts`/turn derivation, and merge-patches
`info["context_curve"]` — same pattern the segment-pricing note describes. Without it, only
sessions folded after this ships get a curve (the positional-dedup wall in `fold_handler.py`
means a re-ingest won't re-fold existing sessions).

**Verify.** Fold unit test: a crafted family with rising cache_read across turns and one
compaction yields a monotone-then-drop series with the compaction turn flagged. Then live:
re-fold one session, hit `GET /v1/sessions/{id}?view=economics`, confirm `context_curve` is a
non-empty series; screenshot the chart on SessionScreen.

**Size:** S–M.

- [x] Added `context_curve_of` (`graph_fold.py`) — per-turn END-of-turn context
  (`cache_read+cache_write` of the turn's last assistant request) + `compaction` flag; wired into
  `info_of` root-only next to `segments`. 4 unit tests (`test_graph_fold_context_curve.py`).
- [x] Promoted `info.get("context_curve")` in `_session_economics_block`; removed the TODO marker.
  New promotion test in `test_service_session_detail.py` (31 green); cold-start stays `None`.
- [x] Wired `ContextCurve` on SessionScreen (ctx→thousands, x=`T{index}`; cold-start note kept as
  the empty branch); mock given a sample rising-with-compaction series. FE typecheck / lexicon /
  vitest (25) / build all green; **browser-verified in mock mode** (chart renders, compaction
  marker at T5, tooltip works, no console errors).
- [x] Verified `context_curve_of` on a real persisted `session_graph` (87-point curve, sane shape,
  compactions flagged on turns 10/21/26); added `info.context_curve*` to the A/B allowlist.
- [ ] Backfill script over `session_graph` for already-folded sessions (deferred — same optional
  status as the other fold backfills; live curve appears on any session folded from now on).
- [x] Fold test (4) + real-graph verification done (see above)

---

## T2 — D3-envelope-scope (widen cost-to-finish envelope to repo-wide)

**Problem.** `feature_detail` envelopes over *this feature's own* member-PR costs
([manager/service.py:993](src/server/api/manager/service.py:993)). With 18 of the repo's
features single-PR (research), that input is length 1 → a degenerate one-point envelope.

**Approach.** Cost the *repo's* trailing merged PRs and feed that to
`forecast.cost_per_pr_envelope`; keep `feature_cost_to_finish(open_prs, envelope)` unchanged.
Feature detail already resolves its repo (`git_repo_id` from `f.ref.project_key`). The pieces
exist: `repo_merged_prs_in_range` ([manager.yaml:254](config/queries/manager.yaml:254)) +
`pr_costs_for_ids` ([manager.yaml:70](config/queries/manager.yaml:70)) +
`cost_per_pr_envelope` ([forecast.py:29](src/server/api/forecast.py:29)).

**Decisions (defaults recommended, confirm before merge):**
- **Trailing window:** last 90 days of merged PRs (recommend), vs all-time. 90d matches the
  "recent $/PR" language `feature_cost_to_finish`'s `basis` string already uses.
- **Below-volume fallback:** if the repo has < N merged PRs in-window (recommend N=5), fall back
  to the feature-local envelope rather than publish a thin repo distribution. Record which scope
  was used so the UI can label it.

**Files:**
- `src/server/api/manager/service.py::feature_detail` — build `pr_costs` from the repo's
  in-window merged PRs (new fetch) instead of only `f.member_pr_ids`; thread the window from
  config; keep member-cost path for the fallback.
- `config/api.yaml` — envelope window + volume floor (co-locate with the other X5 config the
  gap-closure plan's config track will move).
- No FE change — `CostEnvelopeChart` + `cost_to_finish` already render this shape.

**Verify.** Service test: a feature with one member PR but a repo with 20 merged PRs produces a
real multi-point envelope (not min==median==max); a repo under the floor falls back to
feature-local. Live (after T0): confirm non-degenerate envelope on FeatureScreen.

**Size:** S–M.

- [x] `_repo_cost_envelope` (`service.py`) fetches the repo's trailing merged PRs
  (`repo_merged_prs_in_range`) + costs (`pr_costs_for_ids`) → `cost_per_pr_envelope`; wired into
  `feature_detail` with feature-local fallback.
- [x] New `ForecastConfig` (`envelope_trailing_days=90`, `envelope_min_prs=5`) on `ApiConfig`;
  below-floor falls back to feature-local (one-cost-source preserved — same `pr_costs_for_ids` path).
- [x] Integration test proves the widening (a 1-open-PR feature estimates at the repo median $40,
  not its own $10 member); 5 feature_detail tests green. No FE change needed
  (`CostEnvelopeChart`/`cost_to_finish` already render this shape).

---

## T3 — D3-benchmark (this feature's cost/time percentile vs peer features)

**Problem.** `"benchmark": None` hardcoded
([manager/service.py:1011](src/server/api/manager/service.py:1011)); schema pins
`benchmark: None = None` ([schemas/manager.py:722](src/server/api/schemas/manager.py:722)); FE
renders an `UnavailableNote` ([FeatureScreen.tsx:161-166](src/webui/src/screens/FeatureScreen.tsx:161)).

**Approach.** Population is ready-made: `quality.list_features_for_repo`
([quality.py:116](src/server/api/manager/quality.py:116)) enumerates every feature in the repo,
each `_feature_view` already carrying `spend.usd_micros` + `merged_prs`. Build a
`FeatureBenchmark` = { this feature's cost + lead-time, the repo feature-cost/time distribution
(`forecast.dist_stats`), and this feature's percentile within it (`forecast.percentile` on the
inverse) }. Replace the `benchmark: None` pin with the new schema.

Two axes (mockup says "vs similar features", cost **and** time):
- **cost:** `spend.usd_micros` per feature — available now.
- **time:** lead time = `last_merged_at − first_session_started_at`. Both timestamps already
  computed in `feature_detail`'s `lifecycle` block; extend `_feature_view` to return them
  per-feature, or compute lead time inside the benchmark aggregate over the enumerated features.

**Decisions (defaults recommended):**
- **"Similar" =** all features in the same repo, first cut (no similarity model). A size/label/
  issue-type cohort is a later refinement and must not gate v1.
- **Ship cost + lead-time together** (both are cheap once the population is enumerated).
- **Exclude the subject feature** from its own comparison distribution.

**Files:**
- `src/server/api/manager/aggregate.py` — new `feature_benchmark(features, subject)` over
  `list_features_for_repo`'s output (cost + lead-time dists + subject percentiles).
- `src/server/api/manager/service.py::feature_detail` — call it; populate `benchmark`.
- `src/server/api/schemas/manager.py` — `FeatureBenchmark` schema; change `FeatureDetail.benchmark`
  from `None` to `FeatureBenchmark | None`.
- `src/webui/src/data/api-types.ts` — regen from OpenAPI (`utils/gen_api_types.sh`).
- `src/webui/src/screens/FeatureScreen.tsx` — render via `PercentileBar` (cost `$`, time `d`);
  keep `UnavailableNote` as the cold-start branch (repo with too few peer features).

**Verify.** Aggregate test: subject feature at a known rank within a synthetic repo yields the
expected percentile; single-feature repo → `None` (cold-start, honest). OpenAPI regen +
typecheck + lexicon + vitest. Live (after T0): benchmark bars render on FeatureScreen.

**Size:** M.

- [x] `aggregate.feature_benchmark(features, subject)` over `list_features_for_repo`'s output —
  cost percentile + peer cost `Dist` (cost-only v1; lead-time is `TODO(D3-benchmark-time)`,
  needs per-peer lifecycle timestamps `list_features_for_repo` doesn't compute). `None` below
  3 priced peers. 4 pure unit tests.
- [x] `FeatureBenchmark` schema (+ moved `Dist` above `FeatureDetail`); `FeatureDetail.benchmark`
  unpinned from `None` to `FeatureBenchmark | None`; regenerated `openapi.json` + `api-types.ts`.
- [x] Wired `PercentileBar` + subject-position caption on FeatureScreen; cold-start
  `UnavailableNote` kept; mock given a sample benchmark.
- [x] Integration test (40th-percentile subject over 5 peers); OpenAPI/typecheck/lexicon/vitest
  (25)/build green; **browser-verified** ("more than 62% of 8 other features", PercentileBar
  renders, no console errors). Full API suite: **384 passed**.
- [ ] Live (real-stack) verification is gated on T0's re-correlation (costs are $0 until
  `session_git_links` is populated) — proven on seeded data instead.

---

## T4 — D1-lifecycle (session lifecycle spans from links + PR events)

**Problem.** `_session_economics_block` returns `"lifecycle": None`
([single_user/service.py:881](src/server/api/single_user/service.py:881)); SessionScreen's
timeline shows only session-start→last-activity ([SessionScreen.tsx:142-146](src/webui/src/screens/SessionScreen.tsx:142)).
Real build/review/rework/ship spans need the PR side.

**Approach.** Join the session to its PR(s) via `session_git_links` (T0 populates it), then
assemble a milestone series from data that now exists:
- session `started_at` (build start),
- linked PR `created_at` (`pr_basic_facts`) — PR opened,
- first review `submitted_at` (`pr_review_children` [manager.yaml:100](config/queries/manager.yaml:100)) — review start,
- `merged_at` / `closed_at` — ship,
- reopen loops from `git_artifact_events` ([storage/schema/git.py:128](src/server/storage/schema/git.py:128)).

Emit raw timestamps only (schemas/README strip-rule; matches `FeatureLifecycle`'s posture).
The FE already has the layout + component: `present/lifecycle.ts::positionLifecycle`
([lifecycle.ts](src/webui/src/present/lifecycle.ts)) + `<LifecycleTimeline>`
([ui.tsx:259](src/webui/src/components/ui.tsx:259)) with `spanKind: build|review|rework` — the
same helper FeatureScreen already feeds.

**Hard dependencies (both from other tracks):**
- **T0** — without `session_git_links`, there is no PR to hang spans on.
- **`git_artifact_events`** — currently uncommitted (working tree) + unmigrated on the stack.
  Reopen/rework spans stay honestly absent until it ships and backfills. Time-to-first-review
  is also thin on the stack (2 review rows) → the review span will often be `None` (correct, not
  broken).

**Files:**
- `config/queries/pr_class.yaml` — reuse `links_for_session` to get a session's PR(s).
- `src/server/api/single_user/service.py::_session_economics_block` — join the session's PR(s),
  fetch PR `created_at`/`merged_at`/`closed_at` + first-review time + reopen events; assemble the
  milestone series; promote into `lifecycle`; remove the two `# TODO(D1-lifecycle)` markers.
- `src/webui/src/screens/SessionScreen.tsx` — type `lifecycle` in `SessionEconomics`, feed
  `positionLifecycle` with `spanKind`-tagged milestones, render via `<LifecycleTimeline>`
  (keep the current start→last-activity fallback when no PR is linked).

**Decisions.** Span taxonomy — recommend **build** (session start → PR opened), **review**
(PR opened → merged, or first review → merged), **rework** (each reopen loop). Confirm before
building whether "rework" is per reopen event or an aggregate.

**Verify.** Service test: a session linked to a merged PR with one review + one reopen yields
build/review/rework milestones in order; an unlinked session falls back to session-only. Live
(after T0 + `git_artifact_events` migrated): timeline renders real spans on SessionScreen.

**Size:** M.

- [x] `_session_lifecycle` (`single_user/service.py`) joins the session's PR(s) via
  `links_for_session` + `pr_basic_facts` + `pr_review_children`; threaded `s`/`catalog`/`session_id`
  into `_session_economics_block`. Returns raw timestamps (strip-rule), `None` when unlinked.
- [x] Milestones: session start → PR opened (build) → first review (review) → reopened (rework,
  one per `git_artifact_events` reopen transition) → merged/ship; removed the `TODO(D1-lifecycle)`
  marker. Rework spans added via new `pr_reopen_events` query reading the PR state-transition log
  (2026-09-14, once churn/reopen's `git_artifact_events` landed); empty list when never reopened.
- [x] Wired on SessionScreen: uses the linked-PR milestones when present, session-only fallback
  kept; mock given a sample lifecycle. FE typecheck/lexicon/vitest(25)/build green.
- [x] Service test (`test_economics_lifecycle_from_the_sessions_linked_pr`, 32 session_detail
  tests green); **browser-verified** (timeline shows session start → PR opened → first review →
  merged with Build/Review spans, no console errors).
- [ ] Real-stack verification gated on T0's re-correlation (`session_git_links` empty until then).

---

## Cross-track notes

- **Backfill discipline.** T1 backfills from `session_graph` (no re-ingest). T2/T3/T4 need no
  fold backfill — they read live git/link tables at request time — but need T0's re-correlation
  run to have populated `session_git_links` first.
- **Cold-start honesty (every track).** Each new block returns `None` (cold-start), never a
  fabricated zero, when its inputs are absent — same absent-vs-empty discipline as the rest of
  these endpoints. The FE cold-start branches (`UnavailableNote`) stay as the empty state.
- **One cost source.** T2/T3 read cost only through the existing `session_git_links → session_info`
  path (via `pr_costs_for_ids` / feature spend); no second cost path is introduced.
- **Tests to keep green.** Backend suite (354+), `npm run typecheck` / `lexicon` / `vitest` /
  `build` for every FE touch, and the A/B differential allowlist for the new fold field (T1).

## Decisions to confirm before coding (consolidated)
1. **T0:** none blocking — it's a query-path bug fix. (Only open call: whether to also flatten the
   sha in `_make_tool_result` now as hardening, or defer it. Recommend defer.)
2. **T2:** envelope trailing window (recommend 90d) + volume floor (recommend N=5) + fallback labeling.
3. **T3:** "similar" = repo-wide first cut (recommend); cost + lead-time from v1 (recommend).
4. **T4:** span taxonomy (build/review/rework/ship) and whether rework is per-reopen or aggregate.
5. **Ownership:** does `git_artifact_events` ship (commit + migrate) as part of T4, or separately?
