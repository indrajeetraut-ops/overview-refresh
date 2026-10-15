# Weekday Resampling Handoff

**Updated:** 2026-09-19
**Repository:** `/Users/sumandra/projects/talium/forecasting/code-econ`
**Branch:** `forecast`

## Agreed behavior

- Forecast normal daily usage by resampling observed covered weekdays at the user level. This replaces ETS, automatic weekly seasonality, and negative-value clipping.
- Preserve observed nonnegative usage, zeros, and spikes.
- For every simulated weekend day, independently apply a reduction event to each user. A drawn event multiplies that user's sampled usage by `reduction_factor`; it never forces usage to zero.
- Treat usage without a user ID as one contributor using the global weekend rule.
- Apply Team overrides per user. A Team override must provide both probability and factor.
- Synthetic generation uses the same semantics. Ordinary daily inactivity remains separate from weekend reduction.
- All behavior-controlling values must be explicit in YAML; the loader must not supply hidden defaults. The agreed example defaults are probability `0.6` and factor `0.25`. The trial `dev-ops` override uses probability `0.01` and factor `0.25`.

## Main files

- Forecast config: `econ-forecast/config/forecasting/forecast.yaml`
- Synthetic config: `econ-forecast/config/synthetic/series.yaml`
- Current model design: `econ-forecast/docs/design/forecasting-weekday-bootstrap.md`
- Functional specification: `econ-forecast/docs/design/forecasting.md`
- Detailed design: `econ-forecast/docs/design/forecasting-design.md`
- Operator guide: `econ-forecast/docs/operations/README-forecast.md`
- Forecast implementation: `econ-forecast/src/forecast/forecasting/weekday_bootstrap.py`
- Forecast module guide: `econ-forecast/src/forecast/forecasting/README.md`

The trial configuration is outside this repository at `/Users/sumandra/projects/talium/forecasting/forecast-trial/config` and was updated separately.

## Validation and current state

- A zero weekend-reduction-probability experiment removed the weekend sawtooth, confirming that the configured weekend event produces the pattern.
- Spike inspection found that spikes are present in individual paths but are smoothed in the mean of 1,000 paths. The configured synthetic spike probability determines how exceptional they appear.
- Earlier non-browser checks passed. Browser plotting tests remain unverified because Playwright Chromium was unavailable. Do not claim they passed.
- The test-report automation is committed as `90932c5` (`test(forecast): persist indexed test reports`).
- The model, config, documentation, plotting, synthetic, and related test changes remain uncommitted.

## Working constraints

- Do not write to `econ-forecast/docs/explore` unless the user explicitly asks to resume exploration notes.
- Ask the user before running another full test suite or independent review. Run only focused checks otherwise.
- Do not commit additional work unless requested.
