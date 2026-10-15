# MVP Dashboard — Backend & UX-Integration Implementation Plan

**Date:** 2026-09-09 (rev. 2026-09-10: added Track U) · **Author:** Lead Architect · **Status:** Plan (pre-build)

**Goal:** deliver the manager dashboard's **12 ready screens** end to end — the read/aggregation
**backend** (served from `src/server/api/`, API surface small and scope-parameterized; REST decision:
`docs/design/` REST-vs-GraphQL ADR) **and the front-end integration** that wires the existing React
mockups to that API as the served application (Track U). The mockups exist; this plan turns them from a
mock-fed prototype into the live product.

**Visual reference + shape inventory:** the Figma-derived React app at `docs/design/mockups/figma/`.
Its `src/data/mock.ts` **type declarations** inventory what each screen needs and are the *starting
proposal* for the contract — **not** the contract itself (§1 draws the line). The app began as a
prototype; we are now building the full application, so the contract is **designed and owned on the
backend** and the mock shapes are cleaned-up input to it. Screens: `Overview`, `Usage`, `Economics`,
`TeamsOverview`, `ProjectsOverview`, `Team`, `Person`, `Project`, `Feature`, `Session`, `PR`,
`MyUsage`.

**Builds on / supersedes:** extends the shipped single-user API
(`docs/plan/2026-08-06-dashboard-api-implementation.md`) and **supersedes the manager-API sketch in
`docs/plan/2026-09-03-mvp-ux-implementation.md` Tracks A/F/U** — that plan predates the Economics,
Usage, and the Session/PR/Feature deep-dive mockups, the React 19 + Vite stack, and the
backend-owned contract, so both its API sketch and its UI track no longer match. Its Ask track (K)
still stands; **Ask/AI (K0–K4) is explicitly out of scope here and gets its own plan.** The 2026-09-08
iteration (timeframe, scope selector, overview tables, dollar threshold) is folded in.

**Backing analysis (read before the track that cites it):**
- Repo/project aggregation model: `docs/design/2026-08-28-repo-project-insights-analysis.md` (**REPO**)
- Making/acceptance cost, the quality 2×2, recoverable waterfall: `docs/design/notes/2026-08-28-efficiency-waste-signals.md` (**EFF**)
- Datasource modes + envelope contract: `docs/design/dashboard-datasource-modes.md` (**MODES**), DASH §8.1
- Tone lexicon (hard gate): `docs/design/session-storytelling.md` §2 and `figma/AGENTS.md`

---

## 0. Scope and shape of the work

Four categories, and keeping them apart is the point of this plan:

1. **Aggregation** — re-grouping `session_info` + git artifacts by a new key (team / person / repo /
   feature / time bucket). ~70% of the screens. Low risk; the data is all present.
2. **Foundations** — small new tables and a query-time identity layer the aggregation joins against
   (teams, budgets, allowances, viewer/role gate, feature boundary key). Net-new but mechanical.
3. **Net-new analysis (the moat)** — the signals a gateway can't produce: the **acceptance-cost**
   PR-lifecycle enrichment, the **quality 2×2**, the **model-fit** verdict, and the **forecasts**
   (cost-per-PR envelope, budget burn-up, cost-to-finish). This is where the real engineering and the
   real risk live, so it is isolated into Track X and pushed behind the aggregation MVP.
4. **Front-end integration (Track U)** — the 12 screens already exist as React components fed by
   `mock.ts`. This category turns them into the served app: own the presentation layer the API no
   longer supplies (formatting, color, routing — the §1 consequence), swap mock→API through a typed
   data seam, and ship all five per-block states (loading / empty / cold-start / unavailable / error)
   mode-aware. It is **not** "build the screens" — it is wire, harden, and serve them.

**Phasing promise:** every phase ships **vertically** — a screen's backend endpoint and its front-end
cutover land together, behind the generated contract, so each phase ends with a screen actually
rendering from the live API. Phase 1 lights up the structural-number screens from pure aggregation;
Phase 2 adds output/quality; Phase 3 adds the deep-dives and forecasts. No screen is blocked waiting on
the hardest analysis.

---

## 1. The contract: backend-owned, OpenAPI-generated — `mock.ts` is input, not law

The screens were built in a prototype, so `mock.ts` happens to be the first place the data shapes
were drawn. That makes it a **useful inventory of what each screen needs — not a contract.** For the
full application we invert the authority: **the backend owns the contract**, and the mock shapes are
cleaned-up *input* to it.

**Source of truth = the API's Pydantic response models.** FastAPI emits an **OpenAPI schema** from
them; the front-end's TypeScript types are **generated from that schema** in CI (`openapi-typescript`
or equivalent). The contract is then explicit, versioned, reviewed, and drift between front-end and
back-end is a build failure rather than a convention two files must remember. `mock.ts` is demoted to
a **fixture that conforms to the generated types** (handy for Storybook/tests), no longer the spec.

**The mock shapes are design input — and get cleaned up as they cross over. Prototype-isms to strip
(do NOT put these in the API):**

- **No presentation in data.** Drop `color`/`fill` (`"#1A7A4A"`, `"var(--primary)"`, `"var(--amber)"`)
  everywhere (`workQuality[]`, `channels[]`, `agentSpend`, `sessionTypes[]`). The front-end owns color;
  the API returns the *category/label*, the client maps it to a hue (provenance/quality palette is
  already pinned client-side, BLUE §7).
- **No pre-formatted display strings.** Return structured/numeric values — `buildTime:"4 days"` →
  `build_days: 4` (client formats); `activeLabel:"3h 20m"` → `active_minutes: 200`; `avgTimeToPR:"2.4d"`
  → a number + unit; `lastActive:"2h ago"` → an ISO timestamp. The client formats time and relative
  dates. (This reverses the earlier draft's "the API formats it" — wrong for a real app.)
- **No navigation hints in data.** `worthALook[].screen`/`screenParam` are routing; the API returns
  the finding + a typed entity ref (`{kind, id}`), the client decides the route.
- **Generated fakes are scaffolding, not contract.** `genEnvelope()`, `genBurn()`, `econFor()` and the
  hardcoded multipliers describe a *picture*; the real shapes they imply (a `Dist`, an envelope series,
  a burn series) are what the contract carries — see X5 for how they're actually computed.

**Contract style = screen-shaped view models** (the BFF approach; one designed payload per screen),
**not** the prototype's accidental shapes — normalize where a shape was flattened just to suit one
screen, and keep resource-ish sub-objects (a `PR`, a `Person`) consistent across the screens that
embed them.

**The envelope wraps every view model.** DASH §8.1 holds — each response carries
`datasource`/`modes_present`, every dollar its `cost_basis`/`cost_complete`, and an uncomputable block
is `null` + `{unavailable:{block,requires}}`, never `0`/`[]`. Model the envelope as a generic Pydantic
wrapper so it is declared once and shows up in the OpenAPI schema for every endpoint.

**Lexicon gate.** Surfaced strings (worth-a-look findings, habit labels, quality labels, channel
labels, flag text) pass the tone lexicon: **recoverable / could save / room to save**, never "waste";
quality is exactly **Clean / Heavy lift / Quick & dirty / Costly rework**. Extend the lexicon CI check
to API-produced strings, not just front-end literals.

---

## 2. Ground rules (inherited, binding — do not relitigate)

From the P2 API (`2026-08-06-dashboard-api-implementation.md` §0) and CLAUDE.md:

1. **`app.py` is the only file that imports FastAPI.** Service/aggregation layers are framework-free
   and unit-testable without a client.
2. **SQL lives in `config/queries/*.yaml`** via `QueryCatalog`; no inline SQL. New manager queries go
   in `config/queries/manager.yaml` (keep `dashboard.yaml` for the single-user surface).
3. **One cost source.** `session_info.cost_usd` / `cost_effective_usd_micros`. The API never
   re-derives dollars from tokens. Aggregates **resolve precedence per session, then sum** (A2
   resolver, MODES §3.3); an aggregate's reported source is a *set*.
4. **No content in `session_info`;** aggregation reads counts/costs/paths/labels only.
5. **Repo identity discipline (REPO §1.1, §1.4).** Cross-user roll-ups group on **`repo_id`** with
   canonical provenance (`pr_link`/`remote`); `path`-only repos are folded into a per-user "local"
   bucket and **never merged across users**. `repo_key` is a display label only.
6. **Subagent double-count (REPO §6.5).** Roll-ups use the inclusive-token / root-session rule already
   settled for S1 so a team/repo total doesn't double-count subagent sessions (they share `user_id`).
7. **Structured `api.request` logs** (route, duration, ids — never payload content).

---

## 3. Exists-vs-new ledger (orientation)

**Already produced — aggregation only, no capture:**

| Surface | Fields in hand |
| :-- | :-- |
| Session economics | `cost_usd`, `cost_effective_usd_micros`, `tokens_total`, `turn_count`, `long_turn_count`, `compaction_count`, `subagent_count`, `error_count`, `models`, `tools`, `files`, `skills`, `subagents`, `datasource`, `jsonl_lag_s` |
| `info.*` sub-blocks | `channel_cost{carry,recache,output,input}`, `carry`, `resume_cost`, `read_then_discard`, `errors`, `practices`, `segments[]`(+`beats.revision_loop`), `session_type`, `repo{repo_id,repo_key,cwd,git_branch,repos[]}`, `cache`, `reasoning_tokens`/`reasoning_ratio`/`reasoning_source`, `story`, `title` |
| Git / PR | `git_artifacts` (PR `stats`:additions/deletions/changed_files/commits/review_count/comment_count, `files[]`, `created_at`/`closed_at`/`merged_at`, `labels`, `milestone`, `reverts_sha`, `state`, `author`/`user_id`, `branch`, review & commit child artifacts). **Caveat:** `linked_issue` is a PR-body "Closes #N" regex only (no branch/title/commit, no entity) — see Track H; issues are **not** ingested as artifacts. |
| Classification | `pr_classification` (`change_type`, `is_revert`, `is_recall`, `rework_ratio`, `module`), `session_git_links` (session↔artifact join + confidence) |
| Identity | `users`(user_id,email,name,github_user), `repositories`(repo_id,kind,label) |
| Shipped API | envelope, precedence resolver, datasource-mode contract, single-user endpoints (overview, rhythm, sessions, session detail, insights, repos, pricing, feedback) |

**Net-new (must be built):**

| Item | Where | Track |
| :-- | :-- | :-- |
| Contract scaffolding: Pydantic response models + generic envelope wrapper, OpenAPI export, TS type generation in CI | `api/schemas.py` + CI | F0 |
| **Identity/org directory**: canonical tables (users+, identity_links, teams+hierarchy, roles) + CSV-import provider behind a sync port; IdP (SCIM/LDAP/SSO) later | `storage` + `directory/` | G |
| **User↔repo contribution** rollup on `users.info.repos[]`, derived from `session_info` + `git_artifacts` (provenance-tagged) | fold/rollup | G4 |
| **Issue ingestion**: `kind:"issue"` artifacts, `issues`/`issue_comment` mapping + backfill, robust PR↔issue linkage (GraphQL + precedence), Jira-capable ref | `ingest/git` | H |
| `teams`, `team_members`, `budgets`, `allowances` tables + config-import CLIs | storage + `cli_*` | F1 |
| Viewer identity + role gate (dev/lead/admin; team/repo ownership) | `api/` dependency | F2 |
| Canonical repo-rollup helper (`repo_id`, fold `path`→local) | `api/aggregate.py` | F3 |
| Feature boundary key = `linked_issue` (grouping at query time; decision taken) | `api/aggregate.py` | F4 |
| Reasoning-token session rollup surfaced for aggregation | fold/query | F5 |
| **Acceptance-cost enrichment**: post-open churn, review rounds, time-to-first-review, time-to-merge, reopen count, onto the PR artifact | `worker/pr_class` or sibling | X1 |
| **Quality 2×2** (Clean/Heavy lift/Quick&dirty/Costly rework = making-cost × acceptance-cost) | analysis | X2 |
| **Recoverable-spend waterfall** aggregation (per-cause buckets → dominant cause) | `api/aggregate.py` | X3 |
| **Model-fit verdict** (good/over/under per segment & session, with savings estimate) | analysis | X4 |
| **Forecasts**: cost-per-PR envelope+cone, budget burn-up, feature cost-to-finish | `api/forecast.py` | X5 |
| Manager/deep-dive endpoints (§4) | `api/app.py` + service | A/D/E |

---

## 4. Endpoint surface (small, scope-parameterized)

Timeframe is always a query param (`from`/`to`, or `period` ∈ 15d/30d/60d → resolved to a window).
Scope is a path segment or, for Economics, a query param. **~13 new endpoints** for 12 screens plus
budgets — no per-widget endpoints.

Each response is a backend-owned view model (Pydantic → OpenAPI → generated TS, §1). The type names
below reference the `mock.ts` shapes they descend from, **cleaned per §1** (no colors, no
pre-formatted strings, no routing hints).

| Screen | Endpoint | Returns (view model) | Track |
| :-- | :-- | :-- | :-- |
| Overview | `GET /v1/org/overview` | org headline + `teams[]` + `worthALook[]` + `agentSpend` + `initiativesTrend` + `monthlySpend` + budget-burn handoff | A1 |
| Usage | `GET /v1/org/usage` | `usageHeader`, `sessionTypes[]`, per-team usage-vs-allowance, seats-under-threshold, reasoning share | A2 |
| Economics | `GET /v1/economics?scope=org\|<team>\|<project>` | `Dist` cost+time, `prCostEnvelope`(+forecast cone), merged/reverted/rework, `budgetBurn` | E1 |
| Teams table | `GET /v1/teams` | `Team[]` + `teamMeta` (avgTimeToPR, reworkRate, lastActive, utilization) | A3 |
| Projects table | `GET /v1/projects` | `Project[]` (summary) + `projectMeta` | A3 |
| Team | `GET /v1/teams/{id}` | `Team` + ranked `Person[]` (status) + highlights + usage-vs-limit series + worthALook | A4 |
| Person | `GET /v1/people/{id}` | `Person` (habits, workQuality 2×2, peerCompare) + `recentPRs` + velocity series | A5 |
| Project | `GET /v1/projects/{id}` | `Project` + `features[]` + ranked contributors + highlights | A6 |
| Feature | `GET /v1/features/{issueKey}` | `FeatureDetail` (channels, PRs, contributors, lifecycle, costToFinish, benchmark) | D3 |
| Session | `GET /v1/sessions/{id}?view=economics` | `SessionDetail` (lifecycle, channels, modelFit, segments, context curve, practices) | D1 |
| PR | `GET /v1/prs/{id}` | `PRDetail` (lifecycle, reviews, channels, sessions, quality, flags) | D2 |
| MyUsage | reuse `GET /v1/users/{id}/*` (overview/rhythm/sessions) + `myTips` | A7 |
| Budgets | `GET/PUT /v1/budgets/{scope_type}/{scope_id}` | budget read/write (lead/admin) | F1 |

**Session endpoint note:** the shipped `GET /v1/sessions/{id}` returns the S4 storytelling/metrics
payload. The new mockup's `SessionDetail` is a *manager economics* view (lifecycle spans, channel $,
model-fit, segment fit). Add it as a **`?view=economics` block on the same route**, not a new route —
it reads the same `session_info` row. Same idea keeps PR/Feature as the only genuinely new deep-dive
routes.

---

## Track F — Foundations

### F0 — Contract scaffolding (the source of truth; do before any endpoint)
Establish the backend-owned contract (§1): `api/schemas.py` with Pydantic response models and a
generic `Envelope[T]` wrapper (mode fields, per-number source, `unavailable` block). Wire FastAPI's
OpenAPI export and a CI step that generates the front-end TS types from it (`openapi-typescript`) and
fails on drift. Port one shipped endpoint (e.g. `/v1/users/{id}/overview`) to a typed model first as
the reference. Document the "prototype-isms to strip" rules (§1) where the schemas live.
**Verify:** `GET /openapi.json` validates; `npm run gen:types` produces compiling TS; a planted field
rename breaks CI; the ported endpoint's response validates against its model.

### F1 — Budgets + allowances
The **teams/members/roles live in Track G** (G1). This task owns only the policy tables:
`budgets(scope_type ∈ {org,team,repo}, scope_id, period, amount_usd)`, `allowances(scope,
limit_tokens)`. Alembic migration; seed via `cli_budgets.py` (config import, no UI). Budgets
read/write via `GET/PUT /v1/budgets/{scope_type}/{scope_id}`; `scope_id` references a G1 team/repo.
**Verify:** migration applies; budget round-trips; a scope with no budget omits the `% of budget`
comparison (null, not 0); a budget on an unknown scope is rejected.

### F2 — Viewer identity + role gate
Request dependency resolving viewer `user_id` + role (dev/lead/admin) + visible teams/repos. It reads
the **canonical directory tables (Track G)** — it does *not* embed a role map of its own. AuthN (who
the viewer is) is a later SSO seam (leave the commented FastAPI-dependency seam, same posture as the
uploader); in MVP the viewer id comes from a trusted header/config. Per-person cost is **lead-scoped**,
framed as recoverable, never a stack-rank (EFF §5.5).
**Verify:** a dev hitting another team's endpoint → 403; a lead sees only their teams; PUT budget as
dev → 403; the gate resolves role/teams entirely from Track G tables.

### F3 — Canonical repo-rollup helper
`aggregate.repo_rollup_canonical(rows)` in `api/aggregate.py`: group by `info.repo.repo_id`, keep
`pr_link`/`remote` provenance for cross-user views, fold `path`-only into a per-user "local" bucket.
Segment-level exact split (`segments[].cost_usd_micros` × `repo_key`→`repo_id`) available for
mixed-session drilldowns; session-level (primary repo) for headline tiles (REPO §1.3).
**Verify:** two users' `local:api` do **not** merge; two users' same `pr_link` remote **do**; a mixed
session splits by measured segment cost.

### F4 — Feature boundary key
**Decision (closes REPO §6.3):** the feature boundary is the **resolved issue** from Track H, keyed by
its issue ref. Group features at query time by that key; resolve issue→epic via the issue's parent/
milestone (now a real entity, H1); PRs with no linked issue fall back to a single-PR "feature".
**Depends on Track H** — today `linked_issue` is a PR-body-only regex string with no entity behind it,
so this task is *blocked* until H lands and must not be built against the current field.
**Verify:** a corpus PR maps to the expected resolved issue; a feature's member PRs all share its
issue key; issue title/epic/status are present (not null) for an issue H has pulled.

### F5 — Reasoning-token rollup surfaced
`info.reasoning_tokens` is already produced by the graph fold. Expose a session total (+ `% of
tokens`) to the aggregation layer (promote to a queried field; a real column only if sort/filter
needs it). Surface at org/usage/person/session.
**Verify:** a golden session's rolled-up reasoning equals the graph's summed estimate; `0`/`None`
where nothing was reported and no estimate applies.

---

## Track G — Identity & org-directory ingestion (prerequisite, Phase 0)

**Detailed plan:** `docs/plan/2026-09-09-identity-ingestion-implementation.md` (task-level breakdown of
G1–G8). This section is the summary.

**The gap:** today `users` is telemetry-derived (`upsert_user_account_uuid` keyed on
`account_uuid`/`github_user`); there is **no roster, no teams, no roles, no hierarchy.** Every
cross-user screen (all of Track A, the role gate F2, budgets/allowances F1) needs an authoritative
directory. The design constraint the customer will impose: **start with a CSV/config import, then move
to the customer's IdP (SCIM / LDAP / Entra / Okta) without a schema rewrite.** So we model the
*directory*, not the *source*, and put a provider port in front of it.

**Separation of concerns (load-bearing):** **authN** (who the viewer is — SSO/OIDC) is deferred and is
*not* what the dashboard needs first. What it needs is the **directory + authorization** (the roster,
team membership, hierarchy, role). Conflating the two is the mistake that forces the rewrite. Build
the directory now; slot SSO behind F2's auth seam later.

### G1 — Canonical directory schema
Provider-agnostic tables the API reads, never knowing the source:
- Extend `users` with directory fields (`display_name`, `status` active/disabled, `primary_team_id`).
- `identity_links(user_id, provider, external_id, PRIMARY KEY(provider, external_id))` — the stable
  external-key map (provider ∈ `csv|github|scim|ldap|oidc`, external_id = login / DN / subject /
  employee-id). **This is the "carry the join key from day one" discipline** that lets a CSV-seeded
  user later reconcile to the IdP-provisioned record instead of duplicating.
- `teams(team_id, org_id, name, parent_team_id NULL)` — `parent_team_id` models the **hierarchy** the
  customer asked about (org → team → sub-team); roll-ups walk it as an adjacency/closure.
- `team_members(team_id, user_id, role_in_team NULL)`.
- `role_assignments(user_id, role ∈ dev|lead|admin, scope_type, scope_id)` — role is *scoped* (a lead
  of team X, an org admin), which F2 reads directly.
- Each directory row carries `source_provider` + `synced_at` so a later authoritative sync can own a
  field that a CSV seed only bootstrapped.
**Verify:** migration applies; hierarchy walk (`descendants(team_id)`) resolves; a user reachable by
two providers resolves to **one** `user_id` via `identity_links`.

### G2 — Directory-sync port + the CSV provider (v1)
A `directory/sync.py` port — `sync(records) -> upserts` — with the canonical record as its only
contract; **providers are adapters behind it** (mirrors the existing collector adapter/normalizer
posture). Ship the **CSV/config provider** now (`cli_directory_import.py`: users, teams+parents,
memberships, role assignments; dry-run + diff output). Upserts are **idempotent** and key on
`identity_links`; a **provider-precedence rule** decides who wins a field when two sources disagree
(authoritative IdP > CSV > telemetry-derived), so re-import and a future IdP sync compose instead of
clobber.
**Verify:** a CSV fixture imports to the G1 tables; re-import is a no-op diff; a row telemetry created
is **enriched, not duplicated**, when the CSV names the same `github`/`email`; precedence keeps an
IdP-owned field over a CSV re-import.

### G3 — Provider roadmap (design stubs, not built here)
Record the later providers so G1/G2 don't get re-litigated: **SCIM 2.0** push endpoint (Okta/Entra
provisioning → the same `sync()`), **LDAP/AD** scheduled directory pull, **OIDC/SAML SSO** for authN
feeding `identity_links(provider=oidc, external_id=subject)` and F2's viewer resolution. All write the
**same G1 tables through the same port** — the API never changes. No code here; a half-page
`docs/design/identity-ingestion.md` is the deliverable.
**Verify:** the design note exists and each provider maps cleanly to G1's columns (no new API-visible
shape).

### G4 — User↔repo contribution rollup (derived)
Materialize each user's repos/projects on the user row from **both** existing sources —
`session_info(user_id, repo_id)` (telemetry) and `git_artifacts(author user_id, repo_id)` (live
GitHub). Write `users.info.repos[] = [{repo_id, provenance, sources[], session_count, pr_count,
first_seen, last_seen}]` (contract: `identity-ingestion.md` §3.4). Hold the canonical-provenance
discipline (REPO §1.1/§1.4): `pr_link`/`remote` = cross-user contribution, `path` = local-only label.
Refresh by a lightweight rollup as sessions/PRs land (compute-on-read acceptable in MVP). **Not** an
association table — the reverse "contributors of repo X" stays a `GROUP BY` over the base tables.
Consumers: F2 repo scope, person/MyUsage repo split (REPO View A), project contributor lists.
**Verify:** a fixture user's `repos[]` = union of their session repos + authored-PR repos; a
`path`-tier repo is marked local and not merged across users; the reverse query still resolves from the
base tables without this list.

---

## Track H — Issue / ticket acquisition & linkage (prerequisite for feature surfaces)

**Detailed plan:** `docs/plan/2026-09-09-issue-tracker-ingestion-implementation.md` (task-level
breakdown H1–H6). This section is the summary.

**The gap:** issues are **not ingested** — `git_artifacts.kind` is `pr|commit|review|check`,
`map_github.py` has `TODO(git): map issues/issue_comment in v1.5`, and `linked_issue` is a
**PR-body-only "Closes #N" regex** with no entity behind it. So the Feature screen's `title`/`epic`/
`status` are unfillable and F4's grouping key is unreliable. This gates **only the feature surfaces**
(A6's `features[]`, D3) — Overview/Usage/Teams/Person/Economics do **not** depend on it and must not be
blocked behind it.

### H1 — Issue acquisition (GitHub first)
Add artifact `kind: "issue"` (and treat epic/milestone as its grouping parent). Map the `issues` and
`issue_comment` webhook events (the existing v1.5 TODO) and add an **issues backfill pass** alongside
the PR/commit/review passes (`backfill.py`). Pull the entity the UI needs: `number`, `title`, `state`
(open/closed), `labels`, `assignees`, `milestone`, parent/epic, timestamps. Paths/labels/counts only —
**never issue body/comment text** (storage §11, same as PRs).
**Verify:** an `issues` webhook lands an `issue` artifact; backfill pulls a repo's open+closed issues;
a golden issue has title/state/labels populated.

### H2 — Robust PR↔issue linkage
Replace the body-only regex with: GitHub GraphQL **`closingIssuesReferences`** (the authoritative link)
as the primary source, plus an extraction fallback with real precedence — **branch name → PR title →
body → commit trailers** — and validation (reject `UTF-8`, `SHA-1`, and non-project keys). Store the
link + its method/confidence, same pattern as `session_git_links`. This is the precedence F4 *claimed*
but that was never built.
**Verify:** a PR with `Closes #N` and a GraphQL reference both resolve to the same issue; a
branch-only link resolves; `SHA-1`/`UTF-8` are rejected; link method/confidence recorded.

### H3 — Tracker abstraction (Jira-capable, GitHub-first)
The mockup shows both `#N` and `PROJ-123`/epics. Model the issue ref as `(tracker, project_key,
number)` with `tracker ∈ github|jira` so the entity model and F4 grouping accommodate Jira without
rework; **ship GitHub only**, and note the **Jira connector as a later track** (OAuth + REST issue
pull + webhook) in `docs/design/issue-tracker-ingestion.md`.
**Verify:** a GitHub issue and a (fixture) Jira key both parse into the common ref shape; grouping
keys off the common shape.

---

## Track X — Net-new analysis (the moat)

### X1 — Acceptance-cost PR-lifecycle enrichment
Carry post-open lifecycle counts onto the PR artifact (EFF §3, §6): **post-open push churn** (lines
changed after `created_at` ÷ lines at open, from commit child artifacts), **review rounds**
(`changes_requested` count over review children), **time-to-first-review** and **time-to-merge**
(`merged_at`−`created_at`), **reopen count**, reopen/revert flags (revert already in
`pr_classification`). Rank churn/rounds above raw comment count; normalize comment/churn to
per-task-class × size bucket (EFF §5.1, §7.1). Written by a `pr_class` sibling pass; idempotent
re-run.
**Verify:** golden PR corpus → churn ratio, review rounds, time-to-merge match fixture timestamps;
a quiet-but-reverted PR does **not** read as clean (lagging signal carried).

### X2 — Quality 2×2 classification
The `WorkQuality` label per PR/session = making-cost (recoverable share) × acceptance-cost (churn /
rounds / reopen): **Clean / Heavy lift / Quick & dirty / Costly rework** (EFF §4.1). Axis thresholds
in config (tunable, like `dashboard.flags.*`). Aggregated into the `workQuality[]` counts the Person
screen renders and the `quality` field on PR/Feature detail.
**Verify:** a fixture PR in each quadrant lands in the right cell; per-person `workQuality` counts sum
to their classified-PR count; label strings pass the lexicon gate exactly.

### X3 — Recoverable-spend waterfall aggregation
`aggregate.recoverable(rows)`: decompose spend into the productive floor (`channel_cost.output`) +
recoverable buckets (context carry, rework/revision loops, resume overhead, read-then-discard, retry
burn, model over-sizing) mapped to existing fields (EFF §2). Return total recoverable $, % of spend,
**dominant cause named**, and the per-habit breakdown the `Habit[]` type wants (`category` ∈
context/rework/model).
**Verify:** buckets sum to ≤ `cost_usd`; dominant cause matches the largest bucket; a person's
`habits[].recoverableAmount` reconciles to their `recoverableSpend`.

### X4 — Model-fit verdict
Per-segment and per-session `fit` ∈ good/over/under and a `modelFit{verdict,note,savingsUsd}`:
compare the model tier used against the segment's `session_type`/task class and outcome (an oversized
model on a small clean task = over; a cheap model that drove a rework loop = under, EFF "Quick &
dirty" vs cost-of-cheap-model). Deterministic rules in config; `note` assembled from templates (no
LLM, no content).
**Verify:** the two golden sessions (`s1` over, `s2` under) reproduce their `modelFit` verdict and
`savingsUsd`; segment `fit` flags match.

### X5 — Forecasts
`api/forecast.py`, deterministic (no RNG): **cost-per-PR envelope** (median + p25/p75 band + p90/p99
+ widening forecast cone) from the trailing PR cost distribution per scope; **budget burn-up**
(actual-to-date + projection to period end); **feature cost-to-finish** (open PRs × recent `$/PR`
envelope → low/mid/high, with the `basis` string). Percentiles computed in Python over the scope's PR
set.
**Verify:** envelope percentiles match a fixture distribution; forecast cone widens monotonically;
cost-to-finish = openPRs × envelope bounds; burn-up actual+projected are continuous at the handoff.

---

## Track A — Aggregation endpoints (Phase-1 spine)

Shared module `api/aggregate.py` (F3/F4/X3 live here): date-ranged fetch by scope
(org/team/repo/person), canonical repo rollup, and the derived metrics reused everywhere — `$/merged
PR`, avg-cost-per-good, rework rate, vs-last-period, usage-vs-allowance %, time-to-produce, velocity
(goods/week), reasoning share. **One place, reused by every endpoint.**

- **A1 `/v1/org/overview`** — headline (spend, budget %, vs last, could-save + dominant cause),
  highlights (active teams+devs, tokens, active repos, merged PRs, $/PR, teams over **dollar** budget,
  rework), `teams[]` ranked with budget marks, `agentSpend`, `initiativesTrend`, `monthlySpend`,
  worth-a-look. **Verify:** golden matches `Overview.tsx`'s binds; teams-over-budget uses spend>budget.
- **A2 `/v1/org/usage`** — `usageHeader`, `sessionTypes[]`, teams ranked by usage-vs-allowance
  (over/healthy/under), seats under threshold, reasoning share, `SESSIONS_NO_OUTPUT_PCT`. No "token
  limit exceeded" wording (2026-09-08 D). **Verify:** over/under counts + low-seat flags match fixture.
- **A3 `/v1/teams` + `/v1/projects`** — the flat overview tables (search/sort/paginate server-side;
  scales to hundreds). Team row: name·spend·vs budget·people·PRs·$/PR·avgTimeToPR·rework·
  utilization%·lastActive. **Verify:** tables sort/paginate; a row's fields match `teamMeta`/`projectMeta`.
- **A4 `/v1/teams/{id}`** — `Team` + ranked `Person[]` (on-track/room-to-save status), highlights,
  per-person usage-vs-limit series, worth-a-look. **Verify:** "room to save" count = per-person
  recoverable threshold; low-use flags = usage% < threshold.
- **A5 `/v1/people/{id}`** — `Person` (cost decomposition via X3, `habits[]`, `workQuality[]` via X2,
  `peerCompare` = cheapest peer on similar task-mix), `recentPRs`, velocity series. Lead-scoped (F2).
  **Verify:** decomposition reconciles to `cost_usd`; velocity goods/times match PR fixture.
- **A6 `/v1/projects/{id}`** — `Project` + ranked contributors + `features[]` grouped by `linked_issue`
  (F4) + highlights (lines±, reopened PRs). **Verify:** feature costs sum to attributed repo spend.
- **A7 MyUsage** — reuse the shipped single-user endpoints; add `myTips` (from X3's per-user
  recoverable buckets, phrased through the lexicon). **Verify:** numbers match old S1; lexicon passes.

---

## Track D — Deep-dive endpoints (Phase 3)

- **D1 `/v1/sessions/{id}?view=economics`** → `SessionDetail`: lifecycle spans (build/review/rework
  from session + PR timestamps), `channels[]` (from `channel_cost` + `carry` + `reasoning` + recache),
  `modelFit` (X4), `segments[]` with `fit`, context-size curve + compaction markers (from turns/
  events), `tools`/`files`/`subagents`/`practices`. **Verify:** `s1`/`s2` goldens reproduce exactly.
- **D2 `/v1/prs/{id}`** → `PRDetail`: lifecycle, `reviews[]`, pre/post-open cost split, `channels[]`,
  contributing `sessions[]` (via `session_git_links`), `quality` (X2), `reworkRatio`/`churnPost` (X1),
  `flags[]`. **Verify:** PR `502` golden reproduces; flags pass lexicon.
- **D3 `/v1/features/{issueKey}`** → `FeatureDetail`: cumulative channels, member `prs[]`,
  `contributors[]`, lifecycle, `costToFinish` (X5), `benchmark` (cost/time percentile vs peers).
  **Verify:** feature `PLAT-214` golden; cost = Σ member-session segment cost; cost-to-finish basis
  string present.

---

## Track E — Economics endpoint (Phase 3)

- **E1 `/v1/economics?scope=`** → the `Dist` cost/time stats, `prCostEnvelope` (+forecast cone, X5),
  merged/reverted PR cost, rework ratio, `budgetBurn`, for scope ∈ org / any team / any project
  (2026-09-08 B scope selector). **Verify:** switching scope re-scales distributions + forecast; the
  org scope reconciles with A1's headline.

---

## Track U — Front-end integration (wire the mockups to the live API)

The 12 screens already exist as React 19 + Vite + Tailwind v4 + Recharts components under
`docs/design/mockups/figma/`, reading `src/data/mock.ts`. Track U turns that prototype into the served
application. **It does not build screens** — it relocates, serves, wires, and hardens them, cutting
over screen-by-screen behind the F0-generated contract. Binding conventions from `figma/AGENTS.md`
apply (reuse `ui.tsx` primitives; tone lexicon; one cost source).

### U0 — App relocation + serving
Move the app out of `docs/design/mockups/figma/` to a source home (`src/webui/`); `vite build` → the
static bundle the FastAPI app serves via the **existing A8 `StaticFiles` mount**, mounted **after**
`/v1/*` so it cannot shadow the API. Retire the vanilla SPA at `src/server/api/static/` once parity
lands (single-user depth ported — `figma/AGENTS.md`). `npm run dev` keeps proxying `/v1` → FastAPI.
Node 22 + the build wired into CI.
**Done when:** `vite build` output serves at `/` from the FastAPI app; `/v1/*` is not shadowed;
`npm run dev` + proxy work.

### U1 — Data seam + generated types + async hooks
`src/data/source.ts`: a `DataSource` interface with `mock` and `api` implementations and a `useData`
hook, **typed against the F0-generated client** (`openapi-typescript` output), selected by
`VITE_DATA_SOURCE`. Migrate screens off direct `mock.ts` imports to the hook. `mock.ts` is demoted to a
fixture that **conforms to the generated types** (Storybook/tests), no longer the contract.
**Done when:** a screen renders identically from `mock` and `api` sources; `npm run typecheck` is green
against generated types; a planted contract drift fails the build.

### U2 — Presentation layer the API no longer supplies *(the §1 consequence)*
Stripping colors, pre-formatted strings, and routing hints from the API (§1) moves that responsibility
to the client. Build it once, shared:
- **Formatters:** durations (`active_minutes` → `"3h 20m"`), relative time (`last_active_at` → `"2h
  ago"`), `tabular-nums` money/token/percent formatting.
- **Category → color mapping:** the provenance/quality palette pinned client-side (BLUE §7) — map
  `workQuality`/`channels`/`agentSpend`/`sessionTypes` category labels to hues; the API sends labels,
  never `#hex`/`var(--…)`.
- **Routing from typed entity refs:** `worthALook`/finding `{kind, id}` → a route; the API carries no
  `screen`/`screenParam`.
**Done when:** no screen reads a color or a pre-formatted string from an API response; formatters and
the color map are unit-tested; a lexicon check covers formatter output strings.

### U3 — The five-state contract, per block *(BLUE §6)*
A shared `<Block>` wrapper so **every** widget ships all five states and picks between them from the
envelope, never by guessing: **loading** (skeleton, reserves final height — no layout jump),
**empty** (in-range no data), **cold-start** (subsystem not yet computed — resolves over time),
**unavailable** (the mode state — driven by the envelope's `{unavailable:{block, requires}}`; visually
distinct; the `SourceNote` + enrollment path, MODES §5), **error** (inline, retry). Cold-start ≠
unavailable is the load-bearing distinction (BLUE §6).
**Done when:** each block renders the correct state for a fixture in each datasource mode; unavailable,
cold-start, and empty are visually distinct and never substituted.

### U4 — Global shell: timeframe, scope, role-gated nav
App-level `period` state (15d/30d/60d default 15d) → `from`/`to` on every request; the Org→Team→Project
**scope selector** on Overview's unit-economics band + the Economics screen → `scope=` (2026-09-08
A/B). Sidebar nav (My usage · Overview · Usage · Economics · Teams · Projects) with **role-gated**
manager tabs (hidden for `dev`, F2); drill via a nav stack with back-links.
**Done when:** switching timeframe/scope refetches and re-renders; a `dev` viewer sees no manager nav;
every drilldown route resolves with working back-nav.

### U5 — Phase-1 screens (aggregation): Overview · Usage · Teams · Projects · Team · Person · MyUsage
Bind each to its endpoint (A1/A2/A3/A4/A5/A7), all five states, mode-aware, visual parity with the
concept mockups. `Person` renders the recoverable decomposition + `workQuality` (the latter gated on
Phase 2). `A6 Project` renders without its `features[]` list until H lands (U6).
**Done when:** each screen renders its endpoint's golden and matches its `*-concept.html` mockup.

### U6 — Feature grouping + quality (Phase 2): Project `features[]` · Person `workQuality`
Wire the feature list on `Project` and the quality 2×2 on `Person`/PR once F4/X1/X2 land.
**Done when:** the feature list renders grouped by issue; the quality quadrant matches X2 output; both
pass the lexicon gate (Clean / Heavy lift / Quick & dirty / Costly rework, never "waste").

### U7 — Deep-dives + Economics (Phase 3): Session · PR · Feature · Economics
Bind `Session` (`?view=economics`: lifecycle spans, channels, model-fit, segment fit, context curve),
`PR` (lifecycle, reviews, channels, flags), `Feature` (channels, PRs, contributors, cost-to-finish),
and `Economics` (Dist + envelope/forecast + burn-up, scope-parameterized). These carry the densest
charts — Recharts with theme colors + budget/limit reference lines.
**Done when:** each renders its endpoint golden and matches the concept mockup; charts theme-correct in
light/dark.

### U8 — Responsive, a11y, lexicon CI, e2e
Mobile reflow (tiles → 1-col, strips wrap); keyboard/focus on nav + drills + selectors; the **lexicon
CI gate** over all front-end strings (banned words + the locked quality vocabulary); an e2e smoke per
screen against the **live API** (`VITE_DATA_SOURCE=api`).
**Done when:** a11y smoke passes; the lexicon gate fails on a planted "waste"; every screen loads
end-to-end against the running FastAPI app.

---

## Track Q — Tests, logging, mode degradation (alongside every track)

- Per-mode tests against a seeded DB in **all three datasource modes**; repo/PR/economics blocks
  render `unavailable` (not empty) in `otel_only` (REPO §6, MODES).
- Envelope conformance test across every new endpoint.
- `unavailable` vs cold-start vs empty asserted **distinctly** per block.
- Service/aggregate unit tests without a FastAPI client.
- **Contract conformance per endpoint:** the response validates against its Pydantic model (the
  source of truth, F0), and the generated TS types compile against the front-end; drift fails CI. A
  golden payload per screen doubles as the front-end fixture.
- Lexicon CI gate extended over API-produced strings.
- `api.request` structured logging; assert no payload content logged.

---

## Sequencing

```
Phase 0 (prerequisites + scaffolding — gate the vertical slices):
  BACK:  F0 contract (Pydantic → OpenAPI → TS gen)     ── gates every endpoint + U1
         G1·G2 identity/org directory (CSV provider)   ── gates F2 + all Track A
         H1·H2 issue ingestion + linkage (GitHub)      ── gates feature surfaces ONLY
         (G3, H3 are design-note stubs; no code)
  FRONT: U0 serve · U1 data seam+types · U2 presentation layer · U3 five-state · U4 shell
         (U1 waits on F0's generated types; U0/U2/U3/U4 can start immediately)

Phase 1 (vertical: aggregation screens; NOT blocked on H):
  F1·F2·F3·F5 ──► aggregate.py ──► A1 A2 A3 A4 A6* A7 ──► U5 (screens render live)
        └─ X3 (recoverable waterfall) feeds A1/A4/A5/A7
        (*A6/Project ships without features[] until H lands)

Phase 2 (vertical: output & quality):
  H ──► F4 ──► A6 features[] ;  X1 ──► X2 (quality 2×2) ──► A5 workQuality ──► U6

Phase 3 (vertical: deep-dives & forecasts):
  X4 ──► D1 ; X1 ──► D2 ; F4+X5 ──► D3 ; X5 ──► E1 ──► U7 ;  U8 (a11y/lexicon/e2e) across
```

- **First end-to-end slice = F0 + G1·G2 + F1·F2·F3 + X3 + A1 + U0·U1·U5(Overview)** — Overview renders
  from the live API over a real directory, in the served app. **Identity (G) is a hard gate: no
  cross-user screen is correct without a roster.**
- **Every phase is a vertical slice:** the endpoint and its screen cutover ship together, so each phase
  ends with real screens on live data — not a backend with no eyes on it.
- **Issue ingestion (H) gates only the feature surfaces** (A6's `features[]`, D3, F4, U6). Everything
  else ships without it.
- The front-end scaffolding (U0–U4) is one-time and front-loaded; U5–U7 are then thin per-screen
  cutovers because the screens already exist.
- Each phase ends green; nothing merges to `feat/mvp-ux` until its Verify passes (CLAUDE.md §4).

---

## Out of scope (recorded)

- **Ask / AI features** (ask bar, session chat, cohort, pins) — a separate plan; the grounded-answer
  contract (NL→whitelisted query) is its own surface and risk. Track U leaves the ask-bar slot present
  but **unwired**.
- **New screens beyond the 12** and visual redesign — Track U wires and hardens the existing mockups; it
  does not add screens or restyle. Design changes go through the mockups first.
- **IdP-backed identity (SCIM / LDAP / AD) and SSO authN** — G ships the canonical directory + the
  CSV provider; the IdP providers and OIDC/SAML sign-in are later tracks behind the same port and F2
  auth seam (design-stubbed in G3, `docs/design/identity-ingestion.md`).
- **Jira issue connector** — H ships GitHub issues and a Jira-capable ref shape (H3); the Jira pull
  (OAuth + REST + webhook) is a later track.
- **`otel_only` manager views** — degrade to `unavailable`, not built out (MODES).
- **Team/budget/directory admin UI** — config-import CLIs only in MVP.
- **Churn normalization research tail** (later-bug linkage, human-authorship discount — EFF §7) —
  ship the per-task-class × size baseline; defer the rest.
- **Non-coding-agent output abstraction** (the 2026-09-08 "F" brainstorm) — parked.
- **Insight/feature persistence, pin storage** — no dismiss/track state here.
