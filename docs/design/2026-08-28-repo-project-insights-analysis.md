# Analysis: Project / Repository-Level Insights

**Date:** 2026-08-28 · **Author:** Lead Architect · **Status:** Analysis (pre-design)
**Assumption:** everything on `feat/github-acquisition-poc` is present post-merge. This is not a
feasibility study of *whether* the data exists — it does — but an inventory of *what to represent
and how*, given a data model that already carries repo identity, per-segment cost, and PR linkage.

**Purpose.** Today the product answers *"what happened in this session"* and *"how is this user
doing."* It has **no project or repository axis.** Two new surfaces close that gap:

- **View A — Repo split on the *user* dashboard.** A developer's own *"where did my effort and
  spend go across the repos I touch?"* Additive to S1–S4, single `user_id`.
- **View B — the tenant Repository view.** A lead/admin's *"what do our repositories cost, who pays
  into each, and what did a unit of work cost?"* Cross-user, aggregated by repo. This is the "single
  pane" the ProductSpec leaves as a placeholder.

---

## 0. TL;DR

1. **The data is already there.** `session_info` carries `info.repo` = `{repo_id,
   repo_id_provenance, repo_key, repos[]}`, per-segment cost + repo (`segments[].cost_usd_micros`,
   `segments[].repo_key`), `pr_list[]` with `pr_repository`, PR work-classification (`pr_class/`),
   and Tier-0 task class (`session_type`). **These views are aggregation + presentation, not new
   capture.**
2. **The attribution model is largely pre-decided by the schema — and correctly.** `info.repo.repos[]`
   already ranks every project a session touched, `primary` first, weighted by resolved
   `tool_call.files[]`. Multi-repo sessions are a solved case, not an open question.
3. **Cost is attributable *below* session grain.** `segments[].cost_usd_micros` + `segments[].repo_key`
   mean a session that spans two repos splits by *measured* segment cost — not a modelled ratio. So
   "cost by repo" can be exact even for mixed sessions.
4. **`repo_id` vs `repo_key` is the one discipline to hold.** `repo_id` (evidence-backed, provenance-
   tagged) is the **only** cross-user/cross-machine join key. `repo_key` (worktree-stripped cwd leaf)
   is a **display label only** — never group View B on it.
5. **"Cost of a feature" is buildable, and it's the flagship.** `pr_list` + `pr_class` + `session_type`
   give session→PR→repo linkage and work type. The only open product call is the *feature boundary
   key* (linked issue vs branch vs label vs PR-cluster).
6. **Datasource mode still governs the edges.** `otel_only` has no `info.repo` — the repo band
   renders `unavailable`, same precedent as S4. Provenance is mixed: a `path`-tier repo is
   lower-confidence than a `pr_link`-tier one, and the UI should show that rather than flatten it.

---

## 1. The attribution model — mostly settled, worth stating explicitly

Every number is *session (or segment) economics re-grouped by repo.* The schema already answers the
three questions that used to be hard.

### 1.1 Repository identity + confidence — the provenance ladder (implemented)

`repo_info` resolves `repo_id` on a 3-tier evidence ladder, strongest first:

| Provenance | Derived from | Join-safe across users/machines? |
|---|---|---|
| `pr_link` | `normalize_remote(pr_repository)` from a `pr_link` event | **Yes — canonical** |
| `remote` | `normalize_remote()` of a captured `git remote` URL | **Yes — canonical** |
| `path` | `local:<repo_key>` from the worktree-stripped cwd leaf | **No — local label only** |

**Design consequence:** carry `repo_id_provenance` into every repo view and *render* it — a `path`-tier
repo is a best-effort local label, a `pr_link`/`remote` repo is a real cross-user identity. View B
should prefer canonical-provenance repos and visibly mark (or fold) `path`-only ones, or two
developers' `local:api` labels will look like one repo when they may not be.

### 1.2 Multi-repo sessions — solved by `repos[]`

`info.repo.repos[]` lists every project touched, `primary` first, ranked by how many
`tool_call.files[]` resolved into each. **Dominant-repo attribution is the shipped default**
(primary = most file refs); secondaries are retained with their own `refs` count and provenance. No
new rule needed — surface `primary` as *the* repo and expose secondaries on drill-down.

### 1.3 Cost grain — session *or* segment

Two legitimate rollups, and the schema supports both:

- **Session-level (simple):** attribute the whole `cost_usd` to `info.repo.repo_id` (the primary).
  One row per session; a small tail of mixed-session error absorbed into the primary.
- **Segment-level (exact for mixed sessions):** `SUM(segments[].cost_usd_micros) GROUP BY
  segments[].repo_key`, mapping `repo_key`→`repo_id` via the session's `repos[]`. This makes "cost by
  repo" *measured* even when one session worked two repos.

**Recommendation:** session-level for the headline tiles (cheap, matches how users think), with
segment-level as the exact backing for View B roll-ups and any mixed-session drill-down. Cost source
stays `cost_usd` / `cost_usd_micros` — no second cost path (dashboard-doc-set lock).

### 1.4 Project vs repository

- **Repository** = `repo_id`. View B's spine.
- **Project** = a query-time `GROUP BY` over carried keys (labels, milestone, a repo→project map).
  Don't materialize a grouping unit in the data layer — the github note's rule holds: carry every
  key, group at query time. `repo_key` can serve as an informal project label for a single user in
  View A now.

---

## 2. View A — Repo/project split on the user dashboard

Reader: the developer, about their own work. Additive to S1–S4. Tone lexicon applies (no
"waste"/judgment on surfaced strings); no content in `session_info`.

| # | Insight | Question | Representation | Source |
|---|---|---|---|---|
| A1 | **Spend by repo** | "Where did my $ go?" | Horizontal bars / donut, top-N + "other", $ and % | `cost_usd` (or segment cost) GROUP BY `repo_id` |
| A2 | **Effort by repo** | "Where did my time/turns go?" | Same bars, metric toggle (cost ↔ sessions ↔ turns ↔ tokens) | session counts, `turn_count`, `tokens_total` |
| A3 | **Repo split over time** | "How did my focus shift?" | Stacked area, repo = series, weekly | sessions × `repo_id` bucketed by `started_at` |
| A4 | **Primary repo this period** | "What did I mostly work on?" | Stat tile + sparkline | argmax of A1/A2 |
| A5 | **Output by repo** | "What did I produce where?" | Bars: files changed / lines ± / PRs opened·merged | `files`, `turns[].files_changed`, `pr_list` |
| A6 | **Cost per unit output, by repo** | "Where was a change expensive?" | Bars = $ / merged PR per repo | cost ÷ `pr_list` (merged) |
| A7 | **Efficiency signals by repo** | "Which repo runs hot for me?" | Small-multiples: cache hit-ratio, long-turn rate, rework, per repo | `info.cache`, `long_turn_count`, `read_then_discard`, `practices`, grouped |

In `otel_only`, this band renders `unavailable` (no `info.repo`).

---

## 3. View B — the tenant Repository view

Reader: a lead / admin, across all users and sessions. Requires canonical-provenance `repo_id`
(`pr_link`/`remote`) to reconcile a repo across developers; `path`-only repos are folded or badged
low-confidence.

### 3.1 Repository directory (the landing grid)

Sortable table, one row per `repo_id`:

| Column | Form | Source |
|---|---|---|
| Repo (+ provenance chip) | name + confidence badge | `repo_id`, `repo_id_provenance` |
| Total cost (period) | $ + trend sparkline | `SUM(cost_usd)` |
| Contributors | avatar stack / count | distinct `user_id` |
| Sessions | count | sessions |
| Output | PRs merged / lines ± | `pr_list`, line counts |
| $ / merged PR | $ | cost ÷ merged PRs |
| Task mix | mini stacked bar | `session_type` / `pr_class` |
| Last activity | relative time | `MAX(last_event_at)` |

### 3.2 Per-repo drill-down — insight inventory

| # | Insight | Question | Representation | Source |
|---|---|---|---|---|
| B1 | **Cost over time** | "What does this repo cost, trending?" | Line/area weekly, PR-merge markers overlaid | `cost_usd` bucketed |
| B2 | **Cost attribution per user** | "Who spends into this repo?" | Stacked bar (repo cost × user) / treemap | `cost_usd` GROUP BY `user_id` |
| B3 | **Contributor leaderboard** | "Who's active here, at what cost/output?" | Table: user × cost × PRs × $/PR × task mix | join sessions↔`pr_list` |
| B4 | **Cost of a feature** | "What did feature X cost end-to-end?" | Feature cards: $ + sessions + PRs + timeline | §4 |
| B5 | **Cost by task class** | "Debug vs implement vs research here" | Stacked bar by `session_type` / `pr_class` | `session_type`, `pr_class` |
| B6 | **Output vs spend** | "Are we getting value here?" | Scatter: $ (x) vs merged PRs / lines (y); point = user or week | cost + `pr_list` stats |
| B7 | **Model mix by repo** | "What models does this repo pull?" | Stacked bar of tokens/$ by model | `models` grouped |
| B8 | **Efficiency signals by repo** | "Rework / cache / long-turn vs org median" | Small-multiples vs org baseline | per-session signals grouped |
| B9 | **Before/after agents baseline** | "Did agents change this repo's velocity?" | Dual timeline: PR velocity pre/post first session | GitHub list-backfill + sessions |

B9 uses the **cheap list-pass backfill** for a long PR-velocity baseline *before* any session
existed — the highest-value single input to an ROI story, at almost no acquisition cost.

### 3.3 Tenant overview (above the directory)

Same primitives, `GROUP BY` relaxed: total spend across repos (treemap, area = $), spend
concentration (are 80% of dollars in 3 repos?), org $/merged-PR trend, cross-repo contributor flow.

---

## 4. "Cost of implementing a feature" — the flagship composite

A "feature" is not a first-class object; it is assembled — but every ingredient exists:

1. **Boundary key (the one open product call).** Best→worst: linked issue/ticket → PR (or PR-cluster
   on one branch) → `git_branch` → label/milestone → a run of same-`session_type` sessions. The
   github note already argues for carrying labels/milestone/linked-issue refs so this is a query, not
   a re-backfill.
2. **Gather sessions** whose `pr_list` (or branch) falls in the boundary.
3. **Sum economics:** `cost_usd`, tokens, sessions, calendar span, contributors, `session_type` mix,
   rework signal, merged/rejected PR outcome (`pr_class`).
4. **Represent** as a *feature card / timeline*: total $, a horizontal timeline of contributing
   sessions and PR events, model/task mix, "$ per merged PR."

The only decision is step 1's key priority. Everything else is composition over data in hand.

---

## 5. Build order (by value & effort, not by data-gating)

Nothing here is gated on capture — this is a value/complexity ordering.

- **Phase 1 — View A repo band (A1–A4) + View B directory (§3.1) + B1/B2/B7.** The 80/20: repo split
  for users, the repository grid + cost-over-time + per-user attribution for leads. Pure aggregation
  over existing `session_info`.
- **Phase 2 — Output & efficiency (A5–A7, B3, B5, B6, B8).** Bring in `pr_list`, `session_type`,
  `pr_class`, line counts. Turns spend into value.
- **Phase 3 — Feature economics + baseline (B4 §4, B9).** The differentiators; B4 needs the boundary-
  key decision, B9 needs the list-backfill wired.

Each phase is independently shippable and degrades by datasource mode (repo band → `unavailable` in
`otel_only`).

---

## 6. Open questions for design

1. **Cost grain for headline numbers:** session-level (primary repo) vs segment-level (exact). Recommend
   session-level tiles, segment-level backing for roll-ups (§1.3).
2. **`path`-provenance repos in View B:** fold, badge low-confidence, or exclude? (Recommend badge +
   allow group-by only within a single user.)
3. **Feature boundary key priority** (§4 step 1) — the one genuinely open product call.
4. **Privacy on View B.** Per-user cost-per-repo is exactly the "snooping" friction the ProductSpec
   Open Question flags. Lead-only, or aggregate-by-default with opt-in detail?
5. **Subagent double-count:** repo roll-ups must use the inclusive-token / root-session rule already
   settled for S1 so a repo total doesn't over-count subagent sessions (they share `user_id`).
6. **Project grouping:** configured repo→project map vs derive from labels/topics. Recommend query-time
   over carried keys; defer the map.

---

## 7. Bottom line

- **The data model already did the hard part.** Repo identity with provenance, multi-repo ranking,
  per-segment cost, and PR/work classification are all in `session_info` post-merge. This is a
  presentation-and-aggregation effort, not a pipeline one.
- **Hold two disciplines:** `repo_id` (not `repo_key`) is the only tenant join key, and provenance
  confidence must be *shown*, not flattened.
- **"Cost of a feature" is the flagship** and is fully supported once the boundary key is chosen.
- Everything stays on the established rails: one cost source (`cost_usd`), no content in
  `session_info`, tone lexicon on surfaced strings, per-datasource-mode graceful degradation.
