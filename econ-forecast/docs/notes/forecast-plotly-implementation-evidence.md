# Forecast Plotly implementation evidence

**Prepared:** 2026-09-14
**Status:** ready for independent review; parent owns final acceptance.

## Contract acknowledgment

Before editing, Agent B ran:

```text
shasum -a 256 -c econ-forecast/docs/notes/forecasting-release-snapshot.sha256
```

Every frozen source reported `OK`. After the Python 3.11 compatibility amendment, Agent B ran:

```text
shasum -a 256 -c econ-forecast/docs/notes/forecasting-release-amendment-1.sha256
```

Both amended sources (`requirements.txt` and `forecast-numerical-preflight.md`) reported `OK`.

## Requirement mapping

| Requirement | Implementation | Independent test/inspection | Observed result |
|---|---|---|---|
| PLOT-01 exact values and identities | `build_figure`; index-based scope/model selection; NumPy mean and linear quantiles | `test_hand_oracle_and_model_org_separation`; fixed hand oracle `[320/3,295/3,305/3]`, p10 `[84,91,86]`, p90 `[132,107,122]`, sample 0 `[100,110,90]`; doubled second model and repeated Team ID in another Organization | Passed at `rtol=1e-12, atol=1e-9`; no model/Organization mixing |
| PLOT-02 display paths do not affect statistics | Statistics calculated before the display-column loop from the complete mmap | Parametrized 0, 1, 10 and 99 requested paths against three saved samples | Passed; identical all-sample mean/bands and truthful displayed counts |
| PLOT-03 dates, units, status and assumptions | Per-model subplots, `Total tokens`, saved date axis, qualified/inactive status, partial omissions and assumptions | Numerical tests plus partial/inactive CLI tests | Passed; unavailable-only produces no chart and exit 2 |
| PLOT-04 repeatability/input preservation | Pure figure construction; shared reader only; export outside run | Normalized trace comparison, source hash before/after, outside-run check | Passed; source hash unchanged |
| PLOT-05 real CLI/shell | `cli_plot_forecast.main`; JSON stdout/stderr; exclusive export; portable wrapper | Wrapper invoked from a directory with spaces using relative input/output; help, argument validation, complete/partial/invalid/storage/corruption/no-overwrite cases | Passed; exits 0/2/1 as specified and existing bytes preserved |
| PLOT-06 offline rendering | `Figure.to_html(include_plotlyjs=True)` | Playwright Chromium opened `file://` output with HTTP(S) routes blocked and page-error collection | Passed; zero external requests, zero uncaught page errors, visible model panels |
| PLOT-07 interactions/text safety | Plotly modebar, legend and escaped artifact text | Playwright performed a zoom-box drag, selected Pan and dragged the plot, verified both range changes, clicked Reset axes, toggled legend opacity, invoked hover and checked `Sep 1, 2026 / model-0 / 2.000 total tokens`; hostile `<img ... onerror>` fixture | Passed; `globalThis.pwned` remained undefined |
| PLOT-08 reference workload | Same bounded display logic over mmap arrays | 4 models × 90 days × 1,000 samples; default 10/model; summed live parent/child RSS polling; browser load and interaction; browser trace p10/mean/p90 checked against the fixture's independent 1,000-sample arithmetic | Passed: generation 0.970 s, browser load 0.446 s, peak summed process-tree RSS 245,923,840 bytes, HTML 4,431,526 bytes, interaction below 5 s; all below the 30 s/1 GiB/20 MiB/15 s/5 s release limits |
| PLOT-09 numerical and visual evidence | Unit oracle plus self-contained browser artifact | Automated browser assertions and manual inspection of preserved screenshot | Passed local implementation check; screenshot shows readable model panels, p10–p90 bands, axes, legends and exact hover without clipping or nonfinite axes. Independent reviewer inspection remains required. |
| Release API and organization identity | Exact `build_figure`/`export_html` signatures; CLI omission of Organization `--scope-id` resolves to canonical stored `scope_id=org_id` | `test_organization_cli_omission_resolves_to_org_id`; parent contract clarification | Passed; summary preserves canonical Organization `scope_id` string |

## Commands and results

```text
venv/bin/python -m pytest econ-forecast/tests/utils --collect-only -q
16 tests collected

venv/bin/python -m pytest econ-forecast/tests/utils/test_plot_forecast.py econ-forecast/tests/utils/test_plot_forecast_cli.py -q
14 passed

PLAYWRIGHT_BROWSERS_PATH=/private/tmp/forecast-playwright \
  venv/bin/python -m pytest econ-forecast/tests/utils/test_plot_forecast_browser.py -q -s \
  --basetemp=/private/tmp/forecast-plotly-browser-evidence
2 passed

bash -n econ-forecast/utils/plot_forecast.sh
passed

./econ-forecast/utils/plot_forecast.sh --help
exit 0 without reading a run

venv/bin/python -m pip check
No broken requirements found.

git diff --check -- econ-forecast/src/forecast/utils econ-forecast/tests/utils \
  econ-forecast/utils/plot_forecast.sh
passed
```

The environment was macOS Darwin 24.6.0 arm64, Python 3.14.6, NumPy 2.4.6, Plotly 7.0.0, Playwright 1.62.0, Chromium 151.0.7922.34 and psutil 7.2.2. Browser binaries were provisioned once under `/private/tmp/forecast-playwright`; plotting itself performed no download or network access.

Preserved evidence:

- `/private/tmp/forecast-plotly-browser-evidence/test_offline_render_hover_zoom0/offline plot.html`
- `/private/tmp/forecast-plotly-browser-evidence/test_offline_render_hover_zoom0/offline-plot.png`
- `/private/tmp/forecast-plotly-browser-evidence/test_reference_plot_workload_f0/benchmark.html`

## Linux x86-64 emulation verification

The parent supplied disposable container `talium-forecast-validation-20260914`, constrained to 2 CPUs and 8 GiB, with the repository mounted read-only at `/workspace` and a Python 3.11 environment. It runs Linux x86-64 under emulation on the Apple Silicon host. This verifies the packaged Linux code/browser path under emulation; it is not a native AWS or production-capacity claim.

Chromium and runtime libraries were provisioned only inside the disposable container:

```text
docker exec -e PLAYWRIGHT_BROWSERS_PATH=/tmp/forecast-playwright \
  talium-forecast-validation-20260914 \
  /workspace/venv/bin/python -m playwright install --with-deps chromium
completed; Chromium 151.0.7922.34 installed under container /tmp
```

The interaction test ran with no concurrent benchmark:

```text
docker exec -e PLAYWRIGHT_BROWSERS_PATH=/tmp/forecast-playwright \
  talium-forecast-validation-20260914 \
  /workspace/venv/bin/python -m pytest \
  /workspace/econ-forecast/tests/utils/test_plot_forecast_browser.py::test_offline_render_hover_zoom_reset_legend_and_hostile_text \
  -q -s --basetemp=/tmp/forecast-linux-browser-evidence
1 passed in 9.57s
```

The current strict-reader numerical and CLI fixtures also passed under the container's Python 3.11 environment:

```text
docker exec talium-forecast-validation-20260914 \
  /workspace/venv/bin/python -m pytest -p no:cacheprovider \
  /workspace/econ-forecast/tests/utils/test_plot_forecast.py \
  /workspace/econ-forecast/tests/utils/test_plot_forecast_cli.py -q
14 passed in 23.55s
```

After the parent confirmed other CPU-heavy container tests had finished, the reference experiment ran separately:

```text
docker exec -e PLAYWRIGHT_BROWSERS_PATH=/tmp/forecast-playwright \
  talium-forecast-validation-20260914 \
  /workspace/venv/bin/python -m pytest -p no:cacheprovider \
  /workspace/econ-forecast/tests/utils/test_plot_forecast_browser.py::test_reference_plot_workload_four_models_90_days_1000_samples \
  -q -s --basetemp=/tmp/forecast-linux-plot-benchmark
1 passed in 6.48s
```

Linux-emulation benchmark measurements were generation 2.317 seconds, browser load 1.757 seconds, summed plotting process-tree peak RSS 244,936,704 bytes, and HTML 4,431,526 bytes. The test also passed the independent all-1,000-sample browser trace oracle and every frozen 30-second generation, 1-GiB RSS, 20-MiB HTML, 15-second load, and 5-second interaction bound.

Artifacts copied from the container for inspection:

- `/private/tmp/forecast-linux-browser-evidence/interaction/offline plot.html`
- `/private/tmp/forecast-linux-browser-evidence/interaction/offline-plot.png`
- `/private/tmp/forecast-linux-browser-evidence/benchmark/benchmark.html`

The Linux-emulation screenshot was inspected after copying. It shows both model panels, bands, axes, legend, inert hostile labels, and the expected hover value without clipping, missing data, or nonfinite axes. Native AWS validation remains unclaimed.

## Additional minimal static presentation work

The parent reassigned `forecast/forecasting/presentation.py` and `tests/forecasting/test_presentation.py` to Agent B after the Plotly utility work. This remains the frozen minimal presentation boundary, not the future reporting module.

The implementation now separates `write_charts(root,index,diagnostics)` from `write_report(root,index,diagnostics,summaries,request)` and retains `write_presentation` as a compatibility wrapper. `build_figure` consumes only saved float64 paths and index metadata. It calculates daily means and linear pointwise p10/p90 values, uses `Figure` plus `FigureCanvasAgg` without pyplot/global backend configuration, and uses `AutoDateLocator`/`ConciseDateFormatter` with at most nine ticks for 30- and 90-day horizons. Known layout warnings are captured and emitted through the existing structured logger; exceptions and other failures are not redirected from stderr.

Independent fixture assertions check mean `[320/3,295/3,305/3]`, p10 `[84,91,86]`, p90 `[132,107,122]`, dates, units, titles, inactive assumptions, invalid arrays, actual PNG bytes, report totals/windows/failures/limitations, measured wall/CPU/RSS/stage/output fields, unavailable pre-accounting output bytes, and hostile Markdown HTML escaping. Agent A integrated chart generation before final artifact accounting and report generation after final runtime/output metrics are available.

```text
venv/bin/python -m pytest econ-forecast/tests/forecasting/test_presentation.py -q
7 passed

venv/bin/python -m pytest econ-forecast/tests/forecasting -q
44 passed

git diff --check -- econ-forecast/src/forecast/forecasting/presentation.py \
  econ-forecast/tests/forecasting/test_presentation.py
passed
```

The actual rendered fixture is preserved at `/private/tmp/forecast-presentation-evidence/test_write_charts_renders_png_0/charts/team-payments__model-a.png`. Manual inspection confirmed a readable mean, band, title, units, legend, assumptions and dates with no clipping or nonfinite axes.

Parent inspection of the first real 30-day default chart found the assumptions footer competing with the x-axis label and date offset. The chart now reserves a dedicated bottom layout region, converts internal assumption identifiers to readable labels, and wraps long footers. A representative 30-day regression with a long scope/model title and three assumptions asserts the reserved axes position, readable footer and bounded tick count. The fixed real-data rendering preserves the original plotted values and is available at `/private/tmp/forecast-presentation-layout-evidence/real-team-gpt-5-6-sol-fixed.png`; visual inspection confirms that the footer, x-axis label and right date offset no longer overlap or clip.

## History and allocation contract oracles

The parent later assigned Agent B one new acceptance file, `tests/acceptance/test_history_allocation_contract.py`, to close explicit HIST/ALLOC oracle gaps. Expected dates, statuses, values and fractions are literal hand-derived values; production helpers are called only as the system under test.

| Criteria | Tests and direct oracle |
|---|---|
| HIST-01 | `test_hist01_counts_distinct_dates_not_rows`: four users × 13 dates remains 13 and fails; four users × 14 dates remains 14 and qualifies. |
| HIST-02 | `test_hist02_outage_truncates_continuous_suffix`: August 10 retains 21 days and qualifies, August 25 retains six and fails, August 31 origin outage retains zero and fails. |
| HIST-03 | `test_hist03_fourteen_days_cross_month_boundary`: exact August 25–September 7 date list qualifies. |
| HIST-04 | `test_hist04_first_x3_streak_and_x1_x7_calendar_rules` and `test_hist04_active_day_type_and_bounds_rejected`: exact `0,10,0,20,30,40,0,50` start, X=1, weekday/weekend X=7 failure, and zero/bool/above-lookback validation. |
| HIST-05 | `test_hist05_daily_positivity_aggregates_split_agents` and `test_hist05_scope_models_are_checked_independently`: alternating agents establish daily positivity; qualified and scattered-startup models retain separate outcomes. |
| HIST-06 | `test_hist06_all_zero_floor_is_inactive_but_subminimum_fails`: 14 covered zeros is inactive; 13 is insufficient history. |
| HIST-07 | `test_hist07_qualification_persists_through_taper_retirement_and_restart`: initial qualification survives taper, recent zero retirement becomes inactive, one-token restart remains qualified, and never-qualified weekly use fails X=4. |
| HIST-08 | `test_hist08_leading_zeros_then_seven_vs_fourteen_days_from_start`: leading zeros excluded; seven days from startup fails, 14 passes; future positives cannot establish earlier startup. |
| HIST-09 | `test_hist09_recent_zero_window_vs_one_positive`: recent all-zero allocation returns inactive/no shares; one recent token remains qualified with exact recent category totals. |
| ALLOC-01 | `test_alloc01_one_million_hand_oracle`: 100K/800K/100K produces 0.1/0.8/0.1 and allocates a 500K path as 50K/400K/50K. |
| ALLOC-02 | `test_alloc02_token_weighted_not_daily_percentage_average`: a real neutral 14-day history containing the design's two unequal-volume observations (900 input/100 output, then 90 cache/10 output) produces exact 900/1100, 90/1100 and 110/1100 token-weighted fractions rather than an average of daily percentages. |
| ALLOC-04 | `test_alloc04_recent_window_excludes_old_outage_and_future`: five-day intersection excludes huge old/pre-outage and post-origin values; exact 50/100/150 category totals and 1/6,1/3,1/2 shares. |
| ALLOC-05 | `test_alloc05_fixed_joint_ratios_conserve_every_day_path_including_zero`: two-agent joint ratios reproduce every pair and sum to each multi-day/sample path, including zero totals and zero-share categories. |

```text
venv/bin/python -m pytest econ-forecast/tests/acceptance/test_history_allocation_contract.py -q
19 passed in 0.20s

git diff --check -- econ-forecast/tests/acceptance/test_history_allocation_contract.py
passed
```

## ETS fit-selection contract oracles

The parent assigned a second new acceptance file, `tests/acceptance/test_fit_selection_contract.py`, for the frozen 14/27/28-day selection and deterministic fallback contract. The tests instrument only the numerical-library boundary and assert the attempted settings and diagnostics directly:

- `test_14_and_27_days_attempt_only_level_additive`: both boundaries attempt exactly `(trend=None, seasonal=None, damped=False)`.
- `test_28_days_attempts_weekly_damped_first`: the first attempt is weekly additive seasonality with additive damped trend.
- `test_28_day_fallback_order_is_weekly_damped_weekly_then_level`: injected rejections prove the exact three-step order and retained reasons.
- `test_all_three_numerical_rejections_return_no_fit_and_full_diagnostics`: all rejected attempts return no fit and three explicit failure records.
- `test_attempt_uses_exact_model_settings_and_1000_iteration_cap`: checks additive error, estimated initialization, seasonal period seven, damped-trend mapping, `maxiter=1000`, and `disp=False` at the mocked Statsmodels boundary.
- `test_false_convergence_and_nonzero_warnflag_are_numerical_failures`: false convergence and nonzero warnflag are both rejected while preserving iterations.
- `test_model_exception_is_recorded_as_failed_attempt`: a deterministic numerical exception becomes a meaningful failed-attempt diagnostic.

After parent source review, ALLOC-02 was strengthened to pass the unequal-volume observations through the neutral wire dataset loader and history aggregation, and ALLOC-05 now asserts the independent exact two-agent ratios `[0.2,0.3,0,0.1,0,0.4]` before conservation.

```text
venv/bin/python -m pytest \
  econ-forecast/tests/acceptance/test_history_allocation_contract.py \
  econ-forecast/tests/acceptance/test_fit_selection_contract.py -q
28 passed in 0.89s

git diff --check -- \
  econ-forecast/tests/acceptance/test_history_allocation_contract.py \
  econ-forecast/tests/acceptance/test_fit_selection_contract.py
passed
```
