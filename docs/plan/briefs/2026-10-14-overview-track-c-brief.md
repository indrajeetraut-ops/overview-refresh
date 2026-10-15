# Brief — Overview refresh, Track C (Overview layout)

Implement §5 of `docs/plan/2026-10-14-overview-ux-refresh.md` (rev 3). Read §0 (binding), §1
(design language), §4 (API fields now available), §5 first; then `src/webui/AGENTS.md` (binding:
tone lexicon; one cost source; data only via `src/data/` + `useData` + `<Block>`; generated types
only; "The timeframe", "The top bar", "The filter bar" and the forecast sections describe what
already exists), the progress log `docs/plan/2026-10-14-overview-refresh-progress.md` (note the
carry-forwards), and root `CLAUDE.md`. Work ONLY in
`/Users/anil/workspace/Talium/code-econ/.claude/worktrees/overview-refresh-c` (branch
`feat/overview-refresh-c`; `src/webui/node_modules` installed). Tracks A, B and D are merged here.

Reference UX (read-only, ideas only, its decisions are NOT binding), repo
`/Users/anil/workspace/Talium/talium-lovable-1.0`: `src/components/analytics-page.tsx` (Overview),
`overview-spend-charts.tsx`, `token-usage-chart.tsx`, `analytics-charts.tsx`,
`pr-cost-forecast-chart.tsx`, `chart-info-tooltip.tsx`. Never modify it.

## What the Overview is today (`src/screens/Overview.tsx`)
Serif headline sentence + "Review {team} →" CTA; four HeroTiles (Spent, Merged PRs, Recoverable
spend, Teams over budget); a StatStrip (active teams, active devs, tokens used, active repos, avg
time to PR, rework rate, model mix, reasoning tokens); "Where the spend is coming from" (agent
donut); "Spend by team" bars with dashed budget lines; "Spend over time" (a temporary monthly roll-up
of `daily_spend`, `TODO(Track C)`); "What a PR costs - and what's coming" (E1 `UnitEconomics`,
its own fetch + Block); "Worth a look"; the "Ask" slot (`AskBox`).

## Target layout (plan §5) and the binding constraint
**§0: "Hero tiles, StatStrip: replaced by four KPI cards. EVERY FIGURE MOVES INTO A CARD."** and
"I don't want you to omit things without asking." Map every existing figure to its new home (list
the mapping in your handshake) and STOP if something has no home:
- Spent + budget % -> KPI 1 gauge. Teams over budget -> KPI 2 banner. Active teams, active devs,
  active repos -> KPI 2. Merged PRs, avg cost / PR, avg time to PR, rework rate -> KPI 3.
  Tokens used, model mix, reasoning tokens -> KPI 4. Recoverable spend, dominant cause -> Row 2
  forecast card footer ("Recoverable ..." line). Agent spend -> Row 4. Team spend + budget -> Row 2.
  The E1 median/tail sentence ("A PR costs $X at the median - the tail runs to $Y") and the E1
  envelope + cone chart -> Row 4 right card; the "See full unit economics ->" link stays.
- UNCHANGED: the serif headline + "Review {team} ->" CTA (wording follows the selection label as
  today via `inWindow`), "Worth a look" (`WorthALook`, same section and position as plan row order:
  after the table), the "Ask" slot (`AskBox`).
- Sections now rendered as cards in rows (the §0 "Section dividers -> card rows" decision): the
  `Divider`s and `SectionHeading` blocks of the Overview go away; each card carries its own small
  title. "Monthly spend bars" are replaced by the daily chart.

1. **Headline + CTA** unchanged.
2. **KPI row**, `md:2 / xl:4` columns, four grouped cards (10px uppercase label inside the card,
   `p-3`-style compact padding, `gap-3`):
   - **Spend & budget:** semicircle gauge (spend vs the period budget, `% of $budget`), with a
     "$X left" or "$X over" pill. Budget dollars = spend / `budget_pct` (as the team bars do). No
     budget (`budget_pct` null): spend only, no gauge. Over budget: red, gauge capped at 100% visually
     with the pill "$X over".
   - **Teams & developers:** banner "N of M teams over budget" or "All teams on track" (-> Teams),
     then Active teams, Active developers, "across N repos".
   - **PR performance:** merged PRs (large), "≈ X per day" (merged / `selection.daysElapsed`, or
     elapsed days of the window for rolling), then Cost / PR, Time to PR, Rework rate (each only
     when non-null, as today).
   - **Tokens & models:** model-mix donut with total tokens in the middle, reasoning tokens and %,
     and a legend (`categoryHue("modelTier")`; the API's `model_mix` rows carry model + tier + pct).
3. **Row 2: Spend by team | Budget forecast.**
   - Spend by team: horizontal or vertical bars with per-bar budget ticks, value labels, red when
     over, "N teams over budget" pill. Keep the existing tooltip facts (spent, budget, remaining/over).
   - Budget forecast: the burn-up chart from `GET /v1/forecast` (org scope, baseline preset, current
     period). Extract the chart out of `components/ForecastPanel.tsx` into a shared component (the
     panel keeps presets, month toggle, daily chart, planner and renders the same chart through it).
     Footer left: runway pill (`lasts` -> "Under budget", `at_risk` -> "At risk", `runs_out` /
     `exhausted` -> "Over budget"), `runwaySentence`, "See forecast detail ->" (Economics). Footer
     right: "Recoverable · could save X% · $Y · mostly {cause}" and "See how to save ->" (Usage; no
     "Best performer" pill). With a known non-monthly cycle show `forecastCycleNote` (resolves its
     `TODO(Track C)`). No forecast: `UnavailableNote`, and the footer falls back to the current
     `budget_pct`, labelled "Budget status". The forecast is fetched OUTSIDE the page `<Block>`,
     keyed on scope only (use the same `forecastScope()`/`ORG_SCOPE_ID` and `useData` pattern as
     `EconomicsScreen`); a timeframe switch must NOT refetch it.
4. **Row 3: Spend over time** (full width): daily area chart over `daily_spend` (elapsed days only,
   already UTC-bucketed by the API) with a "N days · daily spend" aside (N = `daily_spend.length`).
   Remove `monthlySpendFromDaily` / `MonthlySpend` from `present/overview.ts` and its tests once
   unused, and the `TODO(Track C)` markers they carry.
5. **Row 4: Spend by agent | PR costs & forecast.**
   - Spend by agent: donut + share/$ list (`agent_spend`, sums to the headline). Under the donut,
     **Task usage**: tabs "All" + one per agent (from `agent_task_mix`), one bar per session type
     (`sessionTypeName`, `categoryHue("sessionType")`); on "All", each bar is split by agent. Share is
     by SESSION COUNT; "All" must equal Usage's `session_types` for the same window.
     **Agent ids:** the API sends raw ids (`claude_code`); the mock sends display names. Add a pure
     agent-id -> display-name mapper in `present/palette.ts` (`claude_code` -> "Claude Code",
     `codex` -> "Codex", `cursor` -> "Cursor", unknown ids title-cased) that also accepts display
     names unchanged, and key `AGENT_HUE` through it, so donut, list, tabs and colours agree on both
     data sources.
   - PR costs & forecast: the existing E1 envelope + cone in a card, compact legend, the median/tail
     sentence, "See full unit economics ->". Keeps its own fetch + `<Block>` (so it can cold-start
     or fail independently).
6. **Row 5: Team breakdown** table: Team (initials avatar + name), Developers, Spend, Merged PRs,
   Budget used (bar + %), Status ("On track" / "$X over budget"). Row click or Enter -> Team
   (`onNavigate("team", id)`); "View all teams ->" -> Teams. Scrolls INSIDE the card on phones
   (`overflow-x-auto`, focusable, labelled) — never clipped.
7. **Worth a look** and **Ask** unchanged (own cards/rows are not required; keep their content).
8. **Footer:** "All amounts in USD".

New primitives in `components/ui.tsx`: `Card`, `Metric`, `Pill`, `SpendGauge`, `ModelDonut`,
`TaskUsageBars`; a `ChartTooltip` content component in `components/charts.tsx`. No new runtime
dependencies (inline SVG icons; Recharts stays). Derived display numbers live in a pure, tested
`present/overview.ts` (gauge fraction/left/over, per-day merge rate, banner text, runway pill,
task-mix tabs/series, team-table rows and status text, budget-used bars...). Components lay out only.

## Steps (strict order; the coordinator releases ONE at a time, each is reviewed before the next)
- **C1** primitives + `present/overview.ts` helpers + the agent-name mapper (+ unit tests). No screen
  change except what is needed to compile; a small kitchen-sink check is fine but not shipped.
- **C2** KPI row (replaces HeroTiles + StatStrip; headline unchanged).
- **C3** Row 2: Spend by team + Budget forecast (+ shared forecast chart extraction; Economics
  must look and behave exactly as before).
- **C4** Row 3: daily spend chart (+ remove the monthly roll-up).
- **C5** Row 4: Spend by agent + Task usage | PR costs & forecast.
- **C6** Row 5: Team breakdown table.
- **C7** Footer, final order of sections (Worth a look, Ask), cleanup of leftovers/TODOs, AGENTS.md
  "Overview v2" section.

## Rules
- Gates (in `src/webui`): `npm run typecheck`, `npm test`, `npm run lexicon`, `npm run build`.
- Every card: loading/empty/cold-start/unavailable/error through `<Block>`/`UnavailableNote`, never
  silently dropped; nulls render as "—"/hidden fields exactly as today (no invented numbers).
- Tone lexicon: "recoverable / could save / room to save", never "waste"; never rank people.
- Light and dark; 320-1440 wide; no horizontal page scroll; accessible names for charts (`role="img"`
  with an `aria-label` summarising the figure) and keyboard access for clickable rows.
- Never `git -C`. Stage only; the coordinator commits after review. No Claude/Co-Authored-By
  references. TODO in source for anything deferred; remove markers when done.
- STOP and report (do not decide) if anything on today's Overview would be removed, moved out of the
  Overview or hidden and §0 does not explicitly cover it; or a requirement is ambiguous.

## Handshake
Before any code, reply with: (a) does the brief make sense; (b) the figure-by-figure mapping
(every figure/section on today's Overview -> its new home) and anything that has no home;
(c) ambiguities/concerns with file:line (generated types for `model_mix`, `agent_task_mix`,
`daily_spend`, the forecast `ForecastView`; how `ForecastPanel` should be split; how the forecast is
fetched outside the Block; the mock's agent/task-mix naming; where cards would collide with the new
top/filter bars (sticky offsets); anything the reference does that we can't without new
dependencies); (d) your approach to C1..C7 in a few lines each, with the file list. Wait for "go".
