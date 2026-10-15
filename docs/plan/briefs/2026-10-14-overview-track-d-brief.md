# Brief — Overview refresh, Track D (fonts and shell)

Implement §6 (D1, D2, D3) of `docs/plan/2026-10-14-overview-ux-refresh.md` (rev 3). Read §0,
§1 (design language), §6 first, then `src/webui/AGENTS.md` (binding: tone lexicon, one cost
source, data only via `src/data/` + `useData` + `<Block>`, generated types only; the "The
timeframe" section describes the provider/gate/picker and the picker's layout contract that D3
must keep), the progress log `docs/plan/2026-10-14-overview-refresh-progress.md`, and root
`CLAUDE.md`. Work ONLY in `/Users/anil/workspace/Talium/code-econ/.claude/worktrees/overview-refresh-d`
(branch `feat/overview-refresh-d`; `src/webui/node_modules` installed). Tracks A and B are
already merged in this branch.

Reference UX (read-only, ideas only): `/Users/anil/workspace/Talium/talium-lovable-1.0`,
files `src/components/app-shell.tsx` (shell, top bar), `analytics-page.tsx` (filter bar,
Export), `src/styles.css` / index css (fonts, 13px base). Never modify that repo.

## Steps (strict order; the coordinator releases one at a time)

**D1. Fonts.**
- `src/index.css`: load Inter (400-700) and JetBrains Mono (400-600) (same Google Fonts `@import`
  mechanism the file uses today). `*` uses Inter; `.font-mono` uses JetBrains Mono. Body text 13px.
- `.font-serif` stays DM Serif Display and is used ONLY for headline sentences: Overview's `h1`
  and `ScreenHeadline` on the other screens. `SectionHeading` titles and `HeroTile` values (still
  used on other screens) move to Inter semibold. Outfit and DM Mono are retired.
- Every chart tick `fontFamily: "DM Mono"` literal (`charts.tsx`, `ForecastPanel.tsx`,
  `Overview.tsx`, anywhere else: grep `DM Mono|DM Serif|Outfit|fontFamily` over `src/`) switches
  to one shared constant (e.g. `MONO_FONT` in `present/` or `components/`).
- Visual pass on ALL 12 screens (Overview, Usage, Economics, TeamsOverview, ProjectsOverview,
  Team, Person, Project, Feature, Session, PR, MyUsage) plus Configure: long headings, truncation,
  table columns that use `font-mono` (digits width changed), number alignment, tiles, chips, charts'
  axis labels. Fix what the font change breaks (wrapping, overflow, clipped tick labels) with
  minimal CSS/class tweaks; do NOT redesign or remove anything.
- Update the AGENTS.md "Structure" bullet about fonts.

**D2. Top bar** (all screens; new `shell/TopBar.tsx`).
- Breadcrumb built from the nav stack (Organization › Teams › Platform › Person ...), functional:
  clicking a crumb navigates there (reuse `App.tsx`'s navigation; entity pages included).
  First check what back/forward affordances the app has today (App.tsx, screens). If a crumb bar
  would REPLACE an existing visible control (a "Back" link, a stack header), STOP and report
  instead of removing it; keeping the existing control next to the breadcrumb is fine.
- Configure button: functional, lead/admin only, the same role gate as today's gear in
  `TimeframeStrip` (it replaces that gear). Visible on every screen the gear showed on, and also on
  screens where the strip is hidden (the top bar is on all screens).
- Ask AI button: INERT. Disabled, `title="Coming soon"`, with `TODO(Track K): wire Ask AI`. The
  Overview "Ask" slot (`AskBox`) stays exactly as is.
- Not added unless asked: notification bell, user/profile menu, workspace selector. The dark toggle
  stays in the sidebar.
- Mobile: the existing hamburger header (mobile nav drawer) merges into the top bar below `md`.

**D3. Filter bar** (replaces `TimeframeStrip`; hosts the existing `TimeframePicker`).
- Team filter: a select listing teams from `scopes()`. INERT: disabled, showing "All teams",
  `title="Coming soon"`, with `TODO(team filter): team-scoped org endpoints`.
- Timeframe picker: functional, unchanged component and behaviour; the bar must satisfy the
  picker's layout contract (positioned, full-width, no overflow hidden, `px-4` below `sm`, picker on
  the right from `sm`) or you must update the picker's anchoring and re-verify at 320-1440.
- Export CSV: INERT: disabled, `title="Coming soon"`, with `TODO(export): wire Export CSV`.
- The bar's left text (date range + "day N of M") stays.
- Hidden on screens that hide the timeframe today (Configure, entity screens: session, pr, feature).
  Below `sm` the bar wraps.
- The `TimeframeGate` placement stays (first-load skeleton only).

## Rules
- Gates (all in `src/webui`): `npm run typecheck`, `npm test`, `npm run lexicon`, `npm run build`.
  Pure logic (e.g. breadcrumb building from the stack) lives in `present/` with vitest tests.
- Accessibility: landmarks (`nav` aria-label="Breadcrumb", `aria-current="page"`), disabled controls
  keep an accessible name, all new controls reachable by keyboard.
- Never `git -C`. Stage only; the coordinator commits after review. No Claude/Co-Authored-By
  references. TODO in source for anything deferred.
- STOP and report (do not decide) if you would remove/move/hide anything on any screen that plan §0
  does not explicitly cover, or a requirement is ambiguous.

## Handshake
Before any code, reply with: (a) does the brief make sense; (b) ambiguities/concerns with file:line
(current nav/back affordances, the mobile header, how the strip/gear/gate are wired in App.tsx, every
place fonts are set, anything that depends on the old font metrics); (c) your approach to D1, D2, D3
in a few lines each. Wait for "go".
