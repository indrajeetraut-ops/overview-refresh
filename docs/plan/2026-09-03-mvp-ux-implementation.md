# MVP Manager Dashboard — UI / API Implementation Plan

**Date:** 2026-09-03 · **Author:** Lead Architect · **Status:** Plan (pre-build)
**Visual contract:** the mockups on branch `feat/mvp-ux` (`docs/design/mockups/`): `dashboard-prototype.html`
(the wired reference), `dashboard-overview / team / project-concept.html`, `ai-ask-concept.html`,
`efficiency-waste-concept.html`. **Rationale:** `docs/design/2026-08-28-repo-project-insights-analysis.md`
and `docs/design/notes/2026-08-28-efficiency-waste-signals.md`.
**Builds on:** the shipped single-user dashboard — API `docs/plan/2026-08-06-dashboard-api-implementation.md`
(`src/server/api/`) and UI `docs/plan/2026-08-06-dashboard-ui-implementation.md`
(`src/server/api/static/`). This plan **extends**, it does not replace.

---

## 0. What we are building

The manager/tenant layer on top of the existing single-user dashboard, in the new calm design
language, plus the three chosen AI features. Five screens, all one drill hierarchy:

```
Overview (org) → Team → Person        Overview (org) → Project → Feature        My usage (self)
```

Delivered as: **manager aggregation endpoints** over `session_info` + git/PR data, a **calm SPA**
extension, and an **Ask** service (grounded answers) behind the ask bar on every screen.

### Prerequisites assumed present (not built here)
- **repo_id + provenance** and **git/PR productivity data** (`pr_list`, merged/reopened, line counts,
  `pr_class` rework) — from the github-acquisition normalizer. Every "shipped / $ per merged PR /
  rework" figure depends on it. (Post-merge assumption — see the analysis doc §0.)
- **Causal graph persisted + queryable** — `session_graph`/`session_tree` tables,
  `graph_service.load`, and `analysis.causal_graph` named APIs + `query()`/`select()`. Powers Ask-2.
- **`session_info` fields** already produced: `cost_usd`, `models`, `segments[]` (`cost_usd_micros`,
  `repo_key`), `info.carry`, `info.resume_cost`, `info.read_then_discard`, `info.errors`,
  `info.practices`, `info.repo`, `pr_list`.

---

## 1. Ground rules (inherited + new)

Inherited and binding (do not relitigate):
- **One cost source:** `session_info.cost_usd` / `cost_usd_micros`. Never a second cost path.
- **Response envelope + precedence resolver + datasource modes** exactly as the P2 API established
  (`api/envelope.py`, `api/precedence.py`). Manager endpoints reuse them.
- **No content in `session_info`;** aggregation reads counts/costs/paths only.
- **Tone lexicon gate** (`static/js/lexicon.md`): surfaced strings say **recoverable / could save /
  room to save**, never "waste"; quadrant words are **Clean / Heavy lift / Quick & dirty / Costly
  rework**. Extend the lexicon CI check to the new strings.
- **Stack (revised 2026-09-06): React 19 + Vite + Tailwind v4 + Recharts.** The framework-free
  vanilla-SPA rule is retired. The front-end is the Figma-derived app currently at
  `docs/design/mockups/figma/` (design system in `src/index.css` + `src/components/ui.tsx`); it
  **supersedes** `src/server/api/static` (ported over time) and **moves to `src/` when wired into
  FastAPI serving** (`vite build` → `dist/` → StaticFiles). Data flows through `src/data/` (mock now,
  API impl swaps in — see the module's AGENTS.md). The "one idea per element" rule is relaxed: tiles
  may carry a value plus a comparison and a secondary stat when the layout stays calm.

New for the manager layer:
- **Scope guard.** Manager endpoints are cross-user; every one takes a viewer identity and is
  gated by role + team/repo ownership (§F4). Privacy posture per analysis §6 / efficiency note §5:
  per-person cost is lead-scoped, framed as recoverable, never a stack-rank.
- **Repo identity discipline.** Tenant roll-ups group on **`repo_id`** (canonical provenance
  `pr_link`/`remote`); **`path`-only repos are excluded** from cross-user totals (analysis §1.1).
- **Grounded answers.** Every Ask response carries its evidence (session count + ids) and the query
  it ran; the model never computes numbers, it selects a query and narrates the result (§K0).

---

## Track F — Foundations (data the manager views need)

### F0 — Team model + membership
Add `teams` (`team_id`, `org_id`, `name`) and `team_members` (`team_id`, `user_id`). Seed from a
config import CLI (`cli_teams.py`); no UI management in MVP.
**Verify:** migration applies; a fixture org with 2 teams × N users loads; `team_of(user_id)` resolves.

### F1 — Budgets
Add `budgets` (`scope_type` ∈ {org, team, repo}, `scope_id`, `period`, `amount_usd`). Read/write via
API (§A6). Nullable — a scope with no budget renders without the "% of budget" comparison.
**Verify:** migration; round-trip set/get; overview tile omits budget meter when null.

### F2 — Canonical repo aggregation
A read helper `repo_rollup_canonical(rows)` that groups sessions by `info.repo.repo_id`, keeps only
`pr_link`/`remote` provenance for cross-user views, and folds `path`-only into a per-user "local"
bucket. Reuse/extend the existing `_repo_rollup` in `service.py`.
**Verify:** unit test — two users' `local:api` do **not** merge; two users' `pr_link` same-remote **do**.

### F3 — Feature grouping key (linked issue)
Extract + validate the linked-issue key per PR (GitHub `#N`; Jira `PROJ-123` validated against the
project-key set) per the issue-linkage note; store `linked_issue` + provenance on the PR artifact;
resolve issue→epic where available. Group features at query time by `linked_issue`.
**Verify:** extraction unit tests (branch/title/body/commit precedence; false-positive rejection
`UTF-8`/`SHA-1`); a corpus PR maps to the expected issue.

### F4 — Viewer identity + role gate
A request dependency resolving viewer `user_id` + role (dev | lead | admin) and the teams/repos they
may see. MVP: static role map from config; real auth is a later seam.
**Verify:** a dev requesting another team's endpoint gets 403; a lead sees their teams only.

### F5 — Per-user usage allowance
Add the monthly token **allowance** (per-user limit, e.g. 512k tokens/person/month) to config; a
team's allowance = active members × limit. Powers the Usage view's over/under-utilisation and the
per-person usage-vs-limit charts. Usage itself is `SUM(tokens)` already available.
**Verify:** team allowance = members × limit; a person's usage% = tokens ÷ limit; "seats under 30%"
count matches a fixture.

### F6 — Reasoning tokens into `session_info` (fold change)
The causal graph already estimates thinking tokens per assistant group
(`analysis.causal_graph.reasoning.estimate_reasoning`, `ReasoningConfig`); it is **not** rolled up.
Add a fold rollup `reasoning_tokens` (session total, + `% of tokens`) to `session_info` via a
`budget`-style pass over the graph, so a session's reasoning spend is queryable — the "was this much
thinking necessary?" signal. Surface at org/person/session and as a first-class Ask target.
**Verify:** a golden session's `reasoning_tokens` equals the graph's summed estimate; `0`/`None`
where the vendor reported nothing and no estimate applies; refold is idempotent.

---

## Track A — Manager / tenant API (`src/server/api/`)

All endpoints: reuse the envelope, take `from`/`to`, pass the F4 scope guard, degrade per datasource
mode (repo/PR blocks render `unavailable` in `otel_only`).

### A0 — Shared aggregation module
`api/aggregate.py`: date-ranged session fetch by scope (org/team/repo/person), the recoverable-spend
decomposition (map `carry`/`resume`/`read_then_discard`/`errors`/rework/model-oversizing → buckets),
and the derived metrics reused everywhere: `$ per merged PR`, **avg cost per good produced**,
`rework rate`, `vs last period`, **usage vs allowance** (%, over/under-utilisation; F5), **time to
produce a good** (calendar span first contributing session → merge), **velocity** (goods merged per
week), and **reasoning tokens** (F6). One place, reused by every endpoint below.
**Verify:** golden aggregation over a fixture corpus; recoverable buckets sum to `≤ cost_usd`;
avg-cost-per-good = spend ÷ merged goods; time-to-good matches session/PR timestamps.

### A1 — `GET /v1/org/overview?from&to`
Headline (spend, budget %, vs last, could-save + dominant cause), highlights strip (active teams(+dev
count), tokens, active repos, merged PRs, $/merged PR, teams over budget, rework rate), teams ranked
by spend with budget marks, "worth a look" findings.
**Verify:** golden response for the fixture org; teams-over-budget count matches budgets fixture.
The Overview headline usage tile links to A1a.

### A1a — `GET /v1/org/usage?from&to`
The Usage view: org usage % of allowance, teams ranked by usage-vs-allowance (over/under), seats
under a low-use threshold, reasoning-token share. (F5 allowance + F6 reasoning.)
**Verify:** golden; over/under-limit team counts and "seats under 30%" match the fixture.

### A2 — `GET /v1/teams/{team_id}?from&to`
Headline + tiles (spent/budget, vs last, could-save), highlights (tokens, avg/person, top model,
merged PRs, $/PR, rework, busiest repo, people-with-room-to-save), people ranked by spend with
on-track/room-to-save status, **a second per-person usage-vs-limit chart flagging low-use seats**
(F5), worth-a-look.
**Verify:** golden; "room to save" count matches per-person recoverable threshold; low-use flags
match usage% < threshold.

### A3 — `GET /v1/teams/{team_id}/people/{user_id}?from&to`
Person drill: cost decomposition (produced / re-reading context / rework / other), shipped, coachable
practice reasons (from `info.practices`), a "template" peer (cheapest peer on similar task-mix), and
a **velocity series** — goods merged per week with cost + time-to-merge each (replaces the old
tokens-consumed emphasis).
**Verify:** golden; decomposition buckets reconcile to the person's `cost_usd`; velocity goods and
their times match the PR fixture.

### A4 — `GET /v1/projects/{repo_id}?from&to`
Project view: headline ("$ / merged PR — worth it"), tiles, highlights (contributors(+names), sessions,
tokens, merged PRs, top model, lines±, reopened PRs), contributors ranked, **feature list** (grouped
by `linked_issue`: cost, PR count, status), worth-a-look.
**Verify:** golden; feature costs sum to the repo's attributed spend within tolerance.

### A5 — `GET /v1/projects/{repo_id}/features/{linked_issue}?from&to`
Feature drill: cost split (producing / rework / reading / other), **time-to-build** (first work →
merge/now), status (n of m PRs merged), contributors, the PR list with status
(merged/reopened/in review).
**Verify:** golden; PR statuses match the git-source fixture; cost = Σ segment cost of member
sessions; time-to-build matches first-session → merge timestamps.

### A6 — Budgets read/write
`GET/PUT /v1/budgets/{scope_type}/{scope_id}`. Lead/admin only.
**Verify:** set a team budget → A1/A2 reflect it; dev role is 403 on PUT.

### A7 — Tests, logging, degradation
Per-mode tests (`otel_only` → repo/PR blocks `unavailable`), structured `api.request` logging already
in `app.py`, and an API-level golden per screen (analysis lists the fields).
**Verify:** the full manager suite green; mode matrix covered.

---

## Track K — Ask (the three AI features)

### K0 — Grounded-answer contract
`api/ask.py` + `POST /v1/ask`. Request: `{question | template_id, scope, params}`. Response envelope:
`{answer_text, viz?, evidence:{session_count, session_ids, people}, query_shown, followups[], pin?}`.
Rule: the model maps NL → a **whitelisted** structured query (never free SQL/code), the engine runs
it, the model narrates the numeric result. Start with a **bounded template library**; open-ended NL
is gated behind it.
**Verify:** a template answer is byte-reproducible; `evidence.session_ids` are real; no template
resolves to an unwhitelisted field.

### K1 — Ask about a number (idea 1)
Templates over the A0 aggregation surface (e.g. "why did {scope} jump", "who reworked most in
{team}", "top N sessions for {person}"). NL→template classifier with the field registry as the safe
target; `query_shown` renders the plain steps.
**Verify:** the mockup's Overview/Team/Project questions each return a grounded answer with evidence.

### K2 — Chat with one session (idea 2)
`POST /v1/sessions/{id}/ask`. Load the family via `graph_service`, translate the question to
`named_api`/`select()` calls, narrate. Multi-turn context per session. Answers tagged "from this
session's map" and cite turns/files.
**Verify:** "why did this cost $X" returns the graph's cost decomposition; a follow-up on rework
names the actual turn range from `segments[].beats.revision_loop`.

### K3 — Ask across a group (idea 3a)
Cohort selector (by team/repo/person/period or a saved filter) → aggregate templates over
`session_info` (`GROUP BY` on already-materialized fields: task class, practices, model, repo). No
graph fan-out in MVP (that is 3b, out of scope). Answers list shared traits with share (`8 of 10`).
**Verify:** "what do my 10 most expensive sessions have in common" returns grounded shares whose
denominators equal the cohort size.

### K4 — Pin
`POST /v1/pins` stores `{viewer, scope, template_id, params, title}`; pinned cards re-run their query
each load. Rendered on the owner's Overview.
**Verify:** pin from an answer → appears on Overview → value updates when the fixture changes.

---

## Track U — React front-end (the Figma-derived app; see its AGENTS.md)

The screens, sidebar, AskBox, and design system already exist in the app. Track U is now
*hardening and completing* it, not building from scratch. Sidebar nav (Org / Teams-with-status-dots /
Projects) replaces the top-tab shell; drill via a nav stack; charts via Recharts with theme colors +
budget/limit reference lines.

### U0 — Design system lock + data seam + lexicon gate
Keep `src/index.css` tokens and `src/components/ui.tsx` primitives as the source of truth (extend,
don't fork); add a thin theme-aware Recharts wrapper. Establish `src/data/` as the only data source
(mock now, API impl later). Add a **lexicon check** (banned words + the locked quality vocabulary)
to CI.
**Verify:** `npm run build` + `typecheck` green; the lexicon check fails on a planted "waste".

### U1 — Shell, nav, router
Add manager nav (My usage / Overview / Teams / Projects) and routes for team/person/project/feature
drills to `router.mjs`; back-links; role-gated nav (hide manager tabs for devs).
**Verify:** every route resolves; drilling Overview→Team→Person and Project→Feature works with back.

### U2 — Overview screen (`screens/org-overview.mjs`)
Headline + action, ask panel, hero tiles, highlights strip, teams ranked (click → team), worth-a-look.
Bind to A1. **Verify:** renders the A1 golden; matches `dashboard-overview-concept.html`.
The Overview headline **usage tile** links to the Usage screen.

### U2a — Usage screen (`screens/usage.mjs`)
Headline (usage % of allowance, over/under teams), tiles, usage-vs-allowance bars (over/healthy/
under-using, allowance mark), reasoning-token share, worth-a-look. Bind A1a. Uses the new
`usage-bar` component (percent value, limit mark, low-use flag).
**Verify:** matches the prototype's Usage view; over/under counts equal A1a.

### U3 — Team + Person (`screens/team.mjs`, `screens/person.mjs`)
Team: people ranked (status colours), highlights, worth-a-look → person. Person: cost decomposition
flow bar, practice reasons, template card. Bind A2/A3.
**Verify:** renders goldens; person drill reachable from people rows and worth-a-look.

### U4 — Project + Feature (`screens/project.mjs`, `screens/feature.mjs`)
Project: contributors ranked, **feature list** → feature. Feature: cost split, who worked on it, PR
list. Bind A4/A5. **Verify:** renders goldens; feature drill reachable.

### U5 — My usage (simplify existing S1)
Recast the existing overview screen to the calm skeleton (headline + tiles + highlights + repo split
+ gentle self-tips + ask); keep the rhythm data available but demoted. Bind to existing `/users/{id}`
endpoints. **Verify:** parity of numbers with the old S1; lexicon gate passes.

### U6 — Ask panel (wire K)
The ask bar → answer card (grounded: evidence + "show how this was worked out") → follow-up chips →
Pin. Session-scoped chat variant for U-session-detail (K2). Bind K0–K4.
**Verify:** the prototype's ask→answer→pin loop works against the live API on each screen.

### U7 — Responsive, a11y, lexicon gate
Mobile reflow (tiles → 1-col, strips wrap), keyboard/focus on ask + drills, and the lexicon CI gate
over all new strings.
**Verify:** a11y smoke pass; lexicon check green; mobile screenshots match.

---

## Sequencing

1. **F0–F2, F4** (teams, budgets, canonical repo, scope guard) — nothing manager-side works without them.
2. **A0** then **A1–A2** in parallel with **U0–U1** (design language + shell).
3. **U2–U3** (Overview, Team, Person) against A1–A3 → first end-to-end manager slice.
4. **A4–A5 + F3 + U4** (Project, Feature) — the git/PR + linked-issue dependency lands here.
5. **U5** My usage recast (independent; can slot anytime after U0).
6. **K0–K1 + U6** (Ask-1 + pin) → then **K2** (session chat) → **K3** (cohort).
7. **A6 budgets UI, U7 polish.**

Each phase ends green; nothing merges to `feat/mvp-ux` until its verify passes.

---

## Out of scope (recorded)

- **Ask-3b** (deep cross-graph contrast, "what do clean sessions do that costly ones don't") — needs
  graph fan-out + a features-per-session table; a later plan.
- **Alert delivery** (email/Slack when trending over budget) — the data exists; delivery is separate.
- **Full Jira/GitHub two-way sync** — MVP only reads the linked-issue key.
- **Real auth / RBAC** — MVP uses a static role map (F4); SSO is a later seam.
- **`otel_only` manager views** — degrade to `unavailable`, not built out.
- **Team/budget admin UI** — MVP imports via CLI.
