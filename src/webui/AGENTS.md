# Talium dashboard — front-end

React 19 + Vite + Tailwind CSS v4 + Recharts. This is the **manager/tenant dashboard** and the
long-term home of the whole Talium dashboard UI. It is the served application: `vite build` →
`dist/` → FastAPI `StaticFiles`, mounted after `/v1/*` (`server/api/app.py`, `config.webui_dist`).
The vanilla SPA that used to live at `src/server/api/static` was **retired** (MVP plan U0); its
single-user depth (sessions, story, insights) re-lands here as those screens are wired.

Relocated from `docs/design/mockups/figma/` on branch `feat/mvp-implementation-#52` (U0).
De-Figma'd: the Figma Make plugins, `.figma/`, and `site.json` are removed; it builds as a normal
Vite app.

**Plan of record:** `docs/plan/2026-09-09-mvp-backend-implementation.md` (Track U). The endpoint
contract is **backend-owned** — Pydantic models in `server/api/schemas/` → `openapi.json` →
generated TS. Do not hand-write request/response types.

## Run

    npm install
    npm run dev        # http://localhost:5173
    npm run build      # -> dist/
    npm run typecheck

Node 22+ (see `.nvmrc`); npm is the package manager.

## Structure

- `src/index.css` — design tokens (CSS vars + Tailwind v4 `@theme`), light/dark, fonts
  (Inter body and section/tile titles at 13px base with tabular figures, JetBrains Mono for
  `.font-mono` numbers and chips, DM Serif Display ONLY for headline sentences and the "Talium"
  wordmark; non-sentence page titles and big values are Inter semibold), and the component classes
  (`hero-tile`, `chip`, `ask-box`, `answer-card`, `pinned-answer`, `progress-bar`).
- `src/components/ui.tsx` — the primitive library (HeroTile, Chip, StatStrip, SectionHeading,
  WorthALook, ProgressBar, Avatar, QualityChip, and the Overview v2 set: Card, Metric, Pill,
  SpendGauge, ModelDonut, TaskUsageBars, LinkButton, …). **Reuse these; extend here, don't fork.**
  HeroTile, StatStrip and Divider are no longer used by the Overview but the other screens still use them.
- `src/components/Block.tsx` — the **five-state wrapper** (U3). Every widget renders through
  `<Block state={…}>`; it picks loading / empty / cold-start / unavailable / error from the
  envelope. `resolveBlockState` is the pure, tested core.
- `src/components/Sidebar.tsx` — nav; `showManagerNav` role-gates the manager tabs (U4/F2).
- `src/components/AskBox.tsx` — the ask loop. **Out of scope** for this build (Track K, separate
  plan). The slot stays in the shell but unwired (U4); do not build against it here.
- `src/present/` — the **presentation layer** (U2): `fonts` (`MONO_FONT`, the mono stack for chart ticks
  and inline styles; a test keeps it equal to `.font-mono`), `format` (durations, relative time, money),
  `palette` (`categoryHue` — the API sends labels, the client picks the hue; `agentName` /
  `assignAgentHues` for agents), `routing` (`entityRoute` from a typed `{kind,id}` ref), `lexicon`
  (the tone gate), `overview` (the Overview's derived numbers, see "Overview v2").
- `src/shell/` — `timeframe` (the app-level `TimeframeSelection` → `{from,to}`: `TimeframeProvider`,
  `TimeframeGate`, `useTimeframe`), `FilterBar` (the bar under the top bar, hosting the picker),
  `TimeframePicker` (the grouped dropdown),
  `TopBar` + `crumbs` (the top bar and its breadcrumb label registry) and `viewer` (role,
  `canSeeManagerNav`) contexts. `App.tsx` wires them. See "The timeframe" and "The top bar" below.
- `src/screens/*` — one file per screen. The 12 wired screens: Overview, Usage, Economics,
  TeamsOverview, ProjectsOverview, Team, Person, Project, Feature, Session, PR, MyUsage.
- `src/data/` — **the data seam** (see below).

## The data seam (important)

Every screen must get its data from `src/data/`, never invent numbers inline.
`src/data/source.ts` is the seam (U1): a `DataSource` interface with `mock` and `api`
implementations and a `useData` hook, selected by `VITE_DATA_SOURCE` (`mock` default, `api`
fetches `/v1/*`). Types come from `src/data/api-types.ts` — **generated** from the backend's
OpenAPI schema (`utils/gen_api_types.sh`); never hand-edit it. `src/data/mock.ts` is demoted to
a **fixture that conforms to the generated types** (Storybook/tests), no longer the contract.
The dev server proxies `/v1` → FastAPI on :8090 (`vite.config.ts`).

## The timeframe (budget-cycle periods, Overview refresh Track A)

One app-wide `TimeframeSelection` (`present/timeframe.ts`): `{key, label, from, to, kind:
"budget-period" | "rolling", isCurrent, daysElapsed, daysTotal}`. There is no `Period` / `15d`
any more, and `Timeframe` (`data/source.ts`) is just `{from, to}`.

- **Budget periods** come from the server (`GET /v1/budget-cycle/periods`, `DataSource.budgetPeriods`):
  exact UTC `from`/`to`, the label and the day counters are used as sent. The server is the only
  place calendar or sprint arithmetic happens (`data/mock-cycles.ts` is a mock-only stand-in that
  lets every cycle kind be checked in the browser). The upcoming period is not offered.
- **Rolling ranges** ("Last 15 / 30 / 60 days", under "Other ranges") end at the moment they are
  picked and start N days earlier (UTC). The window touches N+1 UTC days; the label stays "Last N
  days", as the API buckets it. A custom date range is deferred (`TODO(custom range)`).
- **`TimeframeProvider`** fetches the periods once and opens on the CURRENT period; with none under
  way, or if the request fails, on rolling "Last 30 days" (the picker notes "Budget periods
  unavailable" on failure). It exposes `{selection, setSelection, timeframe, periods, cycleLabel,
  status, reloadPeriods}`. `cycleLabel` is what budgets are stored under (`monthly`, `sprint`, ...),
  and is all a `dev` viewer can know of the cycle (the cycle itself is lead/admin only).
- **`TimeframeGate`** wraps the filter bar and the screen area in `App.tsx`, not the shell: it shows a
  skeleton only until the first answer, so no rolling window is fetched first, and the shell's
  navigation state survives `reloadPeriods()` (which Configure calls after saving the cycle).
- **Stable identity rule.** Every `useData` keys on the `timeframe` object, so it must only change
  when the selection does: the provider memoises it per selection, a rolling selection is created
  in the click handler (its `to` is "now" there, never in render), and re-picking the selected key
  is a no-op. A reload deliberately replaces the selection, so everything refetches once. The
  forecast is keyed on scope only and never refetches on a timeframe change.
- **Wording.** Screens never say "this month" / "this period" / "30 days": they use
  `selection.label` or the `present/cycle.ts` helpers (`inWindow`, `windowName`, `periodNoun`,
  `amountLabel`). Dates are formatted from the ISO text, never in the browser's zone.
- **Picker layout contract** (kept by `FilterBar`, which hosts the picker; the same note is in its
  header). Below `sm` the listbox is `absolute left-4 right-4 top-full`, anchored to the nearest
  positioned ancestor, so the host must be positioned (the bar is `sticky top-12`), full width, not
  `overflow: hidden`/`auto`, with the `px-4` gutter, and nothing positioned between it and the
  picker. From `sm` the picker root is `relative` and the 280px listbox hangs right-aligned under
  the button, so the picker must sit on the right of its bar with 280px of room to its left (the
  right cluster, picker plus Export CSV, is `ml-auto` and wraps as a unit). The listbox's max
  height subtracts both bars.

**Configure's "Budget cycle" section** (`ConfigureScreen`, lead/admin): kind (month, quarter,
half-year, year, sprint, custom), sprint length / current sprint number / its start date, custom
first and last day, recurring or one-time (sprint and custom only). The draft, validation (a mirror
of the server's rules; the server's own 400/422 message is always shown, via `errorMessage` in
`data/source.ts`) and the request body live in `present/cycle.ts`. A kind change needs a confirming
click ("amounts are not converted"); a same-kind edit shows a notice; Save is off when nothing
changed. After a save it shows the server's `note`, reloads the periods (back to the current one)
and refetches the amount, whose label follows the SAVED cycle ("Budget per sprint (USD)"). Amounts
are stored per cycle label, so a kind change leaves scopes without a budget until amounts are
entered for the new cadence.

**Forecast copy.** The forecast is still by calendar month under every cycle; with a known
non-monthly cycle Economics says "Forecast is by calendar month" (`forecastCycleNote`) on the
forecast panel, its flat-rate fallback and the planner. Per-cycle forecasting is a follow-up
(`TODO(A4)` on the server).

## The top bar (Overview refresh Track D2)

`shell/TopBar.tsx`: a sticky `h-12` bar on every screen, outside `TimeframeGate` (so it works while
the periods load); the filter bar sticks right under it (`top-12`). Left to right: the mobile
hamburger and a compact "Talium" wordmark (below `md`, where the sidebar is a drawer; the drawer is
unchanged), the breadcrumb, Configure and Ask AI.

- **Breadcrumb** (`<nav aria-label="Breadcrumb">`, `aria-current="page"` on the last crumb) is the
  navigation stack, as walked: no synthesised "Teams"/"Projects" parent. `present/crumbs.ts`
  (pure, tested) maps it: Overview is the single crumb "Organization"; Usage / Economics / Teams /
  Projects / Configure sit under an "Organization" crumb that goes to Overview; My usage (a `dev`
  viewer) has none. A crumb click goes through `App.tsx`'s `navigateCrumb`: it drops the stack back
  to that entry (or resets to Overview). Over four crumbs it shows the first, "…" (click to expand)
  and the last two; below `md` only the last. The screens' own back buttons stay.
- **Entity names** are not fetched for the bar: each entity screen publishes its name once its own
  detail has loaded (`useCrumbLabel(kind, id, name)`, `shell/crumbs.tsx`); until then the crumb says
  "Team", "Person", ... (for a team or project, the `scopes()` name the Shell already fetched for
  the filter bar, `present/crumbs.ts::scopeLabels`).
- **Configure** is a button here (it used to be a gear beside the timeframe picker): same lead/admin gate (`canSeeManagerNav`),
  icon plus label from `sm`, icon-only below. On Configure itself it does nothing and is shown as active (only the last crumb carries `aria-current`).
- **Ask AI** is disabled ("Coming soon", `TODO(Track K)`). No bell, profile menu or workspace
  selector; the dark toggle stays in the sidebar.

## The filter bar (Overview refresh Track D3)

`shell/FilterBar.tsx` replaces `TimeframeStrip`: sticky under the top bar, same gutters and content
width, `flex-wrap`. Shown on the screens that show a timeframe (not Configure, not session / pr /
feature); the `TimeframeGate` placement is unchanged. Left: the date range and "day N of M" (the
day count is `sr-only` below `sm`, and the full text is the tooltip) and, for managers, the team
filter. Right: the `TimeframePicker` (functional) and, for managers, Export CSV. A `dev` viewer
sees the date text and the picker only.

- **Team filter** and **Export CSV** are inert: disabled, `title="Coming soon"`, accessible names
  "Team filter" / "Export CSV" (`TODO(team filter)`, `TODO(export)`). The select lists "All teams"
  and the `scopes()` teams. `shell/scopes.tsx` (`ScopesProvider`/`useScopes`) fetches `scopes()` once
  for managers (never for a `dev` viewer); the same result backs the breadcrumb fallback and the
  scope pickers on Economics and Configure, which no longer fetch it themselves.

## Overview v2 (Overview refresh Track C)

`screens/Overview.tsx` is the serif headline + "Review {team} →" CTA (unchanged), then rows of cards
(`gap-3`), then Worth a look and Ask (unchanged: their `SectionHeading`s stay), then the footer
"All amounts in USD". The page is one `<Block>` over `orgOverview`; every card below is a component
that lays out figures decided in `present/overview.ts` (pure, `present/overview.test.ts`).

| Row | Cards (component) | What is in it |
|---|---|---|
| KPI, `md:2 / xl:4` | Spend & budget; Teams & developers; PR performance; Tokens & models (inline in `Overview.tsx`, primitives from `ui.tsx`) | gauge (`spendGauge`: spend vs the window's budget, "$X left / over", no gauge without a budget); banner "N of M teams over budget" / "All teams on track" (`teamsBanner`) -> Teams, active teams, active developers, "across N repos"; merged PRs, "≈ X per day" (`mergedPerDay`), Cost / PR, Time to PR and Rework rate (hidden when null); model-mix donut (`modelSlices`: top 4 + "+N other") with total tokens, reasoning tokens |
| 2 | `TeamSpendCard` \| `BudgetForecastCard` | bars with a budget tick per team (`teamSpendBars`, `teamTip`, `teamLabelPlan`) and the "N teams over budget" pill; the baseline burn-up of the current calendar month through `ForecastBurnUp` (shared with Economics), runway pill + sentence, "Recoverable · could save X% · $Y · mostly {cause}" (`recoverableLine`), links to Economics and Usage |
| 3 | `DailySpendCard` | area chart over `daily_spend` (elapsed UTC days; micros -> dollars only in `dailySpendRows`), one day = a dot, none = `UnavailableNote` |
| 4 | `AgentSpendCard` \| `UnitEconomicsCard` | `agent_spend` donut + list and "Task usage" (`TaskUsageBars` over `agent_task_mix`: tabs All + per agent, share by session count; "All" = Usage's `session_types`); the E1 median / tail sentence and envelope + cone chart (`CostEnvelopeChart compact`) |
| 5 | `TeamBreakdownCard` | table in API order: Team, Developers (= `members`), Spend, Merged PRs, Budget used, Status (`teamTableRows`) |

Where every figure of the old Overview went: Spent + budget % -> Spend & budget; Teams over budget ->
the Teams banner; active teams / devs / repos -> Teams & developers; merged PRs, avg cost / PR, avg
time to PR, rework rate -> PR performance; tokens, model mix, reasoning tokens -> Tokens & models;
Recoverable spend and its cause -> the forecast card footer; agent spend -> Spend by agent; team spend
and budget -> Spend by team; the monthly bars -> the daily chart; the E1 sentence and chart ->
PR costs & forecast.

**Data rules**
- **The forecast** is fetched in `Overview` OUTSIDE the page `<Block>` (`useData(src.forecast(forecastScope(org)))`,
  keyed on the scope and a retry counter only), so a timeframe switch refetches the overview but never
  the forecast. The card shows the baseline preset for the period under way, else the first
  (`forecastPeriodIndex`); the forecast is by calendar month under every cycle, so its budget can differ
  from the gauge's, and a known non-monthly cycle adds `forecastCycleNote`. With no forecast (null,
  unavailable, cold-start, error) the card keeps its `Block` / `UnavailableNote` state and the footer
  falls back to "Budget status" from `budget_pct`. The Recoverable line is always shown.
- **`UnitEconomicsCard`** keeps its own `economics` fetch and `<Block>` keyed on the timeframe, so it can
  cold-start or fail alone.
- **Budget dollars** are not on the wire for the window: they are recovered as spend / `budget_pct`
  (`budgetMicros`, guarded for a zero or missing share), the same way the team bars always were.
  "Over budget" is the server's flag or a share above 100%.
- **Agents.** The API sends raw ids (`claude_code`), the mock sends display names; `agentName` maps both
  to one name ("Claude Code", "Codex", "Cursor"; unknown ids are title-cased; a missing id is
  "Unknown"). One colour map per card (`agentHueMap` -> `assignAgentHues`): pinned agents keep theirs,
  the others start at a hash of their name and probe to a free slot of `UNKNOWN_AGENT_RAMP` (none of them
  is `--blue`, which is `--primary`). The donut, list, tabs, bars and legend all read that one map.
- **Derived numbers live in `present/overview.ts`**, not in components; micro-USD stays integer until the
  last formatter. Nulls render as "—" or hide the field; nothing is invented.
- **Charts and a11y.** Each chart is `role="img"` (or `group` where the chart has focusable content) with
  an aria-label that summarises it; tooltips use `ChartTooltip` (`chartTooltip.tsx`, re-exported from
  `charts.tsx`). Tables scroll INSIDE their card on a phone (`TeamBreakdownCard`: a focusable labelled
  region with an edge fade). Team and agent names truncate with a `title`; the row button keeps the full name.
- **`Card` sets its own text colour** (`var(--foreground)`): the page has no text colour of its own, so
  unstyled text inside a card would be black on the dark card. A card is also deliberately NOT
  `overflow: hidden` (tooltips must be able to leave it) and is a size container (`cqi` sizing).
- **Sticky bars.** The top bar and the filter bar are sticky; `main` has `scroll-padding-top`
  (`STICKY_BARS_SCROLL_PADDING`, next to `TOP_BAR_HEIGHT_CLASS`) so a focused control scrolls clear of both.

**Left on purpose / follow-ups.** The `ForecastTip` / `DailyFanChart` / `BurnUpChart` tooltips still
use their own markup (`TODO(tooltip)` in `charts.tsx`; moving them to `ChartTooltip` would change
Economics' look). The cone chart's tooltip and the E1 median / tail lines are shared with Economics and
unchanged. The team filter and Export CSV in the filter bar and Ask AI in the top bar are inert (D2/D3).
`schemas/README.md` needed no change: Track C consumes the Track A and B responses as documented.

## Binding conventions

- **Tone lexicon (non-negotiable).** User-facing strings say **recoverable / could save / room to
  save**, never "waste", and never rank a person. Work-quality labels are exactly **Clean /
  Heavy lift / Quick & dirty / Costly rework** (see `QualityChip`). A lexicon check should gate CI.
- **One cost source.** Dollars come from one figure per scope; never compute a second cost path.
- **Tiles may carry more than one fact.** (Relaxed from the earlier "one idea per tile" rule — the
  editorial layout can hold a value + comparison + secondary stat cleanly.)
- Use double quotes for strings containing apostrophes; keep JSX tags closed; default-export
  components.

## U5 — done (Phase 1's 7 screens live)

Overview, Usage, Teams, Projects, Team, Person, and MyUsage's `myTips` all bind through
`useData` + `<Block>` to their Track A endpoint, typed against the generated `Envelope_*_`
schemas (`data/source.ts`). Where a mockup section had no backend yet in Phase 1 (Overview's
forecast), it renders through `<UnavailableNote>`, never silently dropped. (Overview's forecast is
live since then: see "Overview v2".)

**Simplifications made while wiring (see the screens for the exact TODOs):**
- Usage screen drops the old "who's using their seat in Platform" person list — A2 doesn't
  return per-person data (that's Team/A4's job).
- Person screen drops the session-history table and the static spend-breakdown donut — both
  need session-level (D1) data, out of Phase 1 scope; the habits list (X3) replaces the donut.
- MyUsage's headline numbers still read the `mock.ts` fixture (`TODO(U5-myusage)`): binding
  them to `/v1/users/{id}/overview` awaits porting that response to a typed schema (F0 ports
  each single-user endpoint as its screen is wired). `myTips` is live.
  **(Superseded 2026-09-16 — see "U5-myusage — done" below; the whole screen is live now.)**
- Teams/Projects tables sort server-side (descending only); the UI's ascending toggle
  reverses the fetched page client-side rather than adding a second API param.

## U6 — done (Project detail + Person's work_quality, Phase 2)

Project detail (`ProjectScreen.tsx`) now binds to A6 the same way; `features[]` (F4) and
`contributors[]` render live, with `<UnavailableNote>` when the repo has no GitHub App
connected (`features: null`). Person's work-quality panel needed **no code change** — U5
already wrote it as `person.work_quality ? (…) : <UnavailableNote>`, so it started rendering
real X2 output the moment A5 stopped returning `null`. Dropped from Project detail (not in
A6's `ProjectSummary`/`Feature` schemas): the "all pull requests" list and per-feature build
time/contributor names (D2/D3, Phase 3) and "avg time to PR"/"open PRs" stats.

## U7 — done (Session / PR / Feature / Economics, Phase 3)

All four bind through `useData`; PR/Feature/Economics via `<Block>` against fully F0-typed
`Envelope[…]` schemas, Session against a hand-typed `sessionDetail` dict (D1's route is still
untyped per `schemas/README.md`'s own porting table — only the `economics` block was worth
typing by hand here, `SessionScreen.tsx`'s own comment explains why). New shared helpers:
`present/lifecycle.ts::positionLifecycle` (raw-timestamp milestones → x-position + span
labels, used by all three deep-dives) and `present/palette.ts::channelTotalsToSeries` (the
raw `{carry,recache,output,input}` channel split → a coloured, chart-ready series). Dropped
from the mockup (not in the real contract, and per schemas/README's strip-rules shouldn't be):
per-model tier list, session/PR/feature colours, pre-baked lifecycle spans, and the churn-based
PR flag (deferred: it needs a rework-ratio threshold, Landing 3). Reopen count and post-open
churn are BOTH captured on the api now — `reopens` (from `git_artifact_events`, with a live
"Reopened" PR flag) and `churn_post_lines`/`rework_ratio` (from the git_commit_stats worker's
compare-API line stats). All three render `null` in the mock only because the sample data has no
reopen events or commit stats. `churn_post_usd_micros` stays `null` everywhere — commit-line
churn has no honest lines→$ conversion.

**Real bugs found while wiring this** (see the branch memory / progress tracker for detail):
`ApiSource` never sent the F2 trusted `x-talium-viewer` header at all, so every manager-gated
screen — not just the new ones — 401ed against a live backend (`VITE_VIEWER_ID`, dev-only,
`vite-env.d.ts`); `ProjectScreen`'s feature link still used the non-unique `issue_key` instead
of D3's new `id`; a duplicate-React-key warning in PR's session list; unrounded `$`/`d`
formatting in `PercentileBar` that only showed up against real (not hand-rounded mock) numbers.

**Correction (2026-09-12, `docs/plan/2026-09-12-mvp-gap-closure-plan.md` QW1):** the
"per-model tier list" line above was wrong. `session_info.models` was folded and stored the
whole time; strip-rule 1 only asks that *presentation* (colour) be stripped, the same
treatment `channels`/`workQuality`/`sessionTypes` got while keeping their data — this was a
wiring gap in `session_detail`/`pr_detail`, not a prototype-ism. Session/PR both render
"Models used" again now (`ModelUsageBars`, `ui.tsx`), token share and a `tier` category from
`aggregate.model_tier`, never a colour on the wire.

## U8 — done (responsive, a11y, lexicon CI, e2e)

Found a real, pre-existing gap while checking mobile: `Sidebar` never collapsed at any
breakpoint, so a phone-width viewport lost most of its screen to a fixed rail. `App.tsx` now
renders the persistent rail only at `md`+ and an off-canvas drawer (backdrop + Escape-to-close)
below it; screen padding moved to `p-4 sm:p-9 md:p-12`. Also fixed `LifecycleTimeline`'s label
collision (real data clusters milestones tighter than the mockup ever did) with a second-row
stagger. New `scripts/lexicon_check.mjs` (`npm run lexicon`) is the CI gate over every
front-end source file — comments stripped so the rule's own prose doesn't trip it — verified
against both a clean pass and a planted violation. The live-API e2e is the same pass U7's own
`x-talium-viewer` header fix made possible (see above); no separate one needed.

## U5-myusage — done (2026-09-16)

`MyUsage.tsx` now binds to live data: headline tiles/progress bar and the two StatStrip figures
that need it read `GET /v1/users/{id}/overview`, F0-typed for the first time (`Overview`,
`schemas/single_user.py`) — the porting-status blocker the old `TODO(U5-myusage)` was waiting
on. Two real gaps found while wiring, both closed on the backend rather than worked around:
`overview()`'s `z1` block had spend/tokens/sessions but no merged-PR count or avg-cost/PR at
all (`_z1_headline` now also takes the person's own merged-PR rows, reusing
`manager_aggregate.merged_pr_count`/`blended_cost_per_pr_usd_micros` — the same pair
`person_detail`/A5 already uses, just scoped to one user); and there is no per-user budget
scope in F1 (`budgets` is `org`/`team`/`repo` only) — "my monthly limit" reads the user's own
primary team's budget as an honest proxy (`_my_budget`), `None` (no bar rendered) when the
person has no team on record. `recoverable` (X3's total, not the full bucket breakdown -- the
donut chart stays mock, out of this task's scope) is new on the same response.

Per-repo breakdown and session history bind to `GET /v1/users/{id}/repos` /
`GET /v1/users/{id}/sessions` — both still bare dicts on the wire (`schemas/README.md`'s own
porting table), hand-typed in `data/source.ts` (`RepoRollupRow`/`SessionRow`) the same way
`SessionScreen.tsx`'s `SessionEconomics` is. Two mock-only columns dropped rather than
fabricated: per-repo **PRs** (the repo rollup has no PR count) and per-session **PRs**/
**Quality** (`_session_row_view` is single-session, not the per-scope classification A5's
`work_quality` panel uses) — same honest-simplification precedent as U5's own Person
session-history drop.

## Cost-based seats — done (2026-09-26)

Plan: `docs/plan/2026-09-26-cost-based-seat-utilization.md`. Seat utilization is measured in
**dollars**, not tokens: a seat's allowance is its team's budget per period / members, scaled to the
selected window (a whole budget period or a period-to-date of it: the full period budget;
any other window, e.g. a rolling range: prorated by days over the actual length of the period
containing it, see `src/server/api/schemas/README.md` "Budget cycle models") and prorated from the seat's start. The token allowance
concept is gone end to end (table, API field, Configure input, generator); tokens still show,
as information only. Screen changes:
- **Team:** seat usage = spend / `seat_allowance` (tooltip shows both); the chart comes from
  `present/seats.ts::seatUsageChartData`, which **drops** people with `usage_pct: null` instead of
  plotting them at 0%; status chip via `seatChip` from the API's headline `status` (Room to save › High use › Low use ›
  New seat › On track -- "On track" only when neither recoverable spend nor the seat band flags;
  bars are coloured by `usage_status`, so an over-used seat is red);
  the Recoverable column shows `recoverable_pct` under the amount, because room to save is now
  a share rule (≥10% of spend and ≥$10 per 30 days), not a flat $50.
- **Usage:** dollar bars against each team's period budget; a person in their first 14 days is
  `status: "new"` ("New seat"), never "Low use".
- **My usage:** reads `overview.seat` (replaces `_my_budget`'s whole-team-budget proxy described
  above); its wording follows the selected window ("so far in Oct 2026", the selection's label) and
  the cycle ("seat per sprint"), never "this month". The API still names the field `seat_monthly`.
- **Configure:** no token field.
- **Teams table:** `utilization_pct` removed from the API, since `budget_pct` (now window-scaled) is
  the team's utilization.
The mock's fixtures don't vary by window, so it treats every figure as one period of whatever
window is selected and uses the team's budget as the allowance; the API scales it.

## T7 - forecast (issue #85, 2026-10-09)

Plan: `docs/plan/2026-10-05-forecast-dashboard-integration.md` (§3.7, §3.8, T7). The Economics
screen's "Where the window lands" section is now `components/ForecastPanel.tsx`; at org scope
`components/BudgetPlanner.tsx` sits under it. Both read precomputed runs (`GET /v1/forecast`,
`GET /v1/forecast/plan`, typed from the generated `Envelope_BudgetForecast_` / `Envelope_BudgetPlan_`);
nothing is fitted or computed in the browser.

**What renders where**
- `present/forecast.ts` (pure, `present/forecast.test.ts`) decides every number, label, sentence
  and chart row; the components only lay them out. Dollars stay integer micro-USD to the string.
  Model outputs are compact (`$18.2k`, `fmtCompactUsd`), the budget is whole dollars (`$7,000`).
- Panel, top to bottom: preset buttons (grouped, `baseline` first and ungrouped, `aria-pressed`,
  the preset `description` is the button's `title`), headline sentences, runway sentence, month
  toggle ("This month" / "Next month", or the month names when no period is under way), burn-up
  chart, daily chart, footnote. The burn-up joins the actual line to the projection at
  `actual_to_date` (no step at `data_through`), draws the likely range (`band_low`..`band_high`),
  the budget line (`ifOverflow="extendDomain"`, otherwise Recharts drops a budget above the data),
  a marker at the median run-out date with the p10..p90 range shaded, and the baseline as a faint
  line when another preset is selected. The daily chart is the last 28 actual days, then the
  forecast after `data_through`; it has no join point on purpose (daily values, adjacent days).
- A period is "current" exactly when `forecast_from > start`. In the month-end case (data ends on
  a month's last day) no period is current: month names as labels, no actual rows, no runway.
- Planner: one row per team then project; budget | expected | plan for (the cost at
  `planning_confidence`, actuals included: `plan_for_usd_micros`) | gap (`plan_for - budget`).
  The gap header cycles none -> largest first -> smallest first -> none, inside each of the
  "Teams" / "Projects" groups, rows without a gap always last, stable. Below `sm` each row is a
  stacked card. A row expands to tokens per model (forecast days only; the current period says so).
  It opens on the first period that is not under way.

**Decisions**
- `forecast: null` (no run, or the scope is not in it), or an envelope-level `unavailable` /
  `cold-start`, renders the flat-rate section exactly as before (passed in as `fallback`).
  The plan with `data: null` + `unavailable` renders nothing (no empty box). A series with
  `status: "incomplete"` shows a one-line note instead of charts.
- The forecast is fetched at the top of `EconomicsScreen` (`useData`, keyed on the scope only),
  not inside the screen's body `Block`, which remounts when the timeframe changes; switching a
  preset or a month never makes a request. The planner fetches itself, so it does refetch when the
  timeframe changes, and on a scope switch the body Block shows its stale body once before it
  reloads, so the planner mounts twice: about two `GET /v1/forecast/plan` requests per scope
  switch in production (known and accepted; not restructured). `presetId` lives in the screen (the planner follows it), goes back to
  `baseline` on a scope change, and falls back to `baseline` when the run lacks it.
- No hooks inside a `Block` render prop (it is called conditionally): the month toggle and the
  planner's sort/expand state live in inner components (`ForecastBody`, `PlannerBody`).
- The org's forecast scope id is `ORG_SCOPE_ID` (`data/source.ts`, was private to Configure);
  `forecastScope()` maps the picker's `"org"` to it. An org whose id is not `"default"` gets
  `forecast: null` and the flat-rate fallback. The economics call still sends `"org"`.
- Sentences: the baseline says "At this pace ...", another preset names itself (`With "{label}"
  ...`); the probability sentence is dropped once the budget is used up (`exhausted`);
  `lasts` reports budget minus the median landing as "likely left".
- Accessibility: the two month toggles are `role="group"` labelled "Forecast month" / "Planner
  month"; preset rows are labelled groups whose buttons keep their visible label as the name (the
  description is only the `title`); the headline is an `aria-live="polite"` region; the gap sort
  button carries its state in its name (`gapSortLabel`), arrows `aria-hidden`.
- Legends list only the series a chart draws (`burnUpLegend` / `dailyLegend`): no "Spent so far"
  in a future month or the month-end case. Forecast token counts use `fmtPlanTokens` (adds a "1.6B"
  tier; the shared `fmtTokens` is unchanged). ISO-day helpers live in `data/isoDay.ts` so the
  fixture generator does not import from `present/`.
- Backend addendum (T6): `BudgetPlan.planning_confidence` and `PlanPeriodFigures.plan_for_usd_micros`
  (the planner could not show the figure its gap is computed from without them).

**Mock-only**
- `data/mock.ts::forecastFixture` / `planFixture` are a deterministic generator typed against the
  generated schemas. The org, the five mock teams and two projects have a run; `growth-web` does
  not, so the fallback path is exercised in mock mode. The scopes cover every runway state
  (platform runs out, growth lasts, data and infra are at risk, mobile is exhausted, the projects
  have no budget) and one incomplete series. Budgets are static: editing one on Configure does not
  move the mock forecast (the API reads the budget per request).

**Dropped / not done**
- `ForecastPreset.assumptions` (machine codes) are not worded or shown; `ForecastView.timezone`
  is unused. Configure's "suggested figure" next to each budget input (stretch) is not built.

## Pending

- Reasoning tokens on My-usage: still out (F5 wired Overview/Usage/Person's session table,
  2026-09-13) — MyUsage's `overview()` binding above doesn't yet surface `z1`'s equivalent;
  a follow-up, not blocked on anything now that the endpoint itself is live.
- Backend-side lexicon gate over API-produced strings (Track Q, not built here).
