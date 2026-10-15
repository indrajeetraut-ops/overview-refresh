# Overview refresh: budget-cycle timeframes, daily spend, new shell and Overview layout

Date: 2026-10-14 · Status: **IMPLEMENTED on `feat/overview-refresh` (not pushed); step-by-step review log: `2026-10-14-overview-refresh-progress.md`** · Scope: `src/webui`,
`src/server/api`, `src/server/storage` (Alembic), `config/queries`

The reference UX is `/Users/anil/workspace/Talium/talium-lovable-1.0`, a Lovable prototype on
sample data. It is a source of **ideas only**; its decisions are not binding. Nothing on today's
UI is removed or moved without the user's sign-off (decision log below).

## 0. Decision log (user, 2026-10-14)

| Topic | Decision |
|---|---|
| Serif headline + "Review {team} →" CTA | **Stays** |
| Timeframes | **Budget-cycle periods** replace 15/30/60d. The app opens on the current period. Not cosmetic (Track A) |
| Spend over time | **Backend daily series** (Track B). Replaces the monthly bars |
| Forecast | **One forecast chart on Overview** from `GET /v1/forecast`. The detail stays on Economics |
| "Shipping vs new work" | **Removed**. The API stops computing `initiatives_trend` |
| "Worth a look", Ask slot | **Unchanged** |
| Hero tiles, StatStrip | Replaced by four KPI cards. Every figure moves into a card |
| Spend-by-team budget lines | Per-bar budget ticks |
| Section dividers | Card rows |
| Monthly spend bars | Replaced by the daily chart |
| Agent task usage | **Build it** from the session-type breakdown the Usage screen already shows, split per agent (Track B) |
| Shell (breadcrumb topbar, Ask AI, team filter, Export CSV) | **Build the UI now, inert**. Made functional later |
| Footer "All amounts in USD" | Add |
| "See how to save" | Links to Usage. No "Best performer" pill |
| Budget for a period-to-date window | Compare with the **full** period budget |
| Cycle-kind change | Existing amounts are not converted. The admin re-enters them for the new cadence |
| "Other ranges" in the picker | Keep rolling 15/30/60. Defer the custom date range |
| Forecast with a non-monthly cycle | Allow all kinds. The forecast cards say "Forecast is by calendar month". Per-cycle forecasting is a follow-up |
| Fonts | **Inter + JetBrains Mono** (the new fonts). DM Serif Display is kept for headline sentences |
| Daily chart in the current period | **Elapsed days only** |

---

## 1. Reference analysis (summary)

- **Overview:** `src/components/analytics-page.tsx`. **Charts:** `overview-spend-charts.tsx`,
  `pr-cost-forecast-chart.tsx`, `analytics-charts.tsx`, `token-usage-chart.tsx`.
  **Tooltip:** `chart-info-tooltip.tsx`. **Shell:** `app-shell.tsx`.
- **Budget cycles:**
  - `src/lib/budget-cycles.ts`: kinds sprint (N weeks, anchored on the current sprint number
    and its start date), month, quarter, half, year and custom; recurring or one-time.
    `getCyclePeriods` returns the last 4 periods that have started, plus the next one.
  - `time-range-select.tsx`: the periods grouped per cycle, then "Other ranges".
  - `budget-cycles-panel.tsx`: the configure UI.
- **Reference gaps we won't copy:**
  - Several cycles but a single budget amount, so the amount's cadence is undefined.
  - It opens on "Last 60 days".
  - Spend is scaled by `days/60` but compared with a fixed budget.
  - Its "projected" status is really the current utilisation.
  - Agent task usage is illustrative sample data.
- **Design language:** Inter + JetBrains Mono, 13px base, 10px uppercase card labels inside the
  card, `p-3` cards and `gap-3`/`mt-4` rows, grouped KPI cards, status pills, zero-delay
  label/value tooltips. The palette is almost identical to ours.

## 2. Where code-econ is today

- **Timeframe:** `shell/timeframe.tsx` has a fixed `Period = "15d"|"30d"|"60d"` (default 15d)
  and resolves it to a rolling `{from, to}`. `aggregate.resolve_timeframe` already accepts an
  explicit `from`/`to`.
- **Budgets:**
  - `budgets(scope_type, scope_id, period, amount_usd)`; `period` is always `"monthly"`
    (`config.budget_period`). `budgets.period_bounds` supports only monthly.
  - `aggregate.window_budget_usd_micros` scales budgets by `window_days / 30`. It feeds
    `budget_pct`, `over_budget` and seat allowances on every manager endpoint.
  - Configure edits one "Monthly budget" per scope.
- **Forecast:** `econ-forecast` runs on calendar weeks or months. `/v1/forecast` periods are
  months.
- **Overview API (`manager/service.py::org_overview`):**
  - `monthly_spend` comes from SQL `scope_monthly_spend`. It reads raw
    `cost_effective_usd_micros`, not the precedence chain the headline spend uses.
  - `agent_spend` (`aggregate.spend_by_agent`) also sums raw `cost_effective_usd_micros`, so
    the donut total can differ from the headline spend.
  - `initiatives_trend` costs two weekly issue queries per load.
- **Task breakdown:** Usage shows `session_types` (`aggregate.session_type_counts`: each root
  session's dominant segment label, counted), named through `present/palette.ts`
  (`sessionTypeName`: Develop, Fix, Review, Comprehend, Plan, Design, Ops, Misc).
  `scope_session_facts` rows already carry both `agent` and `session_type`.

---

## 3. Track A — Budget cycles and cycle-aligned timeframes

### A1. Storage and model
- New table `budget_cycle`, one row per org.
  - Columns: `kind` (`month|quarter|half|year|sprint|custom`), `sprint_weeks`, `anchor_date`,
    `anchor_number`, `custom_end`, `recurrence`, `updated_at`.
  - SQLAlchemy model in `server/storage/schema/`, Alembic revision in
    `server/storage/dbm/alembic/versions/`, following `budgets.py` /
    `8e59d8946ba4_budgets_allowances.py`. Seed it in `synthetic_load.py` if budgets are seeded
    there.
  - With no row the cycle is `month`, so today's behaviour holds.
- Budget amounts are always "per period of the org's cycle". `budgets.period` stores the cycle
  label (`monthly`, `quarterly`, `sprint`, …).
  - When the kind changes, rows under the old label stay stored but unused. Scopes show
    "no budget" until amounts are entered for the new cadence.
  - Configure says this before saving the change.
- `server/api/manager/budget_cycles.py` (framework-free, like `budgets.py`):
  - `periods(cycle, today, history)` → `[{key, label, start, end_exclusive, is_current,
    days_total, days_elapsed}]` plus `next`, in UTC.
  - Port the reference's arithmetic and tests.
  - Generalise `budgets.period_bounds` to any cycle.

### A2. API
- `GET /v1/budget-cycle` and `PUT /v1/budget-cycle` (lead/admin, same gate as budgets).
- `GET /v1/budget-cycle/periods?history=6` returns the periods with exact ISO `from`/`to`. The
  server is the only place calendar or sprint arithmetic happens.
- Window budget, in `window_budget_usd_micros` and `seat_allowance_usd_micros`:
  - When `[from, to)` is a whole period of the cycle, or a period-to-date of it, the budget is
    the **full period budget**. Seat allowance = team period budget / members, still prorated
    from a seat's start date.
  - Any other window (rolling): prorated by days over the cycle's **actual** period length,
    replacing the fixed `BUDGET_PERIOD_DAYS = 30`.
  - Consequence (accepted): early in a period, few teams and seats read as over budget or high
    use. Pace is the forecast card's job.
- `period=` / `PERIOD_DAYS` stay accepted for compatibility.
- Pydantic → `openapi.json` → `npm run gen:types`.

### A3. Front end
- `shell/timeframe.tsx`:
  - `Period` becomes `TimeframeSelection {key, label, from, to, kind: "budget-period" |
    "rolling", isCurrent, daysElapsed, daysTotal}`.
  - The provider fetches the periods once and defaults to the current period. Screens wait for
    it, so no rolling window flashes first.
- Picker (lives in the new filter bar, Track D): "Budget periods" (dates, with "· current"
  marked) and "Other ranges" (last 15/30/60 days).
  - TODO in code: custom date range, deferred.
- Audit every user-facing "this period / this month / last N days" string, and every
  `period`/`PERIODS` use. Each one changes to read the selection's label. Files: MyUsage,
  Usage ("share of the monthly budget"), Team, Person, Teams/Projects tables, Economics,
  `present/seats.ts`, `mock-source.ts` (its 30-day assumption).
- `ConfigureScreen`:
  - New "Budget cycle" section: kind, sprint length, current sprint number and start date,
    custom start/end, recurring or one-time.
  - The amount label follows the cycle ("Budget per sprint (USD)").
- Mock: a fixture cycle (monthly) and fixture periods.

### A4. Forecast copy
- With a non-monthly cycle, the Overview forecast card and Economics' forecast show "Forecast
  is by calendar month".
- TODO in code: run `econ-forecast` per org cycle (follow-up).

### A5. Tests
- pytest:
  - period generation for every kind, plus edges: sprint numbering, quarter rollover,
    one-time custom, today before the anchor;
  - full-period vs prorated window budget;
  - `budget_pct`/`over_budget` for a period-to-date window on Overview, Teams and Team;
  - seat allowance;
  - the routes and their gate.
- vitest: selection → `{from, to}`, the default selection, the picker's grouping.

---

## 4. Track B — Overview API changes (backend)

**B1. `daily_spend`**
- New field `daily_spend: [{day: date, spend_usd_micros: int}]` on `OrgOverview`, covering the
  window's **elapsed days only**, with missing days filled as 0.
- Computed from the same `rows` and precedence chain (`precedence.resolve_aggregate`) as
  `headline.spend`.
- A session is bucketed on the UTC day of `max(started_at, from)`.
- pytest: `sum(daily_spend) == headline.spend.usd_micros`.
  - If the precedence resolver can't be split per row additively, stop and come back to the
    user.
- Remove `monthly_spend` and `scope_monthly_spend`.

**B2. `agent_task_mix`**
- New field `agent_task_mix: [{agent, type, count}]`: root sessions per (agent, dominant
  session type).
- Computed from the same `rows` as `session_type_counts`, so "All" on Overview matches Usage's
  breakdown for the same window.
- Share by **session count**, matching Usage. Spend per type is not attempted.
- Rows with no `session_type` are omitted, same rule as `session_type_counts`.

**B3. `agent_spend` on the precedence chain**
- Group `rows` by agent and resolve each group's cost through the same chain as the headline,
  so the donut total equals the headline spend.
- This fixes an existing one-cost-source gap. pytest: `sum(agent_spend) == headline.spend`.

**B4. Drop `initiatives_trend`**
- Remove the field and schema, `aggregate.initiatives_trend`, and the `issues_started_weekly` /
  `issues_shipped_weekly` calls from `org_overview`.
- First grep for any other caller of those queries; delete the queries only if none.
- Drop `InitiativesTrend` from `charts.tsx` and the mock.

**B5. Housekeeping**
- Regenerate `openapi.json` and `api-types.ts`.
- `mock-source.ts`: a deterministic daily series that sums to the mock headline, plus an agent
  task mix.
- Update `schemas/manager.py` docstrings.

---

## 5. Track C — Overview layout

`useData` + `<Block>` throughout. Recharts stays. No new runtime dependencies: inline SVG icons.
Derived display numbers live in a pure, tested `present/overview.ts`.

1. **Serif headline + CTA** (unchanged). The wording follows the selection's label
   ("Sprint 233 so far", "October so far").
2. **KPI row**, `md:2 / xl:4`:
   - **Spend & budget:** semicircle gauge (spend vs period budget, `% of $budget`), with a
     "$X left" or "$X over" pill. The budget in dollars is spend ÷ `budget_pct`. With no
     budget: spend only.
   - **Teams & developers:** banner "N of M teams over budget" or "All teams on track"
     (→ Teams), then Active teams, Active developers, "across N repos".
   - **PR performance:** merged PRs (large), "≈ X per day" (over elapsed days), then Cost / PR,
     Time to PR, Rework rate.
   - **Tokens & models:** model-mix donut with total tokens in the middle, reasoning tokens and
     their %, and a legend (`categoryHue("modelTier")`).
3. **Row 2: Spend by team | Budget forecast**
   - **Spend by team:** bars with per-bar budget ticks, value labels, red when over, and an
     "N teams over budget" pill.
   - **Budget forecast:** the burn-up chart from `/v1/forecast` (org, baseline, current
     period). Extract the chart from `ForecastPanel.tsx` into a shared component; Economics
     keeps its presets, month toggle, daily chart and planner.
   - Footer, left: runway pill (`lasts` → Under budget, `at_risk` → At risk,
     `runs_out`/`exhausted` → Over budget), `runwaySentence`, "See forecast detail →".
   - Footer, right: "Recoverable · could save X% · $Y · mostly {cause}" and "See how to save →"
     (Usage).
   - No forecast: an `UnavailableNote`, and the footer falls back to current `budget_pct`,
     labelled "Budget status".
   - Fetched outside the page `<Block>`.
4. **Row 3: Spend over time** (full width): a daily area chart over the elapsed days (B1), with
   a "N days · daily spend" aside.
5. **Row 4: Spend by agent | PR costs & forecast**
   - **Spend by agent:** donut + share/$ list (B3).
   - Under the donut, **Task usage**: tabs All / one per agent (from `agent_task_mix`), and a
     bar per session type, using Usage's names and colours (`sessionTypeName`,
     `categoryHue("sessionType")`). On "All", each bar is split by agent.
   - **PR costs & forecast:** the existing E1 envelope + cone in a card, with a compact legend
     and "See full unit economics →".
6. **Row 5: Team breakdown** table. Columns: Team (initials + name), Developers, Spend, Merged
   PRs, Budget used (bar + %), Status ("On track" / "$X over budget"). Clicking or pressing
   Enter on a row → Team. "View all teams →". Scrolls inside the card on phones.
7. **Worth a look**: unchanged.
8. **Ask** slot: unchanged.
9. **Footer:** "All amounts in USD".

New primitives in `components/ui.tsx`: `Card`, `Metric`, `Pill`, `SpendGauge`, `ModelDonut`,
`TaskUsageBars`. A `ChartTooltip` content component in `charts.tsx`.

---

## 6. Track D — Shell and fonts (app-wide)

**D1. Fonts**
- `index.css`: load Inter (400–700) and JetBrains Mono (400–600). `*` uses Inter;
  `.font-mono` uses JetBrains Mono. Body text is 13px.
- `.font-serif` stays DM Serif Display, used **only for headline sentences**: Overview's `h1`
  and `ScreenHeadline` on other screens. `SectionHeading` titles and `HeroTile` values (still
  used on other screens) move to Inter semibold.
- Chart tick `fontFamily: "DM Mono"` literals in `charts.tsx` / `ForecastPanel.tsx` /
  `Overview.tsx` switch to a shared constant.
- Visual pass on all 12 screens: long headings, truncation, tables with mono columns.

**D2. Top bar** (all screens, new `shell/TopBar.tsx`)
- **Breadcrumb** built from the nav stack (Organization › Teams › Platform › Person…).
  Functional.
- **Configure** button: functional. It replaces the gear in `TimeframeStrip`; lead/admin only,
  same gate.
- **Ask AI** button: **inert**. Disabled, with a "Coming soon" title. TODO(Track K).
- Not added unless asked: notifications bell, user/profile menu, workspace selector. The dark
  toggle stays in the sidebar.

**D3. Filter bar** (replaces `TimeframeStrip`)
- **Team filter:** a select listing teams from `scopes()`. **Inert**: disabled, showing "All
  teams", with a TODO for team-scoped org endpoints.
- **Timeframe picker:** functional (Track A3).
- **Export CSV:** **inert**: disabled, with a TODO.
- Hidden on screens that hide the timeframe today (Configure, entity screens).
- Mobile: the existing hamburger header merges into the top bar. Below `sm`, the filter bar
  wraps.

---

## 7. Remaining open points

- **Headline serif on other screens.** D1 keeps the serif only on headline sentences and moves
  section titles to Inter. Say so if section titles should stay serif too.
- **Inert controls.** They are shown disabled with "Coming soon". The alternative is hiding
  them behind a flag until they work.
- **Daily-spend day boundary.** UTC, consistent with the forecast. The viewer's timezone is a
  follow-up.

## 8. Order of work and verification

1. **Track B** (backend, independent): B4 → B3 → B1 → B2, each with pytest, then
   types and mock.
2. **Track A**: A1 → A2 (pytest) → A3 (vitest) → A4.
3. **Track D**: fonts and the visual pass, then the top bar and filter bar (hosting A3's
   picker).
4. **Track C**: primitives → KPI row → rows 2–5 → footer.
5. Each step: `pytest` for touched backend modules, `npm run typecheck`, `npm test`,
   `npm run lexicon`, `npm run build`. Then the browser on mock (light/dark at
   375/768/1280/1440) and once against the synth stack (`webui-synth-verify`).
   - Check the over-budget, no-budget, no-forecast, non-monthly-cycle and
     loading/error/unavailable states.
   - Check that switching the timeframe does not refetch the forecast.
6. Update `webui/AGENTS.md` (timeframe model, shell, Overview v2) and `schemas/README.md`.
7. Independent verifier pass after each track. Work on a new branch off `main`.

**Follow-ups:**
- Custom date range.
- `econ-forecast` per org cycle.
- Team filter and Export CSV wired up.
- Ask AI (Track K).
- Viewer-timezone daily series.
