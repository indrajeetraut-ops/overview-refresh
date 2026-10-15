# Forecast UI (src/webui) — Test Report

Run: `2026-10-08T21:28:05.384Z`

- Total: 107  Passed: 107  Failed: 0  Skipped: 0
- Wall time (sum of cases): 0.11s

## By test module

| Module | Tests | Passed | Failed | Skipped | Time (s) |
|---|---:|---:|---:|---:|---:|
| src/components/Block.test.ts | 7 | 7 | 0 | 0 | 0.00 |
| src/present/forecast.test.ts | 75 | 75 | 0 | 0 | 0.10 |
| src/present/present.test.ts | 21 | 21 | 0 | 0 | 0.01 |
| src/shell/shell.test.ts | 4 | 4 | 0 | 0 | 0.00 |

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
| src/present/forecast.test.ts.fmtCompactUsd > compacts micro-dollars | pass | 0.001 |
| src/present/forecast.test.ts.fmtCompactUsd > never prints a four-digit dollar figure or a 1000.0k | pass | 0.000 |
| src/present/forecast.test.ts.fmtCompactUsd > has billion and trillion tiers instead of printing 1000.0M | pass | 0.000 |
| src/present/forecast.test.ts.fmtCompactUsd > keeps the sign and tolerates a missing value | pass | 0.000 |
| src/present/forecast.test.ts.probabilityLabel > rounds, but never shows certainty that is not there | pass | 0.000 |
| src/present/forecast.test.ts.probabilityLabel > is a dash for something that is not a number | pass | 0.000 |
| src/present/forecast.test.ts.bandLabel names the percentile pair | pass | 0.000 |
| src/present/forecast.test.ts.dates > formats an ISO day without a time zone | pass | 0.000 |
| src/present/forecast.test.ts.dates > the shared day helpers are the same functions | pass | 0.000 |
| src/present/forecast.test.ts.dates > does not shift the day west of UTC | pass | 0.001 |
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
| src/present/forecast.test.ts.headlineSentences > baseline with a budget | pass | 0.003 |
| src/present/forecast.test.ts.headlineSentences > uses compact thousands for larger landings | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > without a budget there is no probability sentence | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > no probability sentence without a budget, even if the period carries a probability | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > a future period names its month | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > month-end case names the months and has no runway | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > another preset: more, less, and the same as the baseline | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > larger deltas are compact | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > omits the comparison when the baseline has no numbers | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > probability edges | pass | 0.001 |
| src/present/forecast.test.ts.headlineSentences > drops the probability sentence once the budget is used up | pass | 0.000 |
| src/present/forecast.test.ts.headlineSentences > an incomplete series has no sentences | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > lasts: the budget minus the median landing is what is likely left | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > lasts floors what is left at zero | pass | 0.000 |
| src/present/forecast.test.ts.runwaySentence > at risk: with dates, days early, the last day, and without dates | pass | 0.000 |
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
| src/present/forecast.test.ts.mock forecast fixture > is internally consistent | pass | 0.044 |
| src/present/forecast.test.ts.mock forecast fixture > the burn-up's actual line meets the projection exactly, for every scope | pass | 0.001 |
| src/present/forecast.test.ts.mock forecast fixture > covers every runway state, a scope without a budget and an incomplete series | pass | 0.000 |
| src/present/forecast.test.ts.mock forecast fixture > every generated sentence passes the lexicon gate | pass | 0.008 |
| src/present/forecast.test.ts.mock forecast fixture > the plan fixture agrees with the run: gap is plan-for minus budget, tokens cover the forecast | pass | 0.015 |
| src/present/present.test.ts.format > durations | pass | 0.001 |
| src/present/present.test.ts.format > days | pass | 0.000 |
| src/present/present.test.ts.format > span in days picks a readable unit | pass | 0.000 |
| src/present/present.test.ts.format > relative time | pass | 0.000 |
| src/present/present.test.ts.format > money + tokens + pct | pass | 0.002 |
| src/present/present.test.ts.palette > pins the quality vocabulary | pass | 0.000 |
| src/present/present.test.ts.palette > maps raw session-type labels to display names and pinned hues | pass | 0.000 |
| src/present/present.test.ts.palette > falls back to a stable ramp for unknown labels | pass | 0.000 |
| src/present/present.test.ts.percentile layout > switches to log scale for a skewed spread, linear otherwise | pass | 0.000 |
| src/present/present.test.ts.percentile layout > stacks colliding labels into lanes and clamps them inside the track | pass | 0.000 |
| src/present/present.test.ts.routing > maps entity refs to routes | pass | 0.000 |
| src/present/present.test.ts.routing > throws when an id-bearing kind has no id | pass | 0.000 |
| src/present/present.test.ts.routing > routes a finding via its entity ref | pass | 0.000 |
| src/present/present.test.ts.lexicon > flags banned words | pass | 0.000 |
| src/present/present.test.ts.lexicon > locks the quality vocabulary | pass | 0.000 |
| src/present/present.test.ts.lifecycle > positions milestones proportionally by elapsed time | pass | 0.001 |
| src/present/present.test.ts.lifecycle > pins an unreached milestone (ts: null) at x=100 | pass | 0.000 |
| src/present/present.test.ts.lifecycle > does not divide by zero with fewer than two reached timestamps | pass | 0.000 |
| src/present/present.test.ts.seats > leaves people with no seat allowance out of the chart instead of plotting 0% | pass | 0.000 |
| src/present/present.test.ts.seats > colours bars by the seat band, not by room to save | pass | 0.000 |
| src/present/present.test.ts.seats > maps the headline status to a chip: room to save, high use, low use, new seat, on track | pass | 0.000 |
| src/shell/shell.test.ts.timeframe > resolves a period to a half-open window ending now | pass | 0.002 |
| src/shell/shell.test.ts.timeframe > 60d spans 60 days | pass | 0.000 |
| src/shell/shell.test.ts.timeframe > labels | pass | 0.000 |
| src/shell/shell.test.ts.role gate (F2) > hides the manager nav from a dev | pass | 0.000 |

Reproduce:

```bash
venv/bin/python -m pytest src/webui/src/ --junitxml=docs/test/reports/forecast-ui-junit.xml
venv/bin/python utils/gen_test_report.py \
    --title "Forecast UI (src/webui)" \
    --junit docs/test/reports/forecast-ui-junit.xml \
    --out   docs/test/reports/forecast-ui-test-report.md \
    --tests src/webui/src/
```

T7: presentation helpers (burn-up and daily rows, sentences, planner rows and sorting), ApiSource forecast URLs and the conforming mock fixture; pure-function tests only, no DOM.
