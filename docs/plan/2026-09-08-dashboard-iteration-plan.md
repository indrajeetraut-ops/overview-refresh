# Dashboard iteration — timeframe, scoping, nav restructure, metrics

> **FOLDED IN (2026-09-10).** Items A–E are absorbed into
> `docs/plan/2026-09-09-mvp-backend-implementation.md` (timeframe → U4, scope selector → U4/E1,
> Teams/Projects tables → A3, dollar threshold → A1/A2, time-to-produce → Track A metrics).
> Item **F** stays parked. This note is kept for the feedback rationale.

**Date:** 2026-09-08 · **Status:** Plan · **Applies to:** the React dashboard (`docs/design/mockups/figma/`).

Feedback batch turned into workstreams. A–E are build; F is a parked brainstorm.

## Answered up front
- **Avg time-to-produce a PR** is already tracked — the Economics tab carries the "Time to merge"
  distribution (avg / p50 / p95) and every PR has its lifecycle time. Work is to **surface avg
  time-to-produce as a headline metric** on Overview/Team/Project (workstream E), not to build it.
- **Team threshold = dollars** (decision): a team is "over" only when **spend > budget**. The per-user
  **token allowance stays** but is presented as **utilization / seat-efficiency** on the Usage tab —
  no "token limit exceeded" language for teams (workstream D).

---

## A. Global timeframe selector (default 15d · 30 · 60)
- Add a **slim header strip** at the top of the content area (the app has only a sidebar today) that
  carries the timeframe control. App-level `period` state, default `15d`.
- Data scales by period via a shared `periodFactor`/`periodLabel` helper (mock); maps to the real
  API's `from`/`to` params later.
- **Screens:** Overview, Usage, Economics, Teams-overview, Projects-overview, Team, Project, Person,
  My usage. *(Not Session/PR/Issue — fixed entities.)*
- **Verify:** switching 15/30/60 changes figures/chart windows on those screens.

## B. Unit-economics scope selector (Org → Team → Project)
- On **Overview's unit-economics band** and the **Economics tab**, add a **scope picker** (Org default ·
  Team · Project) that re-plots the percentile rows, the cost-per-PR envelope, and the forecast.
- **Verify:** picking a team/project re-scales the distributions + forecast; caption names the scope.

## C. Nav restructure — Teams & Projects overview tables (scales to hundreds)
- **Replace the sidebar's per-team / per-project lists** with two flat items. Sidebar becomes:
  *My usage · Overview · Usage · Economics · Teams · Projects*.
- **Teams overview screen** — searchable/sortable **table of all teams** (name · spend · vs budget ·
  people · PRs · $/PR · avg time-to-produce · rework · utilization% · last active) → Team drilldown.
- **Projects overview screen** — same pattern → Project drilldown.
- Team/Project drilldowns unchanged; only the way you reach them changes. Search/sort/pagination
  handle 100s of entities.
- **Verify:** Teams/Projects tabs open tables; a row opens the drilldown; sidebar no longer enumerates.

## D. Team threshold in dollars, not tokens
- Team over/under = **dollar budget** everywhere (Overview bars, Teams table status, status dots).
- **Reframe the Usage tab:** team token usage → **utilization %** (efficiency), drop breach wording;
  keep per-user **low-seat-use** flag. Only hard "over" is the dollar budget.
- **Verify:** no screen says "token limit exceeded"; a team is "over" only when spend > budget.

## E. Surface avg time-to-produce
- Add **avg time-to-produce a PR** as a highlight on Overview, Team, Project (beside `$/merged PR`);
  already present as the distribution on Economics.
- **Verify:** metric appears on the three views and reconciles with the Economics median.

## F. Non-coding-agent spend — 🧠 BRAINSTORM (parked, not building)
Open questions to work through before any build:
- **What "output" is** with no PR — a Cowork doc/PPTX, a resolved Jira/incident, a runbook, a research
  summary. Unit economics needs a non-PR output abstraction.
- Same drill hierarchy, or a **parallel "artifact" object** beside PR/Issue?
- **Agent-type as a first-class dimension** across all screens (the spend-by-agent donut is the seed);
  how cost/time/quality/model-fit translate to non-coding work.
- Deliverable: a short options doc for a dedicated session, not folded into this build.

---

## Sequencing
1. **C (nav restructure) + A (header + timeframe)** — shared header shell; foundational. *(starting here)*
2. **D (dollar threshold)** — alongside C.
3. **B (scope selector)** on the unit-economics sections.
4. **E (time-to-produce)** — additive, quick.
5. **F** — schedule the brainstorm.

Sits on the React app (uncommitted on `feat/mvp-ux`); every item degrades to the API's `from`/`to`/scope.
