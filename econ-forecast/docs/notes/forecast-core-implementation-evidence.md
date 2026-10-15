# Forecast core implementation evidence

> Historical evidence for the earlier ETS implementation. Current model evidence is in [the weekday-resampling design](../design/forecasting-weekday-bootstrap.md) and current acceptance tests.

Date: 2026-09-14. Owner: Agent A. Status: implementation complete and awaiting
independent acceptance. This note records implementation evidence; it is not an
acceptance decision.

## Delivered core

`forecast.forecasting` implements strict config/request validation, streamed neutral
and synthetic ingestion, coverage-aware histories, additive statsmodels ETS fitting,
joint residual paths, allocation and summaries, atomic completed-run storage, a
strict memory-mapped reader, JSON CLI channels, bounded workers/numerical threads,
resource observation, and the shell wrapper. Exact-identical inputs use analytic
ETS smoothing with zero innovations. Other inputs use affine conditioning without a
low-variation gate or invented noise. Warning-sensitive statsmodels calls serialize
through a process-wide reentrant lock; scope preparation and artifact work remain
bounded-parallel.

## Criterion mapping

| Criterion | Production evidence | Executable evidence |
|---|---|---|
| HIST-01–03 | `dataset.py`, `history.py` count continuous covered dates and truncate at outages | `test_history_allocation_contract.py` minimum/outage/calendar oracles |
| HIST-04–05 | `history.py` finds the first coverage-gated daily aggregate streak per scope/model | startup validation, split-agent, and independent-model tests in `test_history_allocation_contract.py` |
| HIST-06–09 | `history.py` separates insufficient history/startup, established inactivity, and recent low use; future evidence is ignored | inactive-floor, persistence, first-start, recent-zero/positive and outage-positive tests in acceptance plus `test_history_boundary_statuses` |
| ETS-01 | `ets.py:Fit.simulate` fixes `anchor="end"`, explicit errors, finite shape, and one-path 2-D normalization | real weekly replay/zero-error, constant/nearconstant, and one-path tests in `test_core_numerics.py`; CLI one-path integration |
| ETS-02 | `ets.py:fit_ets` selects level-only below 28 days and ordered seasonal fallbacks at 28+, each capped at 1,000 iterations | real and injected fallback tests in `test_core_numerics.py` and independent acceptance control-flow coverage |
| PATH-01–02 | `paths.py` samples centered aligned residual rows with a stable scoped seed and preserves internal offset dates | opposing-vector oracle, same-seed replay, worker/scope-order replay, and future-row acceptance tests |
| PATH-03–04 | `paths.py` validates finite output and computes correction only over requested dates | exact `[-5,40,60]` 0.05 boundary and zero/all-negative/nonfinite/offset tests |
| PATH-05 | `paths.py` intersects active models' residual dates without shortening their fits | independent 90/21 shared-window and 14/13 boundary acceptance test |
| ALLOC-01–05 | `allocation.py` uses recent joint agent/category token sums and conserves fixed shares | hand arithmetic, weighting, scope-window and conservation tests in `test_history_allocation_contract.py` plus joint-agent unit oracle |
| ALLOC-06 | `allocations.json`, diagnostics, report, and module README record dates, evidence, shares, and assumptions | hand-oracle workflow and report/presentation tests |
| RESULT-01 | `runner.py:_scope_status` retains every applicable model and derives complete/conditional/inactive/incomplete risk state | history-state, hand-oracle, validate-only and CLI exit tests |
| STORE-01 | `schemas.py`, producer index, and `artifacts.py` validate identity, shape, dtype, hash, status and read-only mmap semantics | reader roundtrip, path=1, hostile/malformed index and filename-collision acceptance tests |
| STORE-02 | runner computes daily row means and period quantiles from column totals, aggregating model columns before scope quantiles | independent hand-array conservation and presentation summary tests |
| STORE-03 | output starts at a new path, failures remove staging, and `manifest.json` is atomically replaced last | no-overwrite, checksum, semantic-reader, late-write cleanup and replay tests |
| CLI/resources | CLI writes one summary to stdout and structured stage logs to stderr; runner limits workload, workers and BLAS threads; `ResourceMonitor` samples process-tree RSS/CPU | wrapper-from-spaced-CWD, failure-channel, monitor lifecycle and frozen reference benchmark tests |

## Commands and observed results

Source contract checks:

```text
$ shasum -a 256 -c econ-forecast/docs/notes/forecasting-release-snapshot.sha256
all listed sources: OK
$ shasum -a 256 -c econ-forecast/docs/notes/forecasting-release-amendment-1.sha256
requirements.txt: OK
econ-forecast/docs/notes/forecast-numerical-preflight.md: OK
```

Current core and independent acceptance run:

```text
$ venv/bin/python -m pytest econ-forecast/tests/forecasting econ-forecast/tests/acceptance -m 'not slow' -q
145 passed, 1 deselected in 20.88s
```

An actual wrapper validate-only run against an insufficient-history fixture returned
exit 2, wrote exactly one JSON summary with `status="invalid"` to stdout, wrote five
structured stage events to stderr, created no output directory, and labeled numerical
eligibility `not_run`.

Focused real ETS one-path workflow:

```text
$ venv/bin/python -m pytest econ-forecast/tests/forecasting/test_runner_integration.py::test_end_to_end_single_path_axis_for_constant_and_varying -q
2 passed in 8.65s
```

The retained varying artifact was under the pytest temporary tree at
`pytest-69/test_end_to_end_single_path_ax1/output`. Public-reader inspection found
status `complete`, ten hash-indexed files, shape `[30,1]`, read-only mmap paths, and
exact agreement between `diagnostics.runtime.output_bytes` and the sum of all
manifest-listed artifact byte counts. Its stages included config, input, summed fit
worker wall, summed simulation worker wall, coordinator model/path wall, summaries,
artifact staging, and charts.

Parent-coordinated reference evidence reports the frozen 100-series/90-history-day/
90-horizon-day/1,000-path workload within its 300-second, 2 GiB RSS and 200 MiB output
limits on macOS and in an emulated Linux x86-64 Python 3.11 container constrained to
2 CPU/8 GiB. This does not establish native AWS performance. Browser checks require
the separately provisioned Playwright environment and are recorded by the Plotly
owner; the ordinary sandbox lacks `PLAYWRIGHT_BROWSERS_PATH` and denies process-list
enumeration.

Runtime versions recorded by completed manifests are NumPy 2.4.6, SciPy 1.17.1,
statsmodels 0.15.0, plus the pinned pandas, matplotlib, Plotly, psutil, and
threadpoolctl versions. The numerical affine/constant probe was rerun after the
Python 3.11-compatible NumPy/SciPy amendment and passed all five parent checks.

## Measurement boundaries

`fit` and `simulation` stage values are summed worker wall time and can exceed total
run wall time. Other stages are coordinator wall time. Sampled RSS can miss
allocations shorter than 50 ms; the process-lifetime OS high-water mark is also
stored. Wall/CPU sampling ends before the final diagnostics/report fixed-point and
atomic manifest publication to avoid changing published artifacts after completion.
`output_bytes` is exact for every manifest-listed artifact and excludes the manifest
itself to avoid recursive byte accounting.
