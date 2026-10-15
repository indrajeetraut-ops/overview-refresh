# Parent validation and acceptance evidence

Updated 2026-09-14. Checkout `/Users/sumandra/projects/talium/forecasting/code-econ`, branch `forecast`. Major implementation and test additions were delegated to GPT-5.6-sol agents. Parent reviewed production/test code, coordinated corrections, ran independent checks, and owns acceptance. No forecast commit or push has been made.

## Delivered behavior

Daily token forecasting supports Organization, Team, and Project scopes from a shared synthetic or neutral daily dataset. It implements coverage/startup/history checks, ordered additive ETS fitting, aligned residual simulation, fixed joint allocation shares, strict saved-path readers, versioned summaries, diagnostics, Markdown reports and PNGs. Forecast and Plotly modules have callable interfaces, named CLIs, companion shell scripts, commented configuration, module READMEs, and structured CLI logs. Plotly reads saved paths into offline interactive HTML.

Budget/scenario calculations, the full reporting module, PostgreSQL session-to-daily preparation, subscription pricing, and accuracy calibration against representative real observations remain outside this implementation. The documented $40K example explains downstream use; it is not an implemented budget command.

## Stable final suites

The complete module test directory includes existing synthetic regression, core numerical/unit checks, independent acceptance, real wrappers, resource benchmarks, Plotly numerical/CLI checks, and actual offline browser interactions.

| Environment | Command | Observed result |
|---|---|---|
| Mac ARM64, macOS 15.7.4, Python 3.14.6 | `PLAYWRIGHT_BROWSERS_PATH=/private/tmp/forecast-playwright venv/bin/python -m pytest econ-forecast/tests -q -s --basetemp=/private/tmp/forecast-parent-final-mac` | **244 passed in 48.00s** |
| Linux x86_64 emulation, Python 3.11, 2 CPU / 8 GiB | `PLAYWRIGHT_BROWSERS_PATH=/tmp/forecast-playwright /workspace/venv/bin/python -m pytest econ-forecast/tests -q -s -p no:cacheprovider --basetemp=/tmp/forecast-parent-final-linux` | **244 passed in 95.19s** |

These runs used stable production code. Final review subsequently added test-only null-category, stale-origin, existing-output-preservation and independent ETS recurrence checks; their supplemental results are recorded below when verified. No changed production behavior is accepted solely from an earlier run.

Mac test artifacts: `/private/tmp/forecast-parent-final-mac`. Linux full log: `/private/tmp/forecast-parent-final-linux.log`; copied test artifacts: `/private/tmp/forecast-parent-final-linux-artifacts`. The original Linux paths refer to the disposable validation container and are not permanent storage.

`pip check` passed on both environments. `git diff --check` and `bash -n` for the two new wrappers passed. Forecast and Plotly `--help` were executed outside the repository; forecast help now documents cutoff, calendar offsets, periods, YAML scope, and validate-only behavior.

An earlier overlapping-edit run reported 1 failed / 240 passed in the CLI help test. A verified the final normalized-help assertion and the stable 244-test run passed. A sandboxed resource run could not enumerate processes; it was not accepted. The harness now requires positive RSS samples, and final resource/browser suites ran with the needed permissions. These environment/earlier results do not replace final evidence.

## Parent principal shell workflow and output inspection

Actual shell stages user-create → roster-create → series-create ran from `/private/tmp` using paths containing spaces. Inputs are retained at `/private/tmp/forecast parent acceptance ui459068`: **8,888 usage rows and 6,000 complete coverage records**. Independent checks confirmed integer/nonnegative counters and synthetic output/cache bounds against processed input. Existing synthetic regression remains part of both full suites.

Final parent forecast output: `/private/tmp/forecast parent acceptance ui459068/forecast-final-2ac2c460`. Command used origin `2026-08-29`, month offset `1`, periods `1`, 1,000 samples, and the generated series via `configs/forecast.yaml`. Exit 0; two scopes and eight model series complete.

A separate parent NumPy/JSON/hash script, without using production summary/allocation/reader helpers, verified:

- Eight float64 `(30,1000)` arrays with finite nonnegative values and canonical Organization identity.
- 240 daily rows: category/agent shares conserve each model's actual row mean.
- 20 summary rows: period totals and linear quantiles match sums of corresponding sample columns across dates and models.
- All 24 manifest-listed files have matching byte counts and SHA256 hashes; actual file inventory matches the manifest plus the manifest itself.
- Diagnostic output bytes equal the sum of every manifest-listed file, including final report/diagnostics and excluding the manifest.
- CLI logs parse as JSON and carry the same run ID as the manifest.

Inspection script: `/private/tmp/forecast_parent_inspect.py`. Shell command/stdout/stderr evidence is saved alongside the run. Parent visually inspected the final `charts/team-team1__gpt-5-6-sol__725634798d132b21.png`: readable dates, labels, uncertainty band and assumptions footer without clipping.

The real Plotly wrapper read this run and generated `/private/tmp/forecast parent acceptance ui459068/parent-final-interactive.html`: four selected models, all 1,000 samples used for statistics, ten displayed paths, **4,358,132 bytes**. Input checksums remained valid. Independent browser tests additionally exercised offline loading, hover, zoom/reset, visibility controls, hostile text escaping, no external requests/errors, and screenshot checks.

## Resource and platform interpretation

The frozen forecast benchmark uses 100 model series, 90 historical dates, 90 internal forecast dates, 62 saved dates, and 1,000 paths. Both final full suites executed cold/repeat runs and enforced **≤300 seconds, ≤2 GiB sampled process-tree RSS, ≤200 MiB output**, positive RSS measurements, and exact array/index dimensions. Earlier independently recorded cold/repeat measurements were 13.455/12.621 seconds on Mac and 17.034/16.476 seconds in Linux emulation, with RSS below 351 MB; full details are in the independent-review note.

Plotly's four-model/90-day/1,000-sample experiment passed all generation, browser load/interaction, memory and HTML-size bounds on both platforms. The final Mac browser experiment measured 0.331s generation, 0.498s browser load, 119,717,888 bytes process RSS and 4,431,526 HTML bytes. Linux measured 1.200s generation, 2.220s load, 103,612,416 bytes process RSS and the same HTML size.

Linux ran in a task-only `python:3.11-slim` x86_64 container with 2 CPU / 8 GiB on an ARM Mac. **This is emulated Linux compatibility/resource evidence, not native AWS performance.** No AWS infrastructure was created. Measure the representative workload on the chosen AWS instance before making a performance commitment.

NumPy 2.4.6 / SciPy 1.17.1 replaced initial pins that excluded Python 3.11; the exact dependency amendment was verified by every agent. Parent reran five affine/constant numerical preflight probes after the change. The full pinned dependency set installed and passed `pip check` on both tested environments.

## Independent review and parent findings

C independently inspected source and coverage, authored wire fixtures and acceptance oracles, ran tests and browsers, checked B's tests, and rechecked corrections. Parent inspected runner memory lifetime, per-scope persistence, final manifest publication, token/history/ETS/path behavior, oracle quality, real outputs and documentation. Corrections included bounded path persistence, one-sample array shape, strict metadata/input validation, future evidence exclusion, inactive allocation handling, import isolation, final byte accounting, honest resource sampling, readable chart layout, CLI help and accurate README JSON schemas.

The [acceptance matrix](forecasting-acceptance-matrix.md) maps all 37 named design criteria and 24 functional scenarios to production code, exact tests and independent checking methods. C's review found no open production defect. All review findings and matrix corrections are closed. Parent accepts the implementation against the agreed forecasting and local Plotly contract, with the explicit downstream/AWS/calibration boundaries above.

## Supplemental verification and final decision

After the stable full suites, seven test-only cases were added; production code did not change:

```text
venv/bin/python -m pytest econ-forecast/tests/acceptance/test_input_failure_contract.py econ-forecast/tests/acceptance/test_ets_recurrence_contract.py -q
7 passed in 1.37s (Mac)

/workspace/venv/bin/python -m pytest econ-forecast/tests/acceptance/test_input_failure_contract.py econ-forecast/tests/acceptance/test_ets_recurrence_contract.py -q -p no:cacheprovider
7 passed in 1.66s (Linux emulation)
```

Thus **251 distinct cases passed on each platform (244 full-suite + 7 supplemental)**. C independently inspected and executed both additions, confirmed the hand recurrence `[57.0,57.8,60.8]`, and verified all final matrix aliases/citations. There are no open implementation findings or required test gaps. Budget runtime and native AWS performance remain outside this acceptance, not implicitly passed.

The task-only Linux container `talium-forecast-validation-20260914` was removed after preserving its final log/artifacts. No unrelated containers or AWS resources were changed. Work remains uncommitted on branch `forecast`.
