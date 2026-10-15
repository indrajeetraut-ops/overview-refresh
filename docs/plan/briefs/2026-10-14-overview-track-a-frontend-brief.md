# Brief — Overview refresh, Track A front end (A3, A4)

Implement §3 A3 and A4 (and the front-end half of A5) of `docs/plan/2026-10-14-overview-ux-refresh.md`
(rev 3). Read §0, §3, §6 D3 first, then `src/webui/AGENTS.md` (binding: tone lexicon, one cost
source, data only through `src/data/` + `useData` + `<Block>`, generated types only), the
progress log `docs/plan/2026-10-14-overview-refresh-progress.md`, and
`src/server/api/schemas/README.md` ("Budget cycle models"). The backend (A1, A2) is committed on
this branch: `GET/PUT /v1/budget-cycle`, `GET /v1/budget-cycle/periods?history=`; types are
already generated in `src/webui/src/data/api-types.ts`. Work ONLY in
`/Users/anil/workspace/Talium/code-econ/.claude/worktrees/overview-refresh-a` (branch
`feat/overview-refresh-a`; `src/webui/node_modules` is installed).

Reference UX (read-only ideas, never modify):
`/Users/anil/workspace/Talium/talium-lovable-1.0/src/components/time-range-select.tsx`,
`budget-cycles-panel.tsx`, `src/lib/budget-cycles.ts`.

## Steps (strict order; the coordinator releases one at a time)

**A3a. Timeframe selection, provider, picker.**
- `shell/timeframe.tsx`: `Period` ("15d"|"30d"|"60d") is replaced by
  `TimeframeSelection {key, label, from, to, kind: "budget-period" | "rolling", isCurrent,
  daysElapsed, daysTotal}`. `useTimeframe()` returns `{selection, setSelection, timeframe}` where
  `timeframe` is the `{from, to}` passed straight to `DataSource` (stable object identity: it must
  only change when the selection changes, otherwise every `useData` refetches).
  - Budget-period selections come from the periods endpoint (`from`/`to` as the server sends them;
    the label is the server's label, plus the date range in the picker).
  - Rolling selections ("Last 15 / 30 / 60 days"): `to` = the moment of selection, `from` = `to`
    minus N days (as today, UTC); `daysElapsed = daysTotal = N`; `isCurrent: false`; `kind:
    "rolling"`.
  - The provider fetches the periods ONCE (through the data seam: new `DataSource` methods
    `budgetCycle()`, `budgetPeriods(history)` and `putBudgetCycle(body)`, with `mock` and `api`
    implementations, typed from the generated `Schemas[...]`) and defaults to the CURRENT period.
    Children render a neutral loading state until it settles, so no rolling window flashes first.
    If the periods request fails, fall back to a rolling "Last 30 days" selection and make the
    picker show a small note "Budget periods unavailable"; screens still work.
  - Keep a pure, tested core in `present/timeframe.ts` (selection -> `{from,to}`, default
    selection, rolling selection, picker grouping: "Budget periods" with "· current" marked and
    the date range, then "Other ranges").
- Picker component `TimeframePicker` (in `shell/`): grouped dropdown (button + listbox, keyboard
  accessible: Arrow keys, Enter, Escape, `aria-*`), "Budget periods" then "Other ranges" (rolling
  15/30/60). It replaces the 15d/30d/60d tab buttons in the CURRENT `TimeframeStrip` (the filter
  bar redesign is Track D3, which will host this same component; do not build that bar now).
  Add `TODO(custom range): custom date range is deferred` next to the "Other ranges" group.
- The gear/Configure button in `TimeframeStrip` stays as it is.
- Mock: `data/mock-source.ts` returns a fixture cycle and fixture periods typed against the
  generated schemas. The mock must support switching cycle kinds (Configure's save changes the
  in-memory cycle, and periods follow) so non-monthly states can be checked in the browser: put a
  small, clearly mock-only period generator in `data/mock-cycles.ts` (month, quarter, half, year,
  sprint, custom; relative to today's UTC date) with a vitest. Comment that the server is the only
  real place for calendar/sprint arithmetic.

**A3b. String audit.** Every user-facing "this period / this month / last N days / monthly /
per month / 30-day" string, and every `period`/`PERIODS`/`periodLabel` use, now reads the
selection's label (or neutral wording that stays true for any cycle). Known files: MyUsage,
Usage ("share of the monthly budget"), TeamScreen, PersonScreen, TeamsOverview, ProjectsOverview,
ProjectScreen, Economics, `present/seats.ts`, `data/mock-source.ts` (its 30-day assumption: the
mock treats fixtures as one period; keep working but make the wording not claim 30 days),
ConfigureScreen text, `App.tsx`. Run `grep -rniE "this period|this month|last [0-9]+ days|monthly|per month|30 days|30-day|15d|PERIODS|periodLabel"` over `src/` and handle every hit; list in your report what you
changed and what you deliberately left (e.g. forecast month names, which really are calendar
months). Do not remove or hide anything that is not a string/period reference.

**A3c. Configure "Budget cycle" section** (`screens/ConfigureScreen.tsx`):
- Controls: kind select (Month / Quarter / Half-year / Year / Sprint / Custom), sprint length
  (weeks), current sprint number and its start date (`anchor_number`, `anchor_date`), custom
  start/end (`anchor_date`, `custom_end`), recurring or one-time (one-time offered only for
  sprint and custom). Show only the fields relevant to the kind. Client-side validation mirrors
  the server's rules but the server's 400/422 message is always displayed.
- Before saving a CHANGE of kind, show the warning (and require a confirming click): existing
  amounts are not converted, they stay stored but unused, and scopes show no budget until amounts
  are entered for the new cadence. For a same-kind edit (sprint length, dates) say amounts now
  apply per period of the changed cycle. After saving, show the server's `note`, refetch periods
  and reset the selection to the current period.
- The amount label follows the cycle's `period_label`: "Budget per sprint (USD)", "Budget per
  month (USD)", ... (pure mapping in `present/cycle.ts`, tested). Lead/admin gate unchanged.
- Tone lexicon applies to every new string (`npm run lexicon`).

**A4. Forecast copy.** With a non-month cycle kind, the Economics forecast (ForecastPanel) shows
"Forecast is by calendar month" (pure helper in `present/forecast.ts` or `present/cycle.ts`,
tested). Do NOT touch the forecast maths. The Overview forecast card does not exist yet (Track
C): leave `TODO(Track C): show forecastCycleNote on the Overview forecast card` where the helper
is defined. Keep the existing `TODO(A4)`/per-cycle forecasting follow-up as is.

## Rules
- Gates (all in `src/webui`): `npm run typecheck`, `npm test`, `npm run lexicon`,
  `npm run build`. Types are generated; if you believe the API schema needs a change, STOP and
  report instead of editing `api-types.ts`/the backend.
- Pure logic lives in `present/` with vitest tests; components only lay out.
- Switching the timeframe must NOT refetch the forecast (it is keyed on scope only; keep it so).
- Never `git -C`. Stage only; the coordinator commits after review. No Claude/Co-Authored-By
  references. TODO in source for anything deferred, removed when done.
- STOP and report (do not decide) if you would remove/move/hide anything on any screen that plan
  §0 does not explicitly cover, or a requirement is ambiguous.
- Do not edit the reference repo.

## Handshake
Before any code, reply with: (a) does the brief make sense; (b) ambiguities/concerns with
file:line (how screens currently read `period`/`useTimeframe`; the `Timeframe` union in
`data/source.ts`; where `useData` keys on the timeframe; the full string-audit hit list
grouped by file); (c) your approach to A3a/A3b/A3c/A4 in a few lines each. Wait for "go".
