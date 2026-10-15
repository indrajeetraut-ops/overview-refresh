# Econ Forecast test results

This directory makes dated test runs reviewable without terminal scrollback.

- [`index.md`](index.md) lists every recorded run for people.
- [`index.json`](index.json) is the machine-readable run index.
- Each `runs/<UTC timestamp>_<run ID>/` directory contains `report.md`,
  `junit.xml`, and `result.json`. The report gives totals by module and one row
  per test. JUnit can be imported into CI and test-report viewers. The JSON
  result records the full UUID, timestamps, command, counts, status, and any
  unverified tests.
- `forecasting-test-report.md` and `forecasting-junit.xml` are the one-time
  report created before automatic indexed runs were introduced.

## What the tests cover

| Test area | Purpose |
|---|---|
| `acceptance/test_forecast_*` | Calendar requests, complete CLI workflow, saved artifacts, conservation, deterministic replay, invalid inputs, and resource limits. |
| `acceptance/test_history_allocation_contract.py` | Coverage-aware history selection, startup rules, inactivity, and allocation arithmetic. |
| `acceptance/test_scope_contract.py` | Organization, Team, and Project attribution and readiness behavior. |
| `acceptance/test_user_reduction_contract.py` | Independent per-user weekend reduction before scope aggregation. |
| `acceptance/test_weekday_config_contract.py` | Required explicit configuration, value boundaries, Team overrides, and rejection of the former ETS configuration. |
| `forecasting/test_*` | Weekday-resampling arithmetic, request parsing, runner behavior, presentation, imports, and runtime accounting. |
| `synthetic/test_*` | Synthetic configuration, generation behavior, spikes, weekend reduction, repeatability, artifacts, and failure handling. |
| `utils/test_plot_forecast.py` and `test_plot_forecast_cli.py` | Plot selection, all-sample statistics, Project panels, CLI behavior, and saved-run integrity. |
| `utils/test_plot_forecast_browser.py` | Offline rendering, hover, zoom, legend interaction, hostile text, default graph, all-Project panels, and browser resource behavior. These four tests are not in the current report because Playwright Chromium was unavailable. |

Each test row uses its pytest name as the concise purpose. Parameterized
values appear in square brackets in the test name. Exact fixtures, input
values, and expected results remain in the referenced test source.

## Current recorded result

The first automatic run on 2026-09-19 executed 287 tests: 287 passed, 0 failed,
and 0 skipped. Its status is `passed_with_unverified` because four browser
tests were not executed. Open [`index.md`](index.md) for its run ID, completion
time, report, and JUnit file.

## Regenerate

From the repository root, use the canonical wrapper:

```bash
econ-forecast/utils/test_forecast.sh
```

The wrapper always creates a UTC timestamp and UUID, runs pytest, generates
JUnit/Markdown/JSON, and updates both indexes. It returns pytest's exit code,
so failures are recorded and still fail a shell or CI job. The standard mode
excludes the four browser tests and records each as unverified. When Playwright
Chromium is available, include them in the same recorded run:

```bash
econ-forecast/utils/test_forecast.sh --include-browser
```

Use `--output-root PATH` when CI should publish the same structure outside the
source tree.
