# MVP Implementation — Progress Tracker

**Branch:** `feat/mvp-implementation-#52` · **Plan:** `docs/plan/2026-09-09-mvp-backend-implementation.md`
**Started:** 2026-09-10 · **State:** Phase 2 COMPLETE (F4/X1/X2 + A6 `features[]`/A5
`work_quality` + U6). Phase 3 backend (X4/X5/D1/D2/D3/E1) is now COMPLETE; U7/U8 (front-end) next.

Living checklist for the end-to-end build. Update as tracks land; each box ticks only when its
plan **Verify** passes and module tests are green (CLAUDE.md §4).

**Green as of Phase 2 close:** full `tests/server` 770 passed (minus the pre-existing
`test_otel_e2e.py` failure noted below) · `src/webui` vitest 22 · `npm run typecheck` +
`vite build` · `utils/gen_api_types.sh --check` · Projects/Team-Person screens verified live in the
browser (mock mode).

---

## Carry-over decisions (demo-backend cleanup)

Confirmed with the owner 2026-09-10:

| Artifact | Decision |
| :-- | :-- |
| `src/server/api/static/` vanilla SPA + StaticFiles mount | **Retire now** — deleted in Phase 0; single-user screens re-land in `src/webui/` |
| `src/server/api/` layout | **Restructure** — split shared / `single_user/` / `manager/` / `schemas/` before adding endpoints |
| Shipped single-user API (`service.py`) | Kept, moved under `single_user/`; ground rules §2 stay binding |
| `docs/design/mockups/figma/` React app | Relocated to `src/webui/` (U0) |
| `figma/src/data/mock.ts` fakes (`genEnvelope`/`genBurn`/`econFor`, colours in data, pre-formatted strings, `screen`/`screenParam`) | **Stripped** (§1); `mock.ts` demoted to a typed fixture |
| `figma/src/components/AskBox.tsx` + ask wiring | **Out of scope** (Track K) — slot kept, unwired (U4) |
| `figma/dist/` committed build output | **Dropped**; gitignored |
| `docs/plan/2026-09-03-mvp-ux-implementation.md` | Superseded (Tracks A/F/U); Ask track K still stands — banner added |
| `docs/plan/2026-09-08-dashboard-iteration-plan.md` | Folded into the 09-09 plan — banner added |
| `docs/plan/2026-08-06-dashboard-ui-implementation.md` | Superseded by `src/webui/` — banner added |
| Untracked working-tree files (`docs/ProductSpec/*`, `utils/*probe*`, `2026-07-*` plans) | Not demo-backend; left untouched |

**Already merged to `main` (Phase 0 prerequisites, verify only):** Track G1·G2 (`src/server/directory/`),
Track H1·H2 (`src/server/ingest/git/` issue ingestion + linkage).

---

**Pre-existing failure (not this branch):** `tests/server/test_otel_e2e.py::test_both_signals_join_into_one_cost_block`
fails on pristine `main` (61fcd1e) — trueup classifies a `both` session as `otel_only`. Out of
scope here; tracked separately.

## Phase 0 — prerequisites + scaffolding

- [x] Retire vanilla SPA (`src/server/api/static/`, mount, `test_app.py` static test) — `b397b36`
- [x] Restructure `src/server/api/` (shared / `single_user/` / `manager/` / `schemas/`) — `b397b36`
- [x] **F0** contract scaffolding — `schemas/envelope.py` (`Envelope[T]`/`Money`/`Sourced`/`Unavailable`/mode blocks) + `schemas/README.md` strip-rules; `cli_export_openapi.py` + committed `openapi.json` + `utils/export_openapi.sh --check` drift guard; `/v1/pricing/models` typed as the reference port. TS `gen:types` lands with U1 (needs `src/webui/`). Tests: `test_schemas_contract.py`, `test_openapi_contract.py`
- [x] Verify G1·G2 (directory) tests green — `tests/server/directory` 14 files, green in the 400-pass baseline
- [x] Verify H1·H2 (issue ingestion) tests green — `tests/server/ingest` green in the baseline
- [x] **U0** app relocation `docs/design/mockups/figma/` → `src/webui/`; `vite build` → `dist/` served by FastAPI `StaticFiles` after `/v1/*` (guarded on `config.webui_dist`, skipped if absent); proxy → `:8090`; `utils/webui_build.sh`. `npm run build`/`typecheck` green; api tests 158 pass
- [x] **U1** data seam — `src/webui/src/data/source.ts` (`DataSource` iface, `ApiSource`, `useData` hook, `Envelope<T>`/`Timeframe`/`Scope` types) + `mock-source.ts` (`MockSource` over the fixtures) wired in `main.tsx` by `VITE_DATA_SOURCE`. Generated `api-types.ts` from `openapi.json` via `utils/gen_api_types.sh` (+ `--check` drift guard, `npm run gen:types`). `mock.ts` banner = demoted to fixture; per-screen shape rewrites ride with U5–U7 (screens not migrated off `mock.ts` yet — that's the A1/Overview slice). typecheck + build green.
- [x] **U2** presentation layer — `src/webui/src/present/`: `format.ts` (duration/relative-time/days/money-micros/tokens/pct), `palette.ts` (`categoryHue(kind,label)` — quality/channel/sessionType/agent/habit/fit/severity maps, BLUE §7), `routing.ts` (`EntityRef` → `entityRoute`/`findingRoute`), `lexicon.ts` (`scanLexicon`/`assertLexicon` + locked quality vocab). `ui.tsx` re-exports the canonical formatters. vitest added; `present.test.ts` 11 tests green.
- [x] **U3** five-state `<Block>` wrapper — `src/webui/src/components/Block.tsx`: pure `resolveBlockState(DataState<Envelope<T>>)` → loading / error / unavailable / cold-start / empty / ready, plus the `<Block>` component (height-reserving `Skeleton`, `SourceNote` for the mode state, inline retry). cold-start ≠ unavailable asserted distinctly. `Block.test.ts` 7 tests green (18 total).
- [x] **U4** global shell — `src/webui/src/shell/`: `timeframe.tsx` (`TimeframeProvider`/`useTimeframe`, app-level `period` 15d default → `timeframeForPeriod` `{from,to}`, `<TimeframeStrip>`), `viewer.tsx` (`ViewerProvider`/`useViewer`, `canSeeManagerNav` — `dev` sees only My-usage). `App.tsx` rewritten: providers + role-gated `<Sidebar showManagerNav>` + `navigateRef(EntityRef)` via `entityRoute`; `dev` blocked from manager roots, initial screen `my-usage`. `shell.test.ts` 4 tests (22 total). Scope selector stays screen-local (Overview band + Economics) — wired in U5/U7.

## Phase 1 — aggregation screens (not blocked on H)

- [x] **F1** budgets + allowances — `schema/budgets.py` (`Budget`/`Allowance`, migration `8e59d8946ba4`), `config/queries/manager.yaml` (new file — cross-user queries live here, `dashboard.yaml` stays single-user), `api/manager/budgets.py` (get/put/list, scope existence validated against `teams`/`session_info`+`git_artifacts`), `cli_budgets.py` + `utils/budgets_import.sh` (dry-run default). `GET/PUT /v1/budgets/{scope_type}/{scope_id}`, `schemas/manager.py::BudgetResponse`. Migration up/down test green.
- [x] **F2** viewer identity + role gate — `api/gate.py` wraps `directory.resolve_viewer` (Track G already produced the `{user_id,roles,visible_teams,visible_repos}` shape); `Viewer.is_manager`, `require_manager`/`require_team`/`require_repo`; `UnauthorizedError`(401)/`ForbiddenError`(403) added to `errors.py`. No `Depends()` (matches the codebase's existing no-FastAPI-dependency convention) — `app.py`'s `_gate_scope` helper is a plain call. Wired onto the budgets routes now; every Track A route reuses it.
- [x] **F3** repo-rollup helper — `api/manager/aggregate.py::repo_rollup_canonical`: `path`-provenance repo_ids (already `local:<key>`) bucketed per-user, `pr_link`/`remote` merge cross-user; sessions counted root-only, cost/tokens summed over every row. Segment-level split deferred (TODO, no Phase-1 screen needs it).
- [x] **F5** reasoning rollup — `aggregate.reasoning_share`: forward-compatible read of `info.reasoning_tokens`; **worker gap found and documented** (TODO(F5-worker-gap) — the causal graph estimates reasoning per-node, `analysis/causal_graph/reasoning.py`, but nothing rolls it up to `session_info.info` yet), returns `None` (cold-start) until that lands.
- [x] **X3** recoverable-spend waterfall — `aggregate.recoverable`: prices only what already carries a dollar figure (ground rule 3) — `channel_cost.output` (floor), `carry.carry_usd_micros`+`channel_cost.recache` (context), segments with a `revision_loop` beat (rework). Resume overhead / read-then-discard / retry burn / model-oversizing surfaced as raw token estimates (TODO(X3-phase2), documented in-code — those EFF §2 buckets need a worker-side pricing pass or X4). `habits_from_recoverable` reconciles by construction. `aggregate.py` also carries `blended_cost_per_pr_usd_micros`, `rework_rate`, `budget_pct`, `spend_by_agent` for Track A. Tests: `test_manager_aggregate.py` 18 green.
- [ ] X3 recoverable-spend waterfall
- [x] **A1** `/v1/org/overview` — `manager/service.py::org_overview` + `schemas/manager.py` (`OrgOverview`, `TeamSummary`, `Finding`/`EntityRef`, …). Per-team breakdown off one org-wide fetch (`user_primary_teams` + `group_rows_by_team`); `vs_last_pct` via `previous_window`; `initiatives_trend` proxies issue created/closed weekly (TODO(A1-initiatives): true feature-level start awaits F4); `worth_a_look` = over-budget + best-$/PR findings with typed entity refs. F2-gated (lead/admin). Tests: service (4) + HTTP (5) green.
- [x] **A2** `/v1/org/usage` — `manager/service.py::org_usage` + `schemas/manager.py::OrgUsage`. `usage_status` bands (over/healthy/under — data values only, no "limit exceeded" wording per iteration-plan D), `seats_under_threshold` (even-split team allowance per seat — TODO(A2-per-seat-allowance) until a real per-user allowance exists), `session_type_counts`, `sessions_with_no_output_pct`, `reasoning_share` (F5). Tests: aggregate unit (6) + service (3) + HTTP (2) green.
- [x] **A3** `/v1/teams` + `/v1/projects` — `manager/service.py::teams_table`/`projects_table` + `schemas/manager.py::TeamTableRow`/`ProjectTableRow`. Server-side sort (`aggregate.sort_desc_none_last`, dotted-path into `Money.usd_micros`)/search/paginate; unknown sort key → 400. Projects = one row per canonical repo (F3). Tests: aggregate unit (5) + service (6, incl. sort/paginate/search + field checks) + HTTP (3) green.
- [x] **A4** `/v1/teams/{id}` — `manager/service.py::team_detail`; ranked `Person[]` via `_person_ranked` (X3 recoverable → `status` on-track/room-to-save, `ROOM_TO_SAVE_USD_MICROS` threshold; usage_pct vs even-split allowance → `low_use`); `worth_a_look` names room-to-save people. F2 `require_team` (lead sees only their own team). Tests: aggregate (2) + service (3, plan's literal Verify) + HTTP (2) green.
- [x] **A5** `/v1/people/{id}` — `manager/service.py::person_detail`; X3 decomposition (`habits[]` reconciling to `recoverable`), `recent_prs`, `velocity` (weekly merged PRs), `peer_compare` (cheapest teammate by $/PR — TODO(A5-peer-task-mix): a real task-mix match awaits X2). `work_quality: None` (cold-start, contract shaped now for X2 Phase 2). F2 `require_team` on the person's own team; unknown person → 404. Tests: aggregate (2) + service (3, plan's literal Verify) + HTTP (3) green.
- [x] **A6** `/v1/projects/{id}` (no `features[]` yet) — `manager/service.py::project_detail`: one canonical repo, ranked contributors, highlights (lines added/removed; `reopened_prs: None`, TODO(A6-reopens) — no history counter captured today). `features: None` (cold-start; contract shaped for F4/Phase 2 even though Track H's issue data already exists — the boundary-key logic isn't built yet). **Bug caught + fixed while wiring this:** `/v1/projects/{id}` and `/v1/budgets/{scope_type}/{scope_id}` needed `{id:path}`/`{scope_id:path}` — a repo_id (`github:owner/name`) contains a literal `/` that FastAPI's default path converter splits on. F2 `require_repo`. Tests: service (2) + HTTP (2, incl. the slash-in-id case) green.
- [x] **A7** MyUsage + `myTips` — reuses the shipped `/v1/users/{id}/*` unchanged (ground rules §2); the one new piece is `GET /v1/users/{id}/tips` (`manager/service.py::my_tips`), X3's habits phrased as tips through the tone lexicon, as ungated as the routes beside it. Tests: service (3, incl. lexicon scan) + HTTP (2) green.
- [x] **U5** Phase-1 screens wired live — Overview/Usage/Teams/Projects/Team/Person/MyUsage(`myTips`) all bind via `useData`+`<Block>` to their generated `Envelope_*_` schema (`data/source.ts` retyped off `api-types.ts`; `mock-source.ts` fixtures rebuilt to the real shapes, not the U1 stubs). `ui.tsx::WorthALook` now routes off typed entity refs (`entityRoute`, U2) instead of `screen`/`screenParam`. Sections with no backend yet this phase (Overview's forecast, Person's `work_quality`) render through the new `<UnavailableNote>`, not silently dropped. Verified in the browser end-to-end in mock mode incl. Overview→Team→Person routing via worth-a-look. `npm run typecheck`/`build`/vitest (22) green.

## Phase 2 — output & quality

- [x] **F4** feature boundary key — `api/manager/quality.py::list_features_for_repo`, reusing Track H's already-built `ingest.git.features.feature_of`/`single_pr_feature` (H5) + new enumeration queries (`repo_feature_numbers`, `repo_prs_without_issue_link`). Issue-with-no-PR is still a feature (`not_started`→`in-progress`, collapsed to the UI's 2-value vocabulary); orphan PRs fall back to single-PR features.
- [x] **X1** acceptance-cost enrichment (achievable slice) — `quality.pr_acceptance_metrics`: review_rounds (changes_requested count), time_to_first_review, time_to_merge, comment_count — all query-time joins over already-persisted `git_artifacts`, no new worker pass needed. **TODO(X1-churn)/TODO(X1-reopen)** documented in-code: post-open push churn and reopen count aren't computable from current capture (commit artifacts from push webhooks carry empty `stats`; no state-transition history is kept) — X2's acceptance axis uses review_rounds alone, not the full churn+rounds+reopen composite EFF describes.
- [x] **X2** quality 2×2 — `quality.quality_label`/`quality_counts_for_prs`: making-cost (X3 recoverable_pct over a PR's linked sessions) × acceptance-cost (review_rounds) → Clean/Heavy lift/Quick & dirty/Costly rework (EFF §4.1 matrix, thresholds `TODO(X2-config)`).
- [x] **A6** `features[]` — now populated via F4 (previously `None`/cold-start).
- [x] **A5** `work_quality` — now populated via X2 (previously `None`/cold-start); empty list is the honest "classified, nothing landed" answer once there's ≥1 merged PR.
- **Found + fixed a pre-existing Phase 1 bug while wiring F4:** `session_info.repo_id` (git_ident scheme, `github:owner/name`) and `git_artifacts.repo_id` (git-source scheme, `github:<repo_num>`) are two unreconciled identity namespaces (documented as deferred in `GitRepo`'s own docstring). A3's `merged_prs`/A6's PR data were silently reading empty against any real dataset — passed only because prior tests used the same fake string on both sides. Fixed with `aggregate.resolve_git_repo_id(s)`/`resolve_git_repo_ids` (bridges via `git_repos.full_name`) wired into `projects_table`, `project_detail`, and F4; regression tests added (`test_manager_repo_identity_bridge.py`) plus fixture fixes in the existing A3/A6 tests. `TODO(repo-identity-unification)` marks the real long-term fix.
- [x] **U6** feature grouping + quality UI — `ProjectScreen.tsx` now binds to A6 (`useData`+`<Block>`), rendering F4's `features[]` and contributors live, `<UnavailableNote>` when no GitHub App is connected. `PersonScreen.tsx` needed **zero changes** — U5 already wrote the `work_quality ? … : <UnavailableNote>` branch, so X2 output appeared the moment A5 stopped returning `null`. `mock-source.ts::project()` updated to return real `features[]` (parity with the now-live API, replacing U5's `features: null` placeholder). Verified live in the browser (Projects → api-gateway shows F4 features; Team → Tom Wright shows X2 quality breakdown).

## Phase 3 — deep-dives & forecasts

- [x] **X4** model-fit verdict — `aggregate.model_tier`/`dominant_model`/`segment_fit`/`model_fit`:
  tier inferred from the model-name family substring (`opus`→high, `haiku`→low, else mid —
  `common/pricing.py` has no tier concept yet, `TODO(X4-config)`); per-segment `fit` compares the
  session's one dominant tier against that segment's own `label` (task class) and whether it carries
  a `revision_loop` beat — `over` (high tier on a light COMPREHEND/PLAN/REVIEW/MISC segment with no
  rework) / `under` (low tier that drove a rework loop) / `good`. Session-level `modelFit` picks
  `under` over `over` when both could appear (currently can't, both need the same session-wide tier
  — `TODO(X4-per-segment-model)` — defensive for when per-segment model attribution lands); `note` is
  a deterministic template (no LLM), `savings_usd_micros` prices only the `over` case at a fixed
  `_TIER_DOWNGRADE_SAVINGS_RATIO` placeholder. Found+fixed while researching this: `_segment_cost_by_beat`
  (X3) assumed `beats` was a list of records; the real shape (`worker/segments/beats.py::build_segments`)
  is `{kind: count}` — fixed via a shared `segment_has_beat` helper, regression test added, fixture
  corrected. Golden-session verify cases (`s1` over, `s2` under) reproduce their verdict + savings.
  Tests: `test_manager_aggregate.py` 47 green (was 35).
- [x] **X5** forecasts — new `api/forecast.py` (sits at `api/`, not `api/manager/`: shared by
  E1 and D3, and D3 is reachable from either surface), framework-free, no RNG:
  `cost_per_pr_envelope` (median/p25/p75/p90/p99 via a hand-rolled `percentile`, numpy's
  "linear" method, over a scope's trailing merged-PR costs — `None` cold-start with none);
  `forecast_cone` (fixed-rate widening band off the envelope, `_CONE_WIDEN_RATE`
  `TODO(X5-config)`, step 0 == today's own band so a chart's actual/forecast lines are
  continuous at the handoff by construction); `budget_burnup` (cumulative actual-to-date +
  flat-rate projection to period end, handoff day carries both fields equal —
  `TODO(X5-trend)` notes mock.ts's growth-fudge factor was deliberately dropped as
  unprincipled); `feature_cost_to_finish` (`openPRs × envelope bounds` → low/mid/high +
  `basis` string; 0 open PRs is a real zero, not cold-start — only a missing envelope is).
  Tests: `test_forecast.py` 14 green, all four plan Verify lines reproduced directly.
- [x] **D1** `/v1/sessions/{id}?view=economics` — same route, no new endpoint (`session_detail`
  gets a `view: str | None` param; unknown view → 400; base payload unchanged without it).
  `single_user/service.py::_session_economics_block` reuses `_cost_by_channel_block` (verbatim,
  as `channels`) and `_segments_block`'s `_SEGMENT_FIELDS` allowlist, and pulls in X4's
  `manager.aggregate.model_fit` (the one place single_user imports from manager — documented as
  intentional, one-directional). `model_fit`/`segments[].fit` are real; `lifecycle` (needs
  session+PR timestamps, D2's join) and `context_curve` (needs a persisted per-turn series,
  never folded today) stay cold-start `None` — `TODO(D1-lifecycle)`/`TODO(D1-context-curve)`.
  Golden sessions `s1`/`s2` (mock.ts's own `sessionDetail`/`sessionReworkDetail`) reproduce
  their `over`/`under` verdicts end to end through the route. `openapi.json`/`api-types.ts`
  regenerated (new `view` query param). Tests: service (7) + HTTP (3) green.
- [x] **D2** `/v1/prs/{id}` — a genuinely new route (no single-user PR endpoint to extend, unlike
  D1). `manager/service.py::pr_detail` composes X1's `pr_acceptance_metrics`, X2's
  `quality_label`/`pr_making_cost_pct`, and two new D2 aggregate helpers: `channel_totals`
  (channel $ summed across a PR's `session_git_links`-joined sessions, with a `complete` flag
  rather than silently passing a partial total as whole) and `pre_post_open_cost_split`
  (session spend bucketed at the PR's own `created_at`). **Found + fixed a second instance of
  the repo-identity gap** (Phase 2's own bug class) while wiring the route's gate: `gate.
  require_repo` needs the session-side `repo_id`, but a PR's own `repo_id` is git-source's
  numeric scheme — added `aggregate.resolve_session_repo_id` (the reverse of A6/F4's
  `resolve_git_repo_id`, same `git_repos.full_name` bridge) and `manager_service.
  resolve_pr_visibility_repo_id` for the route to gate with before calling `pr_detail`.
  `rework_ratio`/`churn_post_usd_micros`/`reopens` stay cold-start `None`
  (`TODO(X1-churn)`/`TODO(X1-reopen)`, already documented in `quality.py`); `flags[]` only
  names signals this pass can actually compute (review-round count, the quality quadrant),
  not the mock's churn-based flag. New schemas: `PRSummary`/`PRCost`/`ChannelTotals`/
  `PRReviewSummary`/`PRReview`/`PRSessionRef`/`PRFlag`/`PRDetail` (`schemas/manager.py`).
  `pr_basic_facts`/`pr_review_children` queries widened for D2's extra columns (reviewer
  identity, PR title/branch/labels/etc.); new `full_name_for_git_repo_id` query.
  `openapi.json`/`api-types.ts` regenerated. Tests: aggregate (+5) + service (5) + HTTP gate
  (4) green, 325 total in `tests/server/api`.
- [x] **D3** `/v1/features/{id}` — **found + fixed a real gap while building this**: A6's
  `Feature.issue_key` (`"#214"`) is a display label, not unique across repos, and A6 never sent
  any other id — `EntityRef.id` (U2's typed routing) had nothing routable to carry for a
  `feature` kind. Added `quality._feature_id`/`feature_by_id` (a symmetric pair, round-trip
  tested): issue-keyed features route on the issue's own `artifact_id`
  (`github:<repo_num>:issue:<n>`), single-PR fallback features on a synthetic `pr:<pr_artifact_id>`
  (`ref.artifact_id` is `None` there by construction); re-derived fresh per request rather than
  stored, so a stale/bogus id 404s instead of resolving to a mismatched feature — including a
  caller-supplied issue number with zero evidence (no pulled issue, no linked PR), which
  `feature_of` would otherwise happily fabricate into an empty feature. `Feature` schema (A6)
  gained the `id` field; existing A6 fixtures updated.
  `manager/service.py::feature_detail` composes member `prs[]` + a new one-query
  `pr_costs_for_ids` (D3/X5), `contributors[]`, raw-timestamp `lifecycle` (no presentation
  spans, D1/D2's own posture), reuses D2's `channel_totals`, and X5's `cost_per_pr_envelope`/
  `feature_cost_to_finish` for `cost_to_finish` — scoped to the feature's own member-PR cost
  history (`TODO(D3-envelope-scope)`: a repo-wide trailing distribution would be sturdier once
  a repo has enough merged PRs). `benchmark` stays cold-start `None` (`TODO(D3-benchmark)`,
  needs a cross-feature distribution not built yet). Gate mirrors D2's
  `resolve_*_visibility_repo_id` two-lookup shape. New schemas:
  `FeatureMemberPR`/`FeatureContributor`/`FeatureLifecycle`/`FeatureCostToFinish`/
  `FeatureDetail`. `openapi.json`/`api-types.ts` regenerated. Tests: quality (+7) + service (5)
  + HTTP gate (3) green, 338 total in `tests/server/api`.
- [x] **E1** `GET /v1/economics?scope_type=&scope_id=` — two-part scope (never a composite
  `scope=` string), matching F1's budgets routes exactly; the route reuses `app.py`'s existing
  `_gate_scope` helper unchanged. org/team walk the usual `directory.users_in_scope` ->
  `fetch_scope_sessions`/`fetch_scope_merged_prs`; `repo` scope walks
  `repo_sessions_in_range`/`repo_merged_prs_in_range` instead (A6's own precedent) — a
  contributor's *other* unrelated repos must never leak into a project's own economics
  (regression-tested directly). New `forecast.dist_stats` (avg/min/p50/p70/p95/max, the
  mockup's own `Dist` shape — a coarser cut than X5's envelope percentiles) backs
  `cost_dist`/`time_to_merge_dist`; `pr_cost_envelope`+`forecast_cone` and `budget_burn` reuse
  X5 unchanged; `reverted_pr_cost`/`rework_ratio` reuse `pr_classification.is_revert`/
  `rework_ratio` (already joined into `scope_merged_prs_in_range`/`repo_merged_prs_in_range` —
  no new worker pass). **Found + fixed a real bug while testing this**: the daily-bucketing
  code always built a full-length all-zero series for `budget_burnup`, so an empty scope
  reported a flat $0 burn-up instead of cold-starting `budget_burn` like every other absent
  block here — fixed to build the series only when the scope has ≥1 session at all.
  `TODO(E1-budget-period)`: `budget_burn` projects to the requested window's own end, not
  `config.budget_period`'s actual current period. New schemas:
  `Dist`/`CostEnvelope`/`ForecastConePoint`/`BudgetBurnPoint`/`Economics`.
  `openapi.json`/`api-types.ts` regenerated. Tests: forecast (+2) + service (5) + HTTP gate
  (4) green, 349 total in `tests/server/api`. Verify reproduced directly: org scope's spend
  reconciles with A1's headline; a repo-scope test locks in the no-leak boundary.
- [x] **U7** deep-dives + Economics wired — `SessionScreen`/`PRScreen`/`FeatureScreen`/
  `EconomicsScreen` rewritten off `mock.ts` onto `useData`+`<Block>` (PR/Feature/Economics,
  fully F0-typed) and a hand-typed `sessionDetail` dict (D1's route stays untyped per
  `schemas/README.md`'s own porting table — only `economics` is worth hand-typing here,
  mirroring `myOverview`'s precedent). New `present/lifecycle.ts::positionLifecycle` (x-position
  + span-label layout from raw timestamps, shared by all three deep-dives — tested) and
  `present/palette.ts::channelTotalsToSeries` (the raw `{carry,recache,output,input}` channel
  split → a chart-ready, colour-mapped series). Dropped mockup-only presentation fields the
  real API doesn't (and per `schemas/README.md`'s strip-rules, shouldn't) send: per-model
  tier list, colours, pre-baked spans/x-positions, the PR/feature churn-based flag and
  reopen count (`TODO(X1-churn)`/`TODO(X1-reopen)`, honestly `null` in both mock and api now).
  **Found + fixed while wiring:** (1) `ProjectScreen`/`App.tsx`'s feature routing used
  `issue_key` (`"#214"`, not unique) instead of D3's new `id` — the routing bug the `id` field
  was added to fix, now actually wired through; (2) `mock-source.ts::project()`'s `features[]`
  was still missing `id` after the D3 schema change; (3) `ApiSource` never sent the F2 trusted
  `x-talium-viewer` header at all — every manager-gated screen (Team/Person/Project from
  Phase 1, not just the new ones) was 401-only against a live backend; added `VITE_VIEWER_ID`
  (dev-only, `vite-env.d.ts`); (4) a duplicate-React-key warning in PRScreen's session list
  (a session can legitimately appear twice, once per role); (5) `PercentileBar`'s `$`/`d`
  formatting had no rounding, fine for hand-rounded mock fixtures but showing 16-digit floats
  against real backend data. Verified two ways: mock-mode click-through of all four screens
  (screenshots), and a live-API pass — seeded a scratch Postgres DB, ran the real FastAPI app,
  set `VITE_DATA_SOURCE=api`, and confirmed Session/PR/Feature/Economics all render real
  computed values end to end (this pass is also what caught the E1 unit/Decimal bug in the
  commit just before this one, and confirms the review-state-casing fix renders correctly).
  `npm run typecheck`/`vitest`/`build` all green.
- [x] **U8** responsive / a11y / lexicon CI / e2e — **found a real, pre-existing gap while
  checking mobile**: `Sidebar` never collapsed below any breakpoint at all — a mobile
  viewport lost ~200px of a ~375px screen to a fixed-width rail, and every hero-tile grid
  overflowed under it. Fixed: `App.tsx` now renders the persistent rail only at `md`+ and an
  off-canvas drawer (backdrop + Escape-to-close) below it, toggled by a hamburger header
  shown only under `md`; screen padding switched from a fixed inline style to
  `p-4 sm:p-9 md:p-12`. Also fixed a second real bug this surfaced: `LifecycleTimeline`
  (Session/PR/Feature's shared timeline) had no collision handling for two milestones close
  together in time — real data clusters far tighter than the mockup's evenly-spaced examples,
  so labels overlapped illegibly; now stagger onto a second row past a threshold gap.
  **Lexicon CI gate**: new `src/webui/scripts/lexicon_check.mjs` (`npm run lexicon`) walks
  every front-end source file (comments stripped, so a file's own prose *about* the rule
  doesn't trip its own gate), scanning code/JSX text for `present/lexicon.ts::BANNED` verbatim
  — verified it both passes clean and fails on a planted violation (in a real string literal,
  not a comment — the gate deliberately ignores comments as data). Backend-side lexicon
  coverage over API-produced strings is Track Q's own line item, not built here.
  **a11y**: BackButton/nav links were already native `<button>`s (keyboard-focusable by
  default); added Escape-to-close on the new mobile drawer.
  **e2e against the live API**: this is what U7's own live-API pass already covered (Overview
  → Projects → ProjectDetail → FeatureDetail → PRDetail → SessionDetail(economics) →
  Economics, all rendering real computed values) — the `x-talium-viewer` header fix found
  there is what makes any of Track A's manager screens reachable live at all, so it's treated
  as this line's own verification too, rather than re-running an identical pass.
  `npm run typecheck` / `vitest` / `lexicon` / `build` all green; full `tests/server/api`
  (349) unaffected (front-end-only track).

**Phase 3 (and the full MVP plan) backend + front-end are now COMPLETE.** Remaining follow-up
items are tracked as `TODO(...)` markers in-code (X1-churn, X1-reopen, X3-phase2, X4-config,
X4-per-segment-model, X5-config, X5-trend, D1-lifecycle, D1-context-curve, D3-envelope-scope,
D3-benchmark, E1-budget-period, F5-worker-gap, repo-identity-unification, U7-econ-picker) and
in Track Q (backend lexicon gate, per-mode test matrix, contract-conformance golden payloads)
— none block the branch; they're the natural next increments once this lands on `main`.

## Track Q — cross-cutting (alongside every track)

- [ ] Per-mode tests (3 datasource modes) · [ ] envelope conformance per endpoint · [ ] unavailable≠cold-start≠empty asserted · [ ] service/aggregate unit tests without FastAPI client · [ ] contract conformance per endpoint · [ ] lexicon CI over API strings · [ ] `api.request` logging, no payload content
