# Issue / Ticket Ingestion & PR Linkage — Implementation Plan

**Date:** 2026-09-09 · **Author:** Lead Architect · **Status:** H1–H6 built 2026-09-10 on
`feat/issue-ingestion` (checkboxes ticked inline; deviations recorded under each task).

**Goal:** pull issues/tickets as first-class artifacts through the **existing git ingest pipeline**,
resolve PR↔issue links authoritatively, and key both on a tracker-agnostic ref so features become a
real, titled, statused object. GitHub first; Jira-ready by shape.

**Spec (read before starting a task):** `docs/design/issue-tracker-ingestion.md` (**ISSUE**) — §3.1
contracts, §3.2 linkage precedence, §4 alternatives, §8 phasing. Backing: git-source design (**GS**)
and `docs/design/notes/2026-08-19-github-signal-sources.md` (**SIG**) for the reader model.

**Position:** the detailed expansion of **Track H** in
`docs/plan/2026-09-09-mvp-backend-implementation.md`. Feeds F4 (feature boundary key) and the
Feature/Project screens.

**Depends on:** the git ingest module (`src/server/ingest/git/`) — `map_delivery` dispatch,
`GraphQLBackfillReader`, REST gap-fill, `git_artifacts`. **Blocks:** F4, A6 `features[]`, D3.
**Gates only the feature surfaces** — the rest of the dashboard does not wait on this. **Status:**
planned 2026-09-09.

---

## 0. Ground rules specific to this module

1. **Reuse the pipeline, add a `kind`.** Issues are `git_artifacts` rows with `kind="issue"`. No new
   artifact table. The webhook mapper, GraphQL backfill, REST gap-fill, indexes, retention, and the
   §11 "paths/labels/counts, never body text" rule all apply unchanged.
2. **Reader agreement by construction** (GS §4.2, SIG §3.6): a webhook-, GraphQL-, and REST-mapped
   issue must be **byte-identical** after mapping. Extract the field-mapping once; the three readers
   call it. No re-implementation per reader.
3. **One dispatch point.** All issue mapping enters through `map_delivery` (map_github.py), exactly as
   PRs do.
4. **Never store issue/comment text** — title, state, labels, counts, timestamps, refs only.
5. **Namespaced `artifact_id`** — `github:<repo_num>:issue:<n>`; a later Jira id is a different
   namespace and cannot collide.
6. **SQL in `config/queries/git.yaml`** (extend) via `QueryCatalog`.

---

## H1 — Issue artifact mapping (webhook path)

**Edit:** `src/server/ingest/git/map_github.py`, `apply.py`.

- [x] `_map_issues(action, payload, org_id) -> MapResult` — build an `issue` artifact per ISSUE §3.1
  (`number`, `state`, `title`, `author`/`user_id`, `labels`, `milestone`, parent `linked_issue`,
  timestamps, `stats.comment_count`, `info.github.assignees`). PR-only columns stay `null`.
- [x] Wire into `map_delivery`: add `if event == "issues": return _map_issues(...)`. Handle actions
  `opened|edited|closed|reopened|labeled|unlabeled|milestoned|assigned` (+ `demilestoned|unassigned|
  pinned|unpinned`); `deleted`/`transferred` → `mapped=False` (row retained as history).
- [x] Fix `_map_issue_comment`: today it returns `mapped=False` for a comment on a **plain issue**
  (only PR-comments bump a counter). Extended it to bump the **issue** artifact's `comment_count` via
  the shared `_map_comment_bump` helper (renamed from `_map_pr_comment_bump`), retargeted to the issue
  artifact.
- [x] `apply.py`: the upsert path already accepts `kind="issue"` unchanged — no `kind` allow-list
  anywhere, and the `git_correlate`/`pr_classify` rings are gated to `pr`/`commit` so an issue row
  rings neither. No edit needed.

**Done when:** an `issues` webhook lands an `issue` artifact with title/state/labels; an
`issue_comment` on a plain issue bumps that issue's `comment_count`; replay is idempotent.

## H2 — Issue backfill (GraphQL) + gap-fill (REST)

**New:** `src/server/ingest/git/graphql/backfill_issues.graphql`; **edit:** `backfill.py`,
`cli_backfill.py`.

- [x] `backfill_issues.graphql` — an `issues` (`is:issue`) `search` connection paged like
  `backfill_search`, selecting the §3.1 fields plus `rateLimit { cost remaining nodeCount resetAt }`
  (SIG §3.6). No `parent`/sub-issue field (preview schema) — a backfilled issue leaves `linked_issue`
  null, same posture as a backfilled PR's merge-transition fields.
- [x] `_graphql_issue_to_result(node, repo_num, org_id) -> MapResult` and `_map_issues` both build via
  the shared `issue_artifact()` field-mapper in `map_github.py` (reader agreement by construction).
  `GraphQLBackfillReader.read_issue_page` added alongside `read_page`.
- [x] REST gap-fill: `RestBackfillReader.list_issue_numbers_since` (filters out PRs) +
  `read_issue` (routes through `map_delivery("issues", ...)` — same function the webhook calls,
  ETag-conditional via `rest_get`). Byte-identical `MapResult` proven in `test_backfill.py`.
- [x] `cli_backfill.py`: sweeps issues after PRs in both readers, sharing the one repo cursor and
  history window (SIG §4). `GapFillRunner._tick_repo` sweeps issues alongside PRs each tick.

**Done when:** backfilling a fixture repo pulls its open+closed issues; a GraphQL-mapped and a
REST-mapped issue for the same number are byte-identical; the `issue_comment` webhook and the backfill
agree.

## H3 — PR↔issue linkage resolver

**New:** `src/server/ingest/git/linkage.py`; `pr_issue_links` schema + migration; queries in
`git.yaml`. **Edit:** the PR GraphQL/webhook field extraction to carry `closingIssuesReferences`.

- [x] `closingIssuesReferences(first: 10) { nodes { number } }` added to the PR node in
  `backfill_search.graphql`. The webhook `pull_request` payload has no such field (GraphQL only), so
  the webhook path reaches the branch/title/body rungs only. Resolved links ride
  `MapResult.issue_links` (a new field); the GraphQL reader also feeds commit messages in for the
  trailer rung.
- [x] `linkage.resolve_links(**inputs) -> [Link]` — the §3.2 ladder, **first rung that yields any
  ref wins**, all its refs carry that rung's method+confidence. Rung 5 reuses the "closes #N" regex
  that was `_parse_linked_issue`.
- [x] Validation: `is_sha` guard drops a 40-hex branch segment; Jira-style `PROJ-N` tokens are
  accepted only when `PROJ` is a *known* project key (with none configured — the MVP GitHub-only
  case — every bare `PROJ-N` is rejected, so `UTF-8`/`ISO-8601` cannot manufacture tickets); a
  `_NEVER_JIRA` denylist blocks standards tokens even if mis-configured.
- [x] `pr_issue_links(pr_artifact_id, tracker, project_key, number, issue_artifact_id NULL, method,
  confidence, created_at)` — PK `(pr_artifact_id, tracker, project_key, number)`,
  `ix_pr_issue_links_issue` on `(tracker, project_key, number)` for the reverse walk. Schema in
  `storage/schema/issue_links.py`, alembic `a1f4c7e29b05`, DDL golden regenerated. `apply.py` writes
  via **delete-at-or-below-winning-confidence then insert**, so a weak webhook re-resolve never
  downgrades a strong backfill link. `_attach_issue_to_links` late-binds `issue_artifact_id` when the
  issue lands later.
  - **Deviation from the design's `"acme/api"` example:** `project_key` for a GitHub ref is the
    `repo_num` as a string (`str(repo_num)`), keeping the resolver pure — the human-readable
    `owner/name` stays a read-time label over `git_repos.full_name`, consistent with `repo_id` vs
    `full_name` elsewhere in the module (§6.2). The real join key is `issue_artifact_id`.

**Done when:** a PR with a `closingIssuesReferences` and a `Closes #N` body resolve to the same issue
at confidence 1.0; a branch-only link resolves at 0.7; SHA-1/UTF-8 are rejected; method+confidence
stored. ✔ (`test_linkage.py` 17 pure cases; `test_issues.py` DB cases for persistence + no-downgrade
+ late attach.)

## H4 — Tracker-agnostic issue ref *(the Jira seam)*

**New:** `src/server/ingest/git/issue_ref.py`.

- [x] `IssueRef{tracker, project_key, number, artifact_id|null}` (frozen dataclass) in
  `issue_ref.py`, with `.key` → the 3-tuple and `.as_dict()`. `github_ref()` builds a resolved
  GitHub ref (`artifact_id = github:<repo_num>:issue:<n>`); `parse_github_refs` / `parse_jira_refs`
  extract them from text. A known-key Jira token parses to `IssueRef("jira", "PLAT", 123, None)` —
  common shape, no connector.
- [x] `linkage.Link` wraps `IssueRef` + method + confidence; `apply.py` and `pr_issue_links` key off
  the tuple, never a bare string.

**Done when:** a GitHub issue and a fixture Jira key both parse into the common shape; F4 groups off the
ref, not a string. ✔ (`test_linkage.py`)

## H5 — Feature grouping helper (what F4 consumes)

**New:** `src/server/ingest/git/features.py` — read helper reading `pr_issue_links` +
`session_git_links` + issue artifacts. *Placed with the git-ingest module that owns
`pr_issue_links`, not `linkage.py` (kept pure) or `api/aggregate.py` (an unbuilt F-track module);
F4 imports and wraps it.*

- [x] `feature_of(s, catalog, IssueRef) -> Feature{ref, issue|None, status, member_pr_ids,
  session_ids, links, min_confidence, cost_usd_micros}` — issue → PRs via `pr_issue_links`, PRs →
  sessions via `session_git_links` (de-duplicated so a session on two member PRs counts once), cost
  = Σ `session_info.cost_effective_usd_micros` over the member sessions (same ledger the change-type
  rollups sum). `min_confidence` carries the weakest link, not flattened (§5).
- [x] `single_pr_feature(...)` — the fallback for a PR with no issue link. Issues with no PR return
  `status="not_started"` with the issue entity present and empty members.
- [x] `status`: `shipped` (issue closed + ≥1 PR merged) · `in_progress` (any PR) · `not_started`.
- [x] branch / label clustering of PRs that carry *no* issue link (REPO §4 rungs 2–4) — done
  2026-09-16, `features.py::branch_label_features`; see
  `docs/plan/2026-09-15-peer-features-frontend-segment-tasks.md` Task 2.

**Done when:** a fixture issue with 3 PRs resolves to a feature whose cost = Σ member-session segment
cost; a no-PR issue resolves to an empty-but-present feature. ✔ (`test_features.py` — 3-PR dedup
cost, no-PR, referenced-but-not-pulled, single-PR fallback).

## H6 — Tests, logging

**New:** `tests/server/ingest/git/` additions.

- [x] Mapper tests (`test_map_github.py` + goldens `issues_opened`/`issues_closed`): `issues`
  actions → correct artifact; `issue_comment`-on-issue bumps the issue; `deleted` unmapped;
  PR-shaped issue object skipped.
- [x] **Reader-agreement test** (`test_backfill.py`): webhook vs GraphQL vs REST issue mapping
  byte-identical; REST issue list excludes PRs; GraphQL issue paging.
- [x] Linkage precedence + validation (`test_linkage.py`, 17 cases): each rung; first-rung-wins;
  SHA-1 / UTF-8 / ISO-8601 rejection; known-key Jira ref.
- [x] `IssueRef` / parser units (`test_linkage.py`).
- [x] Feature grouping (`test_features.py`): multi-PR dedup cost, no-PR, single-PR fallback.
- [x] DB integration (`test_issues.py`): issue lands & bumps, replay idempotent, backfill↔webhook
  last-writer-wins, linkage persistence + no-downgrade + late issue attach.
- Logging: reused the existing `structlog` loggers (`ingest.git.backfill` gains `kind=`/`issues=`
  counts; `gapfill_repo` logs `issues=`). No new module logger needed — issues ride the git-source
  module's existing JSON logging.

**Done when:** `pytest tests/server/ingest/git` green including the new issue paths. ✔
(no-DB: 96 passed / 33 skipped; with `TALIUM_TEST_DSN`: full ingest+storage+pr_class 332 passed.)

---

## Sequencing

```
H1 (webhook issues) ──► H2 (backfill/gap-fill) ──► H4 (issue ref) ──► H5 (feature grouping → F4)
                    └──► H3 (linkage resolver) ───────────────────────┘
H6 alongside every task
```

- H1 is the first slice — issues start landing live. H2 backfills history.
- H3 (linkage) and H4 (ref) can proceed in parallel with H2; H5 needs H3+H4.
- Suggested PRs: (H1), (H2), (H3+H4), (H5+H6).

## Out of scope (recorded)

- **Jira connector** — OAuth + REST issue/epic pull + webhook; a later track. H4 makes the ref shape
  ready (ISSUE §8).
- **Epic/initiative modeling v2** — the "initiatives started vs shipped" trend and issue→incident
  linkage (EFF §7); later.
- **Issue/comment text** — never ingested (§11).
- **Two-way sync** — MVP reads issues; it does not write back to GitHub/Jira.
