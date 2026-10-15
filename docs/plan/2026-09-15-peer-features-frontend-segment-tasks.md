# Implementation plan — peer comparison, feature clustering, My-Usage wiring, scope picker, per-segment models

**Status:** Ready to implement. Five independent tasks; each section is self-contained and can be
taken as its own PR. Ordered cheapest-first.
**Origin:** the "product-logic upgrades" and "front-end loose ends" tracks of
`docs/plan/2026-09-12-mvp-gap-closure-plan.md`. Their upstream blockers (Track churn/reopen,
segment-level pricing Phase 1) have landed, so all five are buildable now.

Task | Source TODO | Size | Kind
---|---|---|---
1. Apples-to-apples cheapest peer | `A5-peer-task-mix` | M | backend
2. Cluster issue-less PRs into features | `H5` | M | ingest/query
3. Wire My-Usage to live data | `U5-myusage` | S–M | schema + front-end
4. Names-only scope-picker endpoint | `U7-econ-picker` | S | backend + front-end
5. Per-model breakdown per segment | `X4-per-segment-model` | M | worker fold + API

---

## Task 1 — Fair "cheapest peer" by cost-per-unit-of-code (`A5-peer-task-mix`)

> **Update 2026-09-17 — linked work only.** The $/KLOC below divided *all* of a person's session
> spend by *all* their merged-PR lines. That named a teammate with no session telemetry (10 merged
> PRs, 0 sessions) "cheapest" at $0/KLOC, and cost and lines described different work. Now
> (`aggregate.linked_work_volume` / `cheapest_peer`): lines come only from merged PRs with linked
> sessions, cost only from those sessions (each counted once). A PR-size cap (`peer.max_pr_lines`,
> leave PRs over 10K lines out entirely) was built and then **disabled** the same day: it dropped
> real spend (82% of one person's linked cost), was gameable, and couldn't tell generated code from
> a real large PR. Generated/vendored lines still count until file-level handling lands
> (`TODO(peer-generated-lines)`). Subject and candidates alike need ≥ `min_linked_prs` (3)
> linked PRs, a linked share ≥ `min_linked_share` (0.5) of their sizable merged PRs, and ≥
> `min_lines_for_compare` (200) linked lines. All knobs are in `config/api.yaml` `peer:`.

### What's wrong today
On a person's screen, `peer_compare` names the teammate with the lowest **blended $/PR** across
the team. The metric is unfair: **a PR is not a unit of volume.** A one-line bug fix and a
2,000-line feature are both "1 PR," so three trivial PRs can make a light contributor look
"cheapest" against someone shipping real volume. Comparing by PR count (or normalizing by it)
measures the wrong thing.

**Decision (per review): measure output as the volume of code emitted, not PR count.** The right
efficiency measure is **cost per unit of delivered code** — total spend ÷ lines of code changed —
which is volume-normalized by construction, so a light and a heavy contributor can be compared
fairly.

### Current implementation
- `service.py::_peer_cost_per_pr` (~L444) already restricts candidates to
  `directory.members_of(team_id, include_subteams=False)` — **same team is already enforced**, no
  change needed there. It builds `{uid: {"cost_per_pr_usd_micros": ...}}` and prices $/PR.
- `aggregate.py::cheapest_peer(candidates, exclude)` (~L643) returns the min-$/PR peer, excluding
  self and unpriced (no merged PRs) candidates.

### The code-volume data already exists (Track churn/reopen)
`git_commit_stats.stats` carries `additions`/`deletions`/`changed_files` per commit, and
`config/queries/manager.yaml::post_open_commit_churn` already computes a PR's
`final_lines = additions + deletions`. So **lines-of-code per PR is queryable today** — no new
capture, just a sum over a person's merged PRs.

### Design — switch the metric to $/line of code, guarded by a volume floor
1. **Volume = lines of code changed** (`additions + deletions`, i.e. churn) across the person's
   merged PRs in the window. Deletions count: removing code is real delivered work, and this
   matches the existing `final_lines` definition. (Not additions-only, not PR count.)
2. **Efficiency metric = cost per line** (`$/LOC`, or per-KLOC for readable numbers):
   `total_spend_usd_micros ÷ total_lines_changed`. This *is* the volume normalization — it makes
   a light and a heavy contributor directly comparable, which the PR count never did.
   `cheapest_peer` returns the teammate with the lowest `$/LOC`.
3. **Output floor (replaces the PR-count floor):** both the subject and any candidate peer must
   have changed at least `min_lines_for_compare` lines in the window (default e.g. 200) to be
   comparable. Below the floor, `$/LOC` is noise (a 5-line month with one costly session reads as
   an absurd per-line cost). Subject below the floor → `peer_compare = None` (honest cold-start).
4. **Optional scale band (config, default off/wide):** even normalized, a 100-line contributor
   and a 50,000-line one may not be a useful comparison. Keep an *optional* ratio band on total
   lines (`band_low`/`band_high`) so the peer is within a comparable scale; default it wide (or
   disabled) since `$/LOC` already does the heavy lifting — a knob, not a requirement.
5. **`_peer_cost_per_pr` → `_peer_cost_per_loc`:** replace/augment the per-candidate value with
   `{"cost_usd_micros": ..., "lines_changed": ..., "cost_per_kloc_usd_micros": ...}`. Sum each
   member's merged-PR `final_lines` (new query: `member_lines_changed` over a user's merged PRs,
   or extend the existing merged-PR fetch to join `git_commit_stats`).
6. **`cheapest_peer` signature change:** takes the subject's own lines + the `peer` config; among
   above-floor, in-band, non-self candidates, return the min `$/LOC`; `None` when none qualify.

### Known limitation (state it honestly in the PR)
`$/LOC` rewards **verbose or generated output** (boilerplate, large mechanical edits look
"efficient") and can penalize **dense, hard work** (a small subtle fix costs a lot per line).
Counting deletions and requiring a volume floor mitigates but does not eliminate this. No single
scalar is perfect; `$/LOC` + a volume floor is a defensible v1 and strictly better than `$/PR`.
A future refinement could weight by task difficulty (the model-fit reasoning-demand signal), but
that's out of scope here.

### Data caveat
`git_commit_stats` only populates for pushes observed after Track churn/reopen landed, plus
history filled by `cli_backfill_commit_stats`. A PR with no line stats yet contributes no volume;
a peer whose PRs are all unstatted isn't comparable (cold-start, not zero). Run the backfill on
the stack before trusting live peer numbers — same posture as the `session_git_links`
re-correlation caveat elsewhere.

### Files
- `src/server/api/manager/aggregate.py` — `cheapest_peer` (rank by `$/LOC`, floor + optional band).
- `src/server/api/manager/service.py` — `_peer_cost_per_loc` (sum members' merged-PR
  `final_lines`), `person_detail` (~L558-561, pass subject lines + config; surface `$/KLOC`).
- `config/queries/manager.yaml` — `member_lines_changed` (sum `final_lines` per user's merged PRs).
- `src/server/api/config.py` + `config/api.yaml` — new `PeerConfig` / `peer:` block
  (`min_lines_for_compare`, optional `band_low`/`band_high`).
- `schemas/manager.py` — `peer_compare` may carry the peer's `$/KLOC` for display, not just an id.
- `tests/server/api/test_manager_aggregate.py` — `cheapest_peer` tests.

### Tests
- A peer with 3 one-line PRs is **not** "cheapest" against a subject shipping thousands of lines
  (the exact regression this task fixes).
- Two peers above the floor: the genuinely lower `$/LOC` one is returned.
- Subject below `min_lines_for_compare` → `None`.
- A peer with merged PRs but no line stats (unbackfilled) is excluded, not counted as zero-volume.

### Size: **M.** Open question: confirm `min_lines_for_compare` and whether the optional scale
band is on by default (defaults ship, tunable later).

---

## Task 2 — Cluster issue-less PRs into features by branch/label (`H5`)

### What's wrong today
Feature grouping only clusters PRs that link to an **issue**. A PR with no issue link becomes its
own one-PR feature (`single_pr_feature`). Two PRs that are obviously the same feature (same
branch, or same label) but carry no issue link show up as two separate single-PR "features."

### Current implementation
- `ingest/git/features.py`: `feature_of(ref)` builds an issue-keyed feature; `single_pr_feature(pr)`
  is the no-issue fallback (one PR, synthetic `pr:` ref).
- `manager/quality.py::list_features_for_repo` (~L113) is the assembler: it emits one
  `feature_of` per issue number, then one `single_pr_feature` per **orphan** PR
  (`repo_prs_without_issue_link` query, ~L134).
- PR artifacts already carry the needed fields: `branch` (head ref) and `labels`
  (`ingest/git/map_github.py` ~L226/L235) — no new capture.

### Design — branch → label clustering rung (REPO §4 boundary-key priority)
The documented priority is `linked issue → PR-cluster on one branch → branch → label`. Task 1's
top rung (issue) and the single-PR fallback exist; H5 adds the **branch, then label** rungs for
orphan PRs only (issue-linked PRs are unaffected).

1. **New query** `repo_orphan_pr_boundaries` (or extend `repo_prs_without_issue_link`): return
   each orphan PR's `artifact_id`, `branch`, and `labels` (read from the PR artifact payload).
2. **New composer** `features.py::branch_label_features(s, catalog, orphan_pr_rows) -> list[Feature]`:
   - Group orphan PRs by **branch** (non-null, non-default). PRs on the same non-default branch
     are one feature.
   - For PRs with no usable branch, group by **shared label** (a single shared label is enough;
     if a PR has multiple labels, pick a deterministic primary — e.g. first alphabetically — to
     avoid a PR landing in two features).
   - PRs with neither a usable branch nor a label fall back to `single_pr_feature` (today's
     behaviour, preserved).
   - Key each clustered feature by a synthetic ref that stays uniform with the rest
     (`branch:<repo_num>:<branch>` / `label:<repo_num>:<label>`), mirroring how
     `single_pr_feature` uses a `pr:` ref. Update `quality.py::feature_by_id` /`_feature_id`
     to recompose these ids (they currently handle `pr:` and issue refs only).
   - Compose cost/sessions/status the same way `feature_of` does — reuse `_sessions_and_cost`
     and `_status` over the cluster's member PR ids.
3. **Assembler change:** in `list_features_for_repo`, replace the per-orphan `single_pr_feature`
   loop with a call to `branch_label_features`, which internally still returns single-PR features
   for the un-clusterable remainder.

### Decisions to make
- **Default branch exclusion:** never cluster on the repo's default branch (`main`/`master`) —
  every direct-to-main PR shares it and would collapse into one giant bogus feature. Exclude the
  default branch (fetch it from `git_repos`, or treat known defaults as non-clustering).
- **Confidence:** issue-linked features carry a link confidence; a branch/label cluster is an
  inference. Set `min_confidence` to a fixed "inferred" value (e.g. 0.5, matching the
  `revision_loop` beat's `inferred` confidence convention) so the UI can distinguish a
  branch-clustered feature from an issue-backed one.

### Files
- `src/server/ingest/git/features.py` — new `branch_label_features` (+ synthetic refs).
- `config/queries/manager.yaml` — new/extended orphan-PR-with-boundaries query.
- `src/server/api/manager/quality.py` — `list_features_for_repo`, `feature_by_id`, `_feature_id`.
- `tests/server/...` — clustering unit tests.

### Tests
- Two orphan PRs on the same non-default branch → one feature with both PRs.
- Two orphan PRs sharing a label, no branch → one feature.
- Orphan PR on the default branch, no label → its own single-PR feature (not clustered).
- A clustered feature's cost = Σ its member PRs' cost; status/sessions composed correctly.
- `feature_by_id` round-trips a `branch:`/`label:` id back to the same feature.

### Size: **M.**

---

## Task 3 — Wire "My Usage" to live data (`U5-myusage`)

### What's wrong today
`webui/src/screens/MyUsage.tsx` reads `myPerson` / `mySessionHistory` from `data/mock.ts`. The
headline tiles (spend, sessions run, PRs), the monthly-limit progress bar, the per-repo spend
breakdown, and the session-history list are all fixture data. (The tips card is already live —
Track A7.)

### Why it's still on mock
`MyUsage.tsx`'s own TODO says binding to `GET /v1/users/{id}/overview` awaits porting that
endpoint's response to a **typed schema**. Confirmed: `app.py::get_overview` (~L132) returns a
bare `dict` with **no `response_model`**, so `api-types.ts` has no `Overview` type to bind to —
unlike the manager endpoints which are all typed (e.g. `/v1/org/overview` →
`OrgOverviewEnvelope`).

### The data already exists
`single_user/service.py::overview` (~L326) returns `{z0, z1, z2, z3, z5_scorecard, ...}`:
- `z1` = `_z1_headline` — the spend / PR / session headline numbers.
- `z2` — model split / economics (per-repo breakdown material).
So this is a **wiring + typing** task, not a data gap (same pattern as the QW1/QW3 "data exists,
never promoted" fixes).

### Design
1. **Type the endpoint:** add an `Overview` response schema in `schemas/single_user.py` (mirror
   the actual `overview()` dict — `z0/z1/z2/z3` blocks) and set `response_model=` on
   `get_overview`. Follow `envelope.py`'s envelope convention if the other single-user endpoints
   use one.
2. **Regenerate the client types:** run the OpenAPI export + `api-types.ts` regen (the repo's
   existing `cli_export_openapi.py` → `openapi.json` → codegen step, same as every prior schema
   change).
3. **Bind the screen:** replace `myPerson` / `mySessionHistory` reads in `MyUsage.tsx` with a
   `useData((src) => src.userOverview(userId, timeframe))` call:
   - headline tiles ← `z1` (spend, sessions, PRs);
   - monthly-limit bar ← the person's allowance/budget (from `z1` or a budget read — confirm
     which block carries the limit; if none does, this reuses the F1 budget the manager side
     already reads);
   - per-repo breakdown ← `z2`'s repo split (the hardcoded `api-gateway`/`auth-service` rows go);
   - session history ← the existing `/v1/users/{id}/sessions` list (already an endpoint,
     `get_sessions_list`), not the mock.
4. **Cold-start:** every block honours the same `Unavailable`/empty treatment the other live
   screens use; no fabricated numbers when a block is absent.

### Files
- `src/server/api/schemas/single_user.py` — `Overview` schema (+ envelope).
- `src/server/api/app.py` — `response_model` on `get_overview`.
- `src/webui/src/data/api-types.ts` — regenerated.
- `src/webui/src/screens/MyUsage.tsx` — bind to live data; drop `mock` imports.

### Tests / verification
- Backend: a schema round-trip test for `overview()` (shape matches `Overview`).
- Front-end: typecheck + lexicon + build; verify live in the browser that the tiles/breakdown/
  history render real numbers and cold-start correctly.

### Size: **S–M** (M only because it's the first single-user endpoint to get typed; the schema
is the bulk of it). Open question: confirm which block carries the monthly limit, or whether to
read the F1 budget for it.

---

## Task 4 — Names-only scope-picker endpoint (`U7-econ-picker`)

### What's wrong today
`EconomicsScreen.tsx`'s scope `<select>` is populated by fetching the full **Teams** and
**Projects** table endpoints with `limit: 50`, scoped to the current timeframe, and throwing away
everything except `{id, name}`.

### Why it's worth a dedicated endpoint (the real reasons)
Not payload weight — the substantive reasons are:
1. **Correctness:** `limit: 50` silently truncates the picker. An org with >50 teams or >50
   projects would be missing scopes from the dropdown, with no error. A picker must enumerate
   **all** scopes.
2. **Timeframe-independence:** a team/repo exists regardless of the window, but the current
   fetch is timeframe-scoped, so every timeframe change refetches heavy per-row cost/velocity/
   budget aggregates only to rebuild an identical name list.
3. Minor: the heavy endpoints compute aggregates the picker discards.

> **Recommendation:** build it — but as a correctness + caching fix, not a perf micro-opt. If the
> product guarantees orgs stay under 50 teams/projects and the refetch-on-timeframe is
> acceptable, this can instead be **closed as won't-fix** and the TODO removed. Decide before
> building. The rest of this section assumes "build it."

### Design
1. **New endpoint** `GET /v1/scopes` (or `/v1/org/scopes`): returns
   `{teams: [{id, name}], projects: [{id, name}]}` for the viewer's org — **no timeframe param**,
   **no limit** (all scopes the viewer can see, gated by `Viewer.visible_*`). Small typed schema.
2. **Backend:** a thin service reading team names (`directory`) and project/repo names
   (`git_repos.full_name` / the projects catalog) — names only, no cost/velocity/budget joins.
3. **Front-end:** `EconomicsScreen.tsx` calls `src.scopes()` once (timeframe-independent, so it
   caches and never refetches on timeframe change); drop the `teams(...)`/`projects(...)` calls
   and the `limit: 50`. Remove the `TODO(U7-econ-picker)`.

### Files
- `src/server/api/app.py` + `schemas/manager.py` (or `single_user.py`) — new endpoint + schema.
- `src/server/api/manager/service.py` — `list_scopes` service.
- `src/webui/src/data/*` + `EconomicsScreen.tsx` — new datasource method + wiring.

### Tests
- Endpoint returns all teams/projects the viewer can see, no truncation, no timeframe dependence.
- Picker renders scopes beyond the old 50-row limit.

### Size: **S.**

---

## Task 5 — Per-model breakdown per work-segment (`X4-per-segment-model`)

### What's wrong today
A segment carries a single `cost_usd_micros`; model choice is only known at the **session**
level (its one dominant model → tier). So the model-fit feature (recently reworked to judge by
reasoning demand) can only compare a segment against the *session's* tier — a session can't
legitimately show both an "over" segment and an "under" segment, and a segment run partly on
Opus and partly on Sonnet can't be attributed.

### The data is already visited, just discarded
`segments/beats.py::build_segments` (~L312) already loops every `assistant_message` in a segment,
reads its own `payload["model"]` (~L360), prices its tokens, and **sums** into one
`cost_micros`/`carry_micros`. It sees each message's model per-message and throws the breakdown
away — exactly the pattern the D1 context-curve and reasoning-rollup fixes exploited (keep the
per-turn series that was already computed).

### Design
1. **Fold change (`build_segments`):** alongside the segment total, accumulate a per-model
   breakdown:
   `segment["models"] = {model: {input, output, cache_read, cache_write, cost_usd_micros}}`.
   Keep `cost_usd_micros` as the total (Σ over models) for backward compatibility. Same
   `pricing_config`/`at` path already threaded in (segment-level pricing Phase 1 landed).
2. **Downstream — segment's own tier:** in `aggregate.py`, derive a segment's dominant model
   from `segment["models"]` (reuse `dominant_model`) → `model_tier`, and have `segment_fit`
   compare against the **segment's** tier when present, falling back to the session tier when a
   segment has no `models` (cold-start / folded before this). This is the exact fallback site the
   model-fit rework left a `TODO(model-fit-per-segment)` marker at — remove it here.
3. **Now both verdicts can co-occur:** with per-segment tiers, one session can carry both an
   "over" segment (high-tier on low-demand work) and an "under" segment (low-tier on high-demand
   work). `model_fit`'s `under`-wins precedence was already written defensively for this — no
   orchestration change, it just becomes reachable.
4. **Savings figure:** the per-segment savings math already prices the flagged segment; with a
   per-segment model it can price against that segment's *own* model rather than the session
   dominant — a small accuracy improvement, not a new mechanism.

### Backfill caveat (state it in the PR)
Like every fold-level change, this only populates `segment["models"]` for sessions folded from
now on. Existing sessions keep the old single-cost segments (their `segment_fit` falls back to
the session tier — correct, not broken) until a backfill re-runs `build_segments` over the
persisted graph (`session_graph`), the same backfill shape the pricing/context-curve fixes use.
Optional, deferrable.

### Files
- `src/server/worker/segments/beats.py` — `build_segments` (per-model accumulation).
- `src/server/worker/segments/__init__.py` — pass through if the shape changes.
- `src/server/api/manager/aggregate.py` — `segment_fit` (segment-own tier + fallback), remove
  `TODO(model-fit-per-segment)`.
- A/B differential allowlist — add `segments[].models` (a new fold field with no baseline
  equivalent, same as prior new-field additions).
- `tests/server/worker/...` + `tests/server/api/...` — see below.

### Tests
- `build_segments` on a segment with two models yields a `models` map summing to the segment
  total.
- `segment_fit` uses the segment's own tier when `models` is present; falls back to session tier
  when absent.
- `model_fit` surfaces both an "over" and an "under" segment in one crafted session (previously
  impossible), with `under` winning the session verdict.

### Size: **M.**

---

## Suggested order
4 (S) → 3 (S–M) → 1 (M) → 5 (M) → 2 (M). Tasks 4 and 3 are quick and user-visible; 1 needs the
product band numbers confirmed; 5 improves the just-landed model-fit feature; 2 is the most
self-contained ingest change. None block each other.

## Product confirmations needed before/while building
- Task 1: `min_lines_for_compare` floor + whether the optional scale band is on by default.
- Task 2: default-branch exclusion list; inferred-cluster confidence value.
- Task 3: where the monthly limit comes from.
- Task 4: build vs. close-as-won't-fix (only if orgs are guaranteed small).
