# Overview refresh front end (vitest) — Test Report

Run: `2026-10-14T22:38:57.207Z`

- Total: 248  Passed: 248  Failed: 0  Skipped: 0
- Wall time (sum of cases): 0.16s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| src/components/Block.test.ts | 7 | 7 | 0 | 0 | 0.00 |
| src/data/mock-cycles.test.ts | 11 | 11 | 0 | 0 | 0.00 |
| src/data/source.test.ts | 5 | 5 | 0 | 0 | 0.00 |
| src/present/crumbs.test.ts | 16 | 16 | 0 | 0 | 0.00 |
| src/present/cycle.test.ts | 25 | 25 | 0 | 0 | 0.00 |
| src/present/fonts.test.ts | 4 | 4 | 0 | 0 | 0.00 |
| src/present/forecast.test.ts | 75 | 75 | 0 | 0 | 0.10 |
| src/present/overview.test.ts | 51 | 51 | 0 | 0 | 0.02 |
| src/present/palette.test.ts | 9 | 9 | 0 | 0 | 0.00 |
| src/present/present.test.ts | 27 | 27 | 0 | 0 | 0.01 |
| src/present/timeframe.test.ts | 17 | 17 | 0 | 0 | 0.00 |
| src/shell/shell.test.ts | 1 | 1 | 0 | 0 | 0.00 |

## All tests

| Test | Result | Time (s) |
|---|---|---|
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > loading | pass | 0.001 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > error | pass | 0.000 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > unavailable — envelope carries {unavailable} | pass | 0.000 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > cold-start — data null, no unavailable (NOT the same as unavailable) | pass | 0.000 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > empty — data present but isEmpty() | pass | 0.000 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > ready — hands back the non-null payload | pass | 0.000 |
| src/components/Block.test.ts.resolveBlockState — the five states are distinct > a present payload with a still-computing section reads as cold-start, not empty | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > month: six started months, the current one last, and the next one | pass | 0.003 |
| src/data/mock-cycles.test.ts.mock budget periods > honours `history` | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > quarter, half and year roll over the calendar year | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > sprint: numbered from the anchor, never below sprint 1 | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > sprint: before the anchor there is no period, only the next one | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > custom: labelled by its dates, one-time gives a single period | pass | 0.001 |
| src/data/mock-cycles.test.ts.mock budget periods > custom recurring repeats with its own length | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock budget periods > every kind has a stored label | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock cycle validation > mirrors the server's reasons | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock cycle validation > rejects an unknown kind or recurrence in the server's words | pass | 0.000 |
| src/data/mock-cycles.test.ts.mock cycle validation > normalises like the server: unused fields null, sprint defaults filled | pass | 0.000 |
| src/data/source.test.ts.errorMessage > prefers the API's 400 reason | pass | 0.001 |
| src/data/source.test.ts.errorMessage > joins a 422 detail list, naming the field | pass | 0.000 |
| src/data/source.test.ts.errorMessage > accepts a plain string detail | pass | 0.000 |
| src/data/source.test.ts.errorMessage > falls back to the status line for anything else | pass | 0.000 |
| src/data/source.test.ts.periodParams > sends the resolved window | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: roots > overview is the single crumb Organization | pass | 0.001 |
| src/present/crumbs.test.ts.buildCrumbs: roots > other manager sections sit under an Organization crumb that resets to Overview | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: roots > my-usage (a dev viewer) has no Organization crumb | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: trail > is the stack as walked, with no synthesised Teams crumb | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: trail > walks Teams > team > person with registry names | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: trail > falls back to a generic noun while a name is unknown, and for blank names | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: trail > names a later section entry plainly | pass | 0.000 |
| src/present/crumbs.test.ts.buildCrumbs: trail > an empty stack has no crumbs | pass | 0.000 |
| src/present/crumbs.test.ts.collapseCrumbs > leaves a trail of four or fewer alone | pass | 0.000 |
| src/present/crumbs.test.ts.collapseCrumbs > keeps the first crumb, an ellipsis and the last two | pass | 0.000 |
| src/present/crumbs.test.ts.collapseCrumbs > shows everything when expanded | pass | 0.000 |
| src/present/crumbs.test.ts.scopeLabels > keys team and project names like the registry does | pass | 0.000 |
| src/present/crumbs.test.ts.scopeLabels > is empty without scopes | pass | 0.000 |
| src/present/crumbs.test.ts.mergeLabels > lets a published name win over the scopes() fallback | pass | 0.000 |
| src/present/crumbs.test.ts.mergeLabels > does not let a blank published name hide the fallback | pass | 0.000 |
| src/present/crumbs.test.ts.mergeLabels > works without a fallback, and feeds buildCrumbs | pass | 0.000 |
| src/present/cycle.test.ts.wording for any cycle > names what a budget is per, from the stored label | pass | 0.001 |
| src/present/cycle.test.ts.wording for any cycle > is not fooled by names every object has | pass | 0.000 |
| src/present/cycle.test.ts.wording for any cycle > labels the amount field after the cycle | pass | 0.000 |
| src/present/cycle.test.ts.wording for any cycle > words the selected window | pass | 0.000 |
| src/present/cycle.test.ts.wording for any cycle > words a sprint, a custom date range and a period not started yet | pass | 0.000 |
| src/present/cycle.test.ts.forecast copy > says the forecast is by calendar month for any known cycle that is not monthly | pass | 0.000 |
| src/present/cycle.test.ts.forecast copy > says nothing for a monthly, unknown or missing cycle | pass | 0.000 |
| src/present/cycle.test.ts.forecast copy > passes the tone lexicon | pass | 0.000 |
| src/present/cycle.test.ts.the draft > opens on the saved cycle, with defaults for what a kind does not store | pass | 0.000 |
| src/present/cycle.test.ts.the draft > offers one-time for sprint and custom only | pass | 0.000 |
| src/present/cycle.test.ts.the draft > builds only the fields the kind uses, calendar kinds always recurring | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > accepts a valid draft of every kind | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > does not check fields the kind does not use | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > sprint length is whole weeks, 1 to 52 | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > sprint number is a whole number from 1 to 100000 | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > needs a real start date inside 2000..2100 | pass | 0.000 |
| src/present/cycle.test.ts.validation mirrors the server's rules > custom needs both days, the last not before the first | pass | 0.000 |
| src/present/cycle.test.ts.which field a problem belongs to > names the field, so the form can mark it invalid | pass | 0.000 |
| src/present/cycle.test.ts.which field a problem belongs to > agrees with validateCycleDraft | pass | 0.000 |
| src/present/cycle.test.ts.what a save would change > nothing when the draft equals the saved cycle (including fields a kind ignores) | pass | 0.000 |
| src/present/cycle.test.ts.what a save would change > a different kind is a kind change | pass | 0.000 |
| src/present/cycle.test.ts.what a save would change > the same kind with other values is an edit | pass | 0.000 |
| src/present/cycle.test.ts.messages before a save > the kind-change warning says nothing is converted and what to do | pass | 0.000 |
| src/present/cycle.test.ts.messages before a save > the same-kind notice says amounts now apply per period of the changed cycle | pass | 0.000 |
| src/present/cycle.test.ts.messages before a save > pass the tone lexicon | pass | 0.000 |
| src/present/fonts.test.ts.MONO_FONT > is exactly the stack .font-mono uses in index.css | pass | 0.001 |
| src/present/fonts.test.ts.MONO_FONT > is also the stack of .chip, and the Google Fonts import loads its first family | pass | 0.000 |
| src/present/fonts.test.ts.MONO_FONT > sets Inter once on body (not on *, which would override .font-mono's children) | pass | 0.000 |
| src/present/fonts.test.ts.MONO_FONT > leaves no retired font behind | pass | 0.000 |
| src/present/forecast.test.ts.fmtCompactUsd > compacts micro-dollars | pass | 0.002 |
| src/present/forecast.test.ts.fmtCompactUsd > never prints a four-digit dollar figure or a 1000.0k | pass | 0.000 |
| src/present/forecast.test.ts.fmtCompactUsd > has billion and trillion tiers instead of printing 1000.0M | pass | 0.000 |
| src/present/forecast.test.ts.fmtCompactUsd > keeps the sign and tolerates a missing value | pass | 0.000 |
| src/present/forecast.test.ts.probabilityLabel > rounds, but never shows certainty that is not there | pass | 0.000 |
| src/present/forecast.test.ts.probabilityLabel > is a dash for something that is not a number | pass | 0.000 |
| src/present/forecast.test.ts.bandLabel names the percentile pair | pass | 0.000 |
| src/present/forecast.test.ts.dates > formats an ISO day without a time zone | pass | 0.000 |
| src/present/forecast.test.ts.dates > the shared day helpers are the same functions | pass | 0.000 |
| src/present/forecast.test.ts.dates > does not shift the day west of UTC | pass | 0.002 |
| src/present/forecast.test.ts.dates > month names and day arithmetic | pass | 0.000 |
| src/present/forecast.test.ts.presets and periods > groups presets: ungrouped (baseline) first, groups in order of first appearance | pass | 0.000 |
| src/present/forecast.test.ts.presets and periods > falls back to the baseline for an id the run does not have | pass | 0.000 |
| src/present/forecast.test.ts.presets and periods > labels the periods | pass | 0.001 |
| src/present/forecast.test.ts.presets and periods > detects a series without numbers | pass | 0.000 |
| src/present/forecast.test.ts.burnUpRows > current period: cumulative actual, a join row with no step, then the projection | pass | 0.000 |
| src/present/forecast.test.ts.burnUpRows > the cumulative actual meets actual_to_date exactly | pass | 0.000 |
| src/present/forecast.test.ts.burnUpRows > adds the baseline's expected only for another preset, joined the same way | pass | 0.001 |
| src/present/forecast.test.ts.burnUpRows > a future period has no actual rows and no join row | pass | 0.000 |
| src/present/forecast.test.ts.burnUpRows > month-end case: no period is current, so no actual rows and no join row | pass | 0.001 |
| src/present/forecast.test.ts.burnUpRows > merges the join row with the last actual day when only one day is actual | pass | 0.000 |
| src/present/forecast.test.ts.burnUpRows > an incomplete series or a missing period gives no rows | pass | 0.000 |
| src/present/forecast.test.ts.burnUpMarkers > budget line and the runway marker with its range | pass | 0.000 |
| src/present/forecast.test.ts.burnUpMarkers > nothing when the runway has no dates, none for a future period, none without a budget | pass | 0.000 |
| src/present/forecast.test.ts.burnUpMarkers > a single-day range (exhausted) draws the marker only | pass | 0.000 |
| src/present/forecast.test.ts.legends list only the series that are drawn > burn-up: a current period has every entry | pass | 0.000 |
| src/present/forecast.test.ts.legends list only the series that are drawn > burn-up: no 'spent so far' in a future month or the month-end case | pass | 0.000 |
| src/present/forecast.test.ts.legends list only the series that are drawn > burn-up: no baseline entry unless a baseline line is drawn, no budget entry without … | pass | 0.000 |
| src/present/forecast.test.ts.legends list only the series that are drawn > daily: actual, expected and band | pass | 0.000 |
| src/present/forecast.test.ts.dailyRows > the last 28 actual days, then the forecast after data_through | pass | 0.000 |
| src/present/forecast.test.ts.dailyRows > keeps all rows when there are fewer than 28, and sorts them | pass | 0.000 |
| src/present/forecast.test.ts.dailyRows > month-end case: 28 actual days and a forecast that all lies after data_through | pass | 0.000 |
| src/present/forecast.test.ts.dailyRows > an incomplete series has actuals only | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > baseline with a budget | pass | 0.002 |
| src/present/forecast.test.ts.headlineSentences > uses compact thousands for larger landings | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > without a budget there is no probability sentence | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > no probability sentence without a budget, even if the period carries a probability | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > a future period names its month | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > month-end case names the months and has no runway | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > another preset: more, less, and the same as the baseline | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > larger deltas are compact | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > omits the comparison when the baseline has no numbers | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > probability edges | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > drops the probability sentence once the budget is used up | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > an incomplete series has no sentences | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > lasts: the budget minus the median landing is what is likely left | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > lasts floors what is left at zero | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > at risk: with dates, days early, the last day, and without dates | pass | 0.001 |
| src/present/forecast.test.ts.runwaySentence > runs out: with dates, singular day, and without dates | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > exhausted names the day, or says it is used up | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > another preset names itself instead of saying "at this pace" | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > no sentence for a period that is not under way, even if it carries a runway object | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > no sentence for a future period, without a runway or without a budget | pass | 0.000 |
| src/present/forecast.test.ts.forecastFootnote says when the forecast starts and what actuals it sits on | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > opens on the first period that is not under way | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > builds a row per team and project, in response order, for a preset and period | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > a preset with no figures keeps the name and budget, with empty figures; an unknown preset falls back | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > sort: descending, ascending, nulls always last, stable, inside each group | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > the sort header cycles none -> descending -> ascending -> none | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > uses plan_for_usd_micros, not the 80th percentile, and the response's confidence | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > the gap sort button names its state | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > forecast token counts get a billions tier | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > groups consecutive rows under Teams and Projects | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > the gap cell | pass | 0.000 |
| src/present/forecast.test.ts.planner helpers > header and token footnote | pass | 0.000 |
| src/present/forecast.test.ts.ApiSource forecast calls > forecast sends scope_type and scope_id, encoding a repo id | pass | 0.001 |
| src/present/forecast.test.ts.ApiSource forecast calls > forecastPlan takes no parameters | pass | 0.000 |
| src/present/forecast.test.ts.ApiSource forecast calls > the org uses the real org id for the forecast, not the economics placeholder | pass | 0.000 |
| src/present/forecast.test.ts.mock forecast fixture > has a run for the org, the teams and two projects, and none for growth-web | pass | 0.000 |
| src/present/forecast.test.ts.mock forecast fixture > serves envelopes through the mock source | pass | 0.011 |
| src/present/forecast.test.ts.mock forecast fixture > is internally consistent | pass | 0.042 |
| src/present/forecast.test.ts.mock forecast fixture > the burn-up's actual line meets the projection exactly, for every scope | pass | 0.001 |
| src/present/forecast.test.ts.mock forecast fixture > covers every runway state, a scope without a budget and an incomplete series | pass | 0.000 |
| src/present/forecast.test.ts.mock forecast fixture > every generated sentence passes the lexicon gate | pass | 0.008 |
| src/present/forecast.test.ts.mock forecast fixture > the plan fixture agrees with the run: gap is plan-for minus budget, tokens cover the forecast | pass | 0.014 |
| src/present/overview.test.ts.budget and gauge > tones use the old thresholds | pass | 0.001 |
| src/present/overview.test.ts.budget and gauge > recovers the budget from spend and share, guarded | pass | 0.000 |
| src/present/overview.test.ts.budget and gauge > under budget: capped arc, left pill, caption | pass | 0.004 |
| src/present/overview.test.ts.budget and gauge > comfortably under budget is green | pass | 0.000 |
| src/present/overview.test.ts.budget and gauge > over budget: red, arc capped at 100%, over pill | pass | 0.000 |
| src/present/overview.test.ts.budget and gauge > exactly on budget has $0 left and is red | pass | 0.000 |
| src/present/overview.test.ts.budget and gauge > no spend figure: no gauge (the card shows a plain Spent metric) | pass | 0.000 |
| src/present/overview.test.ts.budget and gauge > no budget: no gauge; zero share: arc empty, no amount | pass | 0.000 |
| src/present/overview.test.ts.KPI texts > teams banner | pass | 0.000 |
| src/present/overview.test.ts.KPI texts > repos | pass | 0.000 |
| src/present/overview.test.ts.KPI texts > merged per day over elapsed days, never dividing by zero | pass | 0.000 |
| src/present/overview.test.ts.KPI texts > reasoning tokens and share | pass | 0.000 |
| src/present/overview.test.ts.model mix slices > small mixes list every model, biggest first, in tier colours | pass | 0.000 |
| src/present/overview.test.ts.model mix slices > rolls everything past the fourth into +N other with the full list in its title | pass | 0.000 |
| src/present/overview.test.ts.model mix slices > keys are unique even when a model id repeats | pass | 0.000 |
| src/present/overview.test.ts.model mix slices > nothing in, nothing out | pass | 0.000 |
| src/present/overview.test.ts.forecast card helpers > runway statuses to pills | pass | 0.000 |
| src/present/overview.test.ts.forecast card helpers > budget status fallback | pass | 0.000 |
| src/present/overview.test.ts.forecast card helpers > recoverable line | pass | 0.001 |
| src/present/overview.test.ts.forecast card helpers > the mock org forecast: the current period, with a runway sentence | pass | 0.005 |
| src/present/overview.test.ts.forecast card helpers > each runway status gives its pill and a sentence about the budget | pass | 0.002 |
| src/present/overview.test.ts.forecast card helpers > the card title names the calendar month being drawn | pass | 0.001 |
| src/present/overview.test.ts.forecast card helpers > no budget, or no period under way, means no runway footer | pass | 0.001 |
| src/present/overview.test.ts.team bars and table > bars carry whole-dollar spend, recovered budget and the delta | pass | 0.000 |
| src/present/overview.test.ts.team bars and table > tooltip facts: spent, budget, and what is over or left | pass | 0.000 |
| src/present/overview.test.ts.team bars and table > table rows: figures, budget-used bar and status | pass | 0.000 |
| src/present/overview.test.ts.team bars and table > over budget is the server's flag or a share above 100%, in status, tone and bar alike | pass | 0.001 |
| src/present/overview.test.ts.team bars and table > big counts get thousands separators; rows keep the API order | pass | 0.000 |
| src/present/overview.test.ts.team bars and table > an over-budget flag without a recoverable budget still says so | pass | 0.000 |
| src/present/overview.test.ts.team bars and table > initials | pass | 0.000 |
| src/present/overview.test.ts.team bar labels > truncates without a dangling space and counts characters, not code units | pass | 0.001 |
| src/present/overview.test.ts.team bar labels > plans labels from the room each bar has | pass | 0.000 |
| src/present/overview.test.ts.daily chart > micros become dollars in the rows, once | pass | 0.000 |
| src/present/overview.test.ts.daily chart > describes range, total and peak for assistive technology | pass | 0.000 |
| src/present/overview.test.ts.daily chart > edge cases: tied peak, all-zero days, huge values, sub-dollar days | pass | 0.000 |
| src/present/overview.test.ts.daily chart > small amounts keep their cents, big ones do not | pass | 0.000 |
| src/present/overview.test.ts.daily aside > counts days | pass | 0.000 |
| src/present/overview.test.ts.task usage > tabs: All, then agents by sessions, under display names | pass | 0.000 |
| src/present/overview.test.ts.task usage > ids and display names are one agent | pass | 0.000 |
| src/present/overview.test.ts.task usage > agents with no sessions get no tab; a missing agent id is Unknown | pass | 0.000 |
| src/present/overview.test.ts.task usage > All: bars by session count, split by agent | pass | 0.000 |
| src/present/overview.test.ts.task usage > an agent's tab: its own types in the session-type colours | pass | 0.001 |
| src/present/overview.test.ts.task usage > unknown tab and empty mix give no bars | pass | 0.000 |
| src/present/overview.test.ts.task usage > on the mock, All per type equals Usage's session types | pass | 0.000 |
| src/present/overview.test.ts.spend by agent > slices use display names, merge an id with its display name and sum to the total | pass | 0.000 |
| src/present/overview.test.ts.spend by agent > a credit counts as zero spend, so the total never goes backwards | pass | 0.000 |
| src/present/overview.test.ts.spend by agent > a missing agent id is Unknown in the donut, the tabs and the bars alike | pass | 0.000 |
| src/present/overview.test.ts.spend by agent > one colour map covers agents that are only in the spend list or only in the task mix | pass | 0.000 |
| src/present/overview.test.ts.spend by agent > donut, list, tabs and bars agree on every agent's colour, from ids and from display names | pass | 0.000 |
| src/present/overview.test.ts.PR cost envelope > rows: now, then the cone's later steps, in dollars | pass | 0.000 |
| src/present/overview.test.ts.PR cost envelope > describes it for assistive technology | pass | 0.000 |
| src/present/palette.test.ts.agent colours in the stylesheet > every ramp colour is defined in both themes | pass | 0.001 |
| src/present/palette.test.ts.agent colours in the stylesheet > the ramp has no repeats, no pinned agent colour, no --blue (it is --primary) and no status red | pass | 0.001 |
| src/present/palette.test.ts.agent colours in the stylesheet > in light and dark, a ramp colour looks different from every pinned agent colour, red and the othe… | pass | 0.001 |
| src/present/palette.test.ts.assignAgentHues > pinned agents keep their colours, from ids or display names | pass | 0.000 |
| src/present/palette.test.ts.assignAgentHues > two unknown agents whose names hash alike still get different colours | pass | 0.000 |
| src/present/palette.test.ts.assignAgentHues > depends on the set of names, not their order or spelling | pass | 0.000 |
| src/present/palette.test.ts.assignAgentHues > as many unknown agents as the ramp has colours all differ, and none uses a pinned colour | pass | 0.000 |
| src/present/palette.test.ts.assignAgentHues > more unknown agents than colours reuse some, deterministically | pass | 0.000 |
| src/present/palette.test.ts.assignAgentHues > a missing id is the one agent called Unknown | pass | 0.000 |
| src/present/present.test.ts.format > durations | pass | 0.001 |
| src/present/present.test.ts.format > days | pass | 0.000 |
| src/present/present.test.ts.format > span in days picks a readable unit | pass | 0.000 |
| src/present/present.test.ts.format > relative time | pass | 0.000 |
| src/present/present.test.ts.format > money + tokens + pct | pass | 0.002 |
| src/present/present.test.ts.palette > pins the quality vocabulary | pass | 0.000 |
| src/present/present.test.ts.palette > maps raw session-type labels to display names and pinned hues | pass | 0.000 |
| src/present/present.test.ts.palette > maps agent ids and display names to one display name | pass | 0.000 |
| src/present/present.test.ts.palette > colours an agent the same from its raw id or its display name | pass | 0.000 |
| src/present/present.test.ts.palette > falls back to a stable ramp for unknown labels | pass | 0.000 |
| src/present/present.test.ts.percentile layout > switches to log scale for a skewed spread, linear otherwise | pass | 0.000 |
| src/present/present.test.ts.percentile layout > stacks colliding labels into lanes and clamps them inside the track | pass | 0.001 |
| src/present/present.test.ts.routing > maps entity refs to routes | pass | 0.000 |
| src/present/present.test.ts.routing > throws when an id-bearing kind has no id | pass | 0.000 |
| src/present/present.test.ts.routing > routes a finding via its entity ref | pass | 0.000 |
| src/present/present.test.ts.lexicon > flags banned words | pass | 0.000 |
| src/present/present.test.ts.lexicon > locks the quality vocabulary | pass | 0.000 |
| src/present/present.test.ts.lifecycle > positions milestones proportionally by elapsed time | pass | 0.000 |
| src/present/present.test.ts.lifecycle > pins an unreached milestone (ts: null) at x=100 | pass | 0.000 |
| src/present/present.test.ts.lifecycle > does not divide by zero with fewer than two reached timestamps | pass | 0.000 |
| src/present/present.test.ts.seats > leaves people with no seat allowance out of the chart instead of plotting 0% | pass | 0.000 |
| src/present/present.test.ts.seats > colours bars by the seat band, not by room to save | pass | 0.000 |
| src/present/present.test.ts.seats > maps the headline status to a chip: room to save, high use, low use, new seat, on track | pass | 0.000 |
| src/present/present.test.ts.daily spend (Overview B1) > the mock daily series covers the elapsed days and sums to the headline exactly | pass | 0.001 |
| src/present/present.test.ts.daily spend (Overview B1) > the mock overview carries daily_spend summing to its headline | pass | 0.001 |
| src/present/present.test.ts.agent task mix (Overview B2) > the mock mix sums per type to the Usage session-type counts, one row per agent and type | pass | 0.000 |
| src/present/present.test.ts.agent task mix (Overview B2) > the mock overview carries it | pass | 0.000 |
| src/present/timeframe.test.ts.selection to window > a budget period carries the server's bounds, label and counters untouched | pass | 0.001 |
| src/present/timeframe.test.ts.selection to window > a rolling range ends now and starts N days earlier (UTC), never current | pass | 0.000 |
| src/present/timeframe.test.ts.the default selection > is the current budget period | pass | 0.000 |
| src/present/timeframe.test.ts.the default selection > is rolling Last 30 days when none is under way or there are none | pass | 0.000 |
| src/present/timeframe.test.ts.keys > resolves a period key from the list and a rolling key against now | pass | 0.000 |
| src/present/timeframe.test.ts.keys > is null for an unknown key or an unoffered rolling length | pass | 0.000 |
| src/present/timeframe.test.ts.date text > shows a budget period's whole range from its ISO text, whatever the local zone | pass | 0.000 |
| src/present/timeframe.test.ts.date text > adds the year when it is not this year or the range spans two | pass | 0.000 |
| src/present/timeframe.test.ts.date text > a rolling range reads from its start day to today | pass | 0.000 |
| src/present/timeframe.test.ts.date text > says how far into the current period we are, and only then | pass | 0.000 |
| src/present/timeframe.test.ts.picker grouping > lists budget periods newest first with dates and the current one marked, then the rolling ranges | pass | 0.000 |
| src/present/timeframe.test.ts.picker grouping > never offers the upcoming period | pass | 0.000 |
| src/present/timeframe.test.ts.picker grouping > with no periods the first group carries a note instead of options | pass | 0.000 |
| src/present/timeframe.test.ts.picker keyboard > steps and clamps at both ends without wrapping | pass | 0.000 |
| src/present/timeframe.test.ts.picker keyboard > Home and End jump to the ends; other keys change nothing | pass | 0.000 |
| src/present/timeframe.test.ts.picker keyboard > copes with an empty list | pass | 0.000 |
| src/present/timeframe.test.ts.custom period rows > do not repeat their dates under a label that already is the dates | pass | 0.000 |
| src/shell/shell.test.ts.role gate (F2) > hides the manager nav from a dev | pass | 0.001 |

Reproduce:

```bash
venv/bin/python -m pytest cd src/webui && npm test --junitxml=docs/test/reports/overview-ui-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Overview refresh front end (vitest)" \
    --junit docs/test/reports/overview-ui-junit.xml \
    --out   docs/test/reports/overview-ui-test-report.md \
    --tests 'cd src/webui && npm test'
```

Pure presentation logic only (the repo has no DOM test setup): timeframe selection, budget-cycle drafts, crumbs, Overview derived numbers, palette and agent colours.

Browser verification (mock and synth stack, 320 to 1440, light and dark) is recorded step by step in docs/plan/2026-10-14-overview-refresh-progress.md.
