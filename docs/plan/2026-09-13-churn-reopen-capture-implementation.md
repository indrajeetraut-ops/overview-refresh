# Track churn/reopen — capture implementation plan

**Parent:** `docs/plan/2026-09-12-mvp-gap-closure-plan.md`, "Track churn/reopen (git-capture gap)".
**Size:** L (ingest-side capture, not a query-time rollup). Sequenced as 3 landings below.

## Problem (root cause, confirmed)

Three TODOs share one cause — the ingest layer never captures the raw signal, so no
query-time change in `manager/` can synthesize it:

- **`X1-churn`** (`src/server/api/manager/quality.py:9`, PR-detail `churn_post_usd_micros`
  `service.py:848`): commit artifacts mapped from `push` webhooks land with `"stats": {}`
  (`ingest/git/map_github.py::_map_push`). The webhook `commits[]` payload carries file
  *paths* (`added`/`removed`/`modified`) but **no line counts** — GitHub only returns
  additions/deletions from a separate REST call.
- **`X1-reopen`** (`quality.py:12`, PR-detail `reopens` `service.py:849`) and **`A6-reopens`**
  (`service.py:692`): `git_artifacts.state` is overwritten in place
  (`state = EXCLUDED.state`, `config/queries/git.yaml::upsert_artifact`), guarded only by
  last-writer-wins on `updated_at`. A `reopened` action just resets `state` to `open`.
  **No transition history is persisted**, so a reopen count cannot be derived.

X2's acceptance-cost axis (`quality.py::quality_label`) therefore uses `review_rounds` alone,
not the EFF §4.1 churn+rounds+reopen composite.

## Decisions locked (2026-09-13)

- **Churn stats fetch:** GitHub **compare API, one call per push** —
  `GET /repos/{owner}/{name}/compare/{before}...{after}` returns aggregate
  `additions`/`deletions`/`total_commits` for the whole push in a single request. Per-push
  granularity is sufficient: pre/post-open is decided by push timestamp vs PR `created_at`,
  not by per-commit lines. (Per-commit `GET /commits/{sha}` was the more precise, N×-budget
  alternative — rejected for MVP.)
- **Reopen scope:** **forward-only.** New/updated PRs get accurate reopen counts via the new
  events table. PRs reopened before the table existed read `None` (honest, never guessed) —
  same posture as the other "not-yet-backfilled" caveats in the parent plan for the *initial*
  landing. A GraphQL-timeline (`ReopenedEvent`) backfill then recovers historical reopens
  (Landing 4a) so the gap doesn't persist.

---

## Landing 1 — persist PR state transitions (unblocks reopens end-to-end)

Self-contained, no external API. Closes `X1-reopen` and `A6-reopens`.

### Storage
New append-only table in `src/server/storage/schema/git.py` (FK-free per storage.md §3.1):

```
git_artifact_events
  event_id      serial PK
  artifact_id   str   (the PR's github:<repo>:pr:<n>; indexed)
  action        str   ("opened" | "reopened" | "closed" | "merged")
  to_state      str   (the resulting git_artifacts.state)
  occurred_at   ts    (the PR payload's updated_at)
  source_key    str   UNIQUE — "<artifact_id>:<action>:<occurred_at>", the replay-idempotency key
  recorded_at   ts
```

Migration under `src/server/storage/dbm/alembic/versions/` (follow the existing git-source
migration's style).

**Idempotency without threading `delivery_id`:** dedup on `source_key =
(artifact_id, action, occurred_at)`, all three derivable from the PR payload by the pure mapper.
Two distinct deliveries for the same `(pr, action, updated_at)` *are* the same transition, so
`ON CONFLICT (source_key) DO NOTHING` is correct under replay — no change to
`apply_map_result`'s signature or its ~30 call sites.

### Mapper → apply
- `map_github.py`: add `MapResult.pr_state_event: dict | None`. `_map_pull_request` already
  knows `action`, `state`, and `pr["updated_at"]` — emit
  `{"artifact_id", "action", "to_state", "occurred_at", "source_key"}` for every mapped PR
  action (it currently discards `action` after the `_PR_ACTIONS` gate). `None` for non-PR
  results. Note `merged` is expressed via the `closed` action + `merged=true`; record
  `action="merged"` when `merged` so the vocabulary matches `state`.
- `apply.py::apply_map_result`: after the artifact upsert, if `result.pr_state_event` is set,
  `INSERT ... ON CONFLICT (source_key) DO NOTHING` via a new `insert_artifact_event` query in
  `git.yaml`. Backfill readers get this for free (they build `MapResult`s through the same
  mapper translators; a backfilled PR emits its current-state event too, harmless under the
  dedup).

### Read sites
- New query `pr_reopen_counts` in `config/queries/manager.yaml`:
  `SELECT artifact_id, COUNT(*) FROM git_artifact_events WHERE action='reopened' AND artifact_id = ANY(:pr_ids) GROUP BY artifact_id`.
- `quality.py::pr_acceptance_metrics`: add `reopens` (0 when the PR has no reopen event, since
  the events table now definitively covers forward PRs — but keep `None` for a PR with *no*
  event rows at all, i.e. ingested before this landing, to stay honest).
- `service.py` PR detail: set `"reopens"` from the metric (drop the `TODO(X1-reopen)`).
- `service.py::project_detail` A6 highlights: `reopened_prs` = count of the project's PRs with
  ≥1 reopen event (drop the `TODO(A6-reopens)`).

### Tests
`tests/server/ingest/git/` — a reopen sequence (opened → closed → reopened → closed) produces
exactly one `reopened` event; replay of the same deliveries adds none. A
`test_manager_service_pr_detail`/`project_detail` regression asserting the surfaced count.

---

## Landing 2 — commit-stats enrichment (unblocks churn)

Depends on nothing in Landing 1; can be built in parallel. Closes `X1-churn`.

### Why a worker step, not the mapper
`map_github.py` is pure/no-IO by contract (its module docstring). The compare call belongs in
the worker, mirroring the A5 backfill precedent (reader calls API → mapper → `apply.py`). The
`stats` column already merges additively (`stats = COALESCE(...) || EXCLUDED.stats` in
`upsert_artifact`), so a later stats fill needs **no re-map** — a plain merge-patch.

### Mechanism
- New ring `ring_git_commit_stats` in `git.yaml` (parallel to `ring_git_correlate`), fired from
  `apply.py` for each `push`-sourced result carrying `push_before`/`push_after` (one job per
  push, keyed on `before..after` for `queue_jobs` coalescing).
- New handler `src/server/worker/git_commit_stats.py`: resolves the repo's `owner/name` +
  `installation_id` (via `git_repos`), calls
  `client.rest_get(installation_id, f"/repos/{owner}/{name}/compare/{before}...{after}")`,
  reads top-level `.files[].{additions,deletions}` summed (or the response's own
  `.total` fields), and merge-patches `{additions, deletions, changed_files}` onto the push's
  head-commit artifact `stats` via a new `merge_commit_stats` query. Respects the existing
  `_InstallationThrottle` in `GitHubClient` — no new rate-limit logic.
- Truncated pushes (`>= PUSH_TRUNCATE_AT`, mapped as a range artifact) already carry
  `before_sha`/`after_sha`; the same compare call fills them, so this also firms up the
  history-import path the mapper left as a range.

### Read sites
- New query `post_open_commit_stats` in `manager.yaml`: for a PR, sum
  `additions+deletions` over commit artifacts linked to the PR (`commit_shas`/join already used
  by the correlator) whose `created_at > pr.created_at`.
- `quality.py::pr_acceptance_metrics` (or a sibling) exposes `churn_post_lines`; `service.py`
  PR detail sets `churn_post_usd_micros` (price via the same date-anchored pricing
  `graph_fold` uses — or ship raw lines first and price in the X2 landing) and `rework_ratio`
  (post-open ÷ total). Drop the `TODO(X1-churn)` markers.

### Tests
A push whose compare response carries additions/deletions lands enriched commit stats; a
post-open push contributes to `churn_post`, a pre-open one does not. Mock the client (existing
tests already stub `GitHubClient` for backfill).

---

## Landing 3 — revisit X2's quality composite

Product/threshold call, not just wiring — ties to the existing `X2-config` TODO.

- `quality.py::quality_label` currently: `acceptance_high = review_rounds >= 1`. Extend to the
  EFF §4.1 composite over `{review_rounds, churn_post, reopens}`. The **weighting and cutoffs**
  are the open question — decide with product, then move the constants to `config/api.yaml`
  alongside the `X2-config` sweep rather than hardcoding new ones here.
- Once landed, update the parent plan's Track churn/reopen checkboxes and the downstream
  `A5-peer-task-mix` note (it's gated on this).

---

## Landing 4 — backfills for pre-existing data (in scope, 2026-09-13)

Forward-only capture (Landings 1–2) leaves already-ingested PRs/pushes reading `None`. These
two backfills recover the history. Both follow the parent plan's established backfill posture:
re-derive from data already in the DB or from the API, never a re-ingest (the positional-dedup
wall in `fold_handler.py` blocks re-ingest anyway).

### 4a — reopen history (GraphQL timeline)
- Add a `backfill_pr_timeline.graphql` query requesting `timelineItems(itemTypes:
  [REOPENED_EVENT])` with each event's `createdAt`, for a PR by number.
- New reader method on `GraphQLBackfillReader` (or a dedicated `cli_backfill_reopens.py`
  entry point under `ingest/git/`, per CLAUDE.md's `cli_{function}` rule) that, for each PR
  artifact in a repo, reads its reopen timeline and writes one `git_artifact_events`
  `action="reopened"` row per event (same `source_key` dedup, so it's safe to co-exist with
  and re-run alongside forward capture).
- Drives off `git_repos` the same way the existing backfill readers do; respects the client
  throttle.

### 4b — churn stats (compare API over historical pushes)
- New `cli_backfill_commit_stats.py` entry point: select `commit` artifacts with empty `stats`
  that carry `before_sha`/`after_sha` (the range/head commits), call the same compare endpoint
  Landing 2's worker uses, and merge-patch the stats in. Reuses Landing 2's `merge_commit_stats`
  query verbatim — the CLI is just a batch driver over the same primitive.
- Idempotent: an already-filled `stats` is skipped (the select filters on empty), and the merge
  is additive.

Both CLIs get a shell wrapper in `utils/` calling `python -m {module}.cli_{function}` per
CLAUDE.md's entry-point rule.

## Checklist (mirrors the parent plan's Track churn/reopen boxes)
- [x] Landing 1: `git_artifact_events` table (`schema/git.py`) + migration `b7c31f9a4e02`;
      mapper `MapResult.pr_state_event` (`_pr_state_event`, closed+merged normalized to
      `"merged"`, non-transition actions record nothing); `apply.py` inserts via
      `insert_artifact_event` with `ON CONFLICT (source_key) DO NOTHING` (replay-idempotent,
      no `delivery_id` threading). Read sites: `pr_reopen_counts` query →
      `quality.py::pr_acceptance_metrics.reopens` (honest `None` for uncaptured PRs, `0` for
      captured-never-reopened) → PR-detail `reopens` + a live "Reopened" flag; `project_detail`
      A6 `reopened_prs`. Tests: mapper unit (`test_map_github.py`), DB lifecycle/replay
      (`test_state_events.py`), service surfacing (pr_detail/project_detail), schema
      (`test_git_schema.py`, DDL golden, table-set). `TODO(X1-reopen)`/`TODO(A6-reopens)`
      markers removed.
- [x] Landing 2: `git_commit_stats` worker (`worker/git_commit_stats.py`, compare API, one call
      per push, additive `merge_commit_stats`) rung from `apply.py::_ring_commit_stats`
      (`ring_git_commit_stats`, coalesced per `before..after`, zero-`before` branch-creation
      pushes skipped); wired in `cli_work.py` (shared `GitHubClient`) + `worker.yaml` topics.
      Read: `post_open_commit_churn` query links commits by **head branch within the PR's open
      window** (NOT `commit_shas` — a squash/rebase collapses to one post-open base-branch
      commit that would read as ~100% churn) → `service.py::_post_open_churn` sets PR-detail
      `churn_post_lines` + `rework_ratio` (post-open ÷ final size), each honest `None` when
      commit history / stats aren't captured. `churn_post_usd_micros` stays `None` on purpose
      (no lines→$ conversion; that's Landing 3). Tests: worker handler with a faked client
      (merge/additive/304/API-error/unknown-repo), apply-ring (`test_git_map.py`), read-side
      (churn, squash-exclusion, both None gates). openapi/api-types/mock regenerated;
      front-end tsc + lexicon + vitest green. `TODO(X1-churn)` markers removed from
      `quality.py`/`service.py`.
- [x] Landing 3: X2 acceptance-cost axis is now the EFF composite. `quality_label(making_cost_pct,
      acceptance, config)` marks messy acceptance if ANY of review_rounds / reopens / rework_ratio
      meets its cutoff; an uncaptured signal (`None`) never flips a clean PR. Thresholds moved to
      `config/api.yaml` `quality:` (`ApiConfig.QualityConfig`, X2-config) with defaults preserving
      the old review-rounds-only behavior. `pr_acceptance_metrics` is now the single batched
      source of all X1 signals (added `churn_post_lines`/`rework_ratio` via a batched
      `post_open_commit_churn` over `:pr_ids`); `service.py::_post_open_churn` removed, PR detail
      reads from acceptance. New PR-detail "Heavy rework" flag (rework_ratio cutoff). Callers
      updated (`quality_counts_for_prs`, `_person_recent_sessions`, `pr_detail`). Tests: composite
      matrix, reopen/rework-trip, uncaptured-never-flips, config-driven thresholds. `TODO(X2-config)`
      cleared.
- [x] Landing 4a: `cli_backfill_reopens.py` (+ `utils/backfill_reopens.sh`) reads each known
      PR's `REOPENED_EVENT` timeline (`graphql/backfill_pr_timeline.graphql`) and appends
      `git_artifact_events` rows via `insert_artifact_event` (now `RETURNING event_id` so the
      write count is observable). Idempotent on `source_key`; dry-run by default, `--apply` to
      write; `--repo`/`--org`. Queries `get_repo_by_full_name` + `list_repo_pr_numbers`. Caveat
      documented: timeline keys on event `createdAt`, webhook on PR `updated_at` -- same instant,
      rare same-second-edit overlap accepted for a metrics backfill.
- [x] Landing 4b: `cli_backfill_commit_stats.py` (+ `utils/backfill_commit_stats.sh`) sweeps
      commit artifacts still missing line stats that carry their push range
      (`info.github.push_meta` or the `before_sha`/`after_sha` columns), via the new
      `commits_needing_stats` query, and reuses the worker's `fetch_and_merge_commit_stats`
      primitive verbatim (extracted for exactly this). Idempotent (candidate query excludes
      filled rows + additive merge); dry-run by default, `--apply` to write; `--repo` scopes,
      else every repo. Tests fake the client (GraphQL/REST) -- no network.
- [ ] Update parent plan checkboxes; remove the `TODO(X1-churn)`/`TODO(X1-reopen)`/
      `TODO(A6-reopens)` markers as each lands (CLAUDE.md rule 4)
