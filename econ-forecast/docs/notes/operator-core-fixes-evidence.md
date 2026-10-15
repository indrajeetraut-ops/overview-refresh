# OP-01 through OP-03 implementation evidence

Status: ready for independent review. This note records implementation checks, not final acceptance.

| Requirement | Implementation | Verification and observed result |
|---|---|---|
| OP-01: omitted origin uses the declared dataset end, including covered zero days | `forecasting/runner.py` resolves `ForecastRequest(origin=None)` after loading declared dataset metadata; `cli_forecast.py` makes `--origin` optional. `ForecastResult.origin`, the structured origin log, and saved `request.json` expose the resolved date. | `test_omitted_origin_uses_declared_covered_zero_and_explicit_cutoff`: final day has complete coverage and no usage; origin and history end both equal `2026-08-29`. Trial's unchanged old dataset resolves to declared end `2026-06-28` in omitted-origin validation. |
| OP-01: explicit cutoff excludes later evidence | The existing history builder receives the explicit origin; calendar resolution still rejects excessive horizons before dataset ingestion for explicit origins. | Same acceptance test: August 20 cutoff gives exact recent total 2,800 tokens; omitted August 29 cutoff gives 81,900 tokens from later observations. Existing request/resource tests retain horizon checks. |
| OP-01: final outage does not move origin earlier | Coverage eligibility remains in `forecasting/history.py`. | `test_omitted_origin_keeps_final_outage_ineligible`: declared end remains `2026-08-29`, validation status is invalid with one rejected scope. |
| OP-02: team names are sole operator inputs | `synthetic/config.py` accepts `team_name` but rejects `team_id`; `forecasting/config.py` requires scope `name`, and `runner.py` resolves that name within the requested Organization to saved internal ID. | `test_names_resolve_saved_ids_and_reject_ambiguity_within_organization` uses `Team A` with stored ID `team-a`; existing cross-Organization scope test uses the same name in two Organizations. Trial's `core` resolves to saved ID `32` without editing the dataset. Unknown names produce an actionable error. |
| OP-03: project definitions/mixtures use names | `synthetic/config.py` accepts `{name}` project entries and name-keyed `project_mix`. `synthetic/series.py` derives stable internal UUIDs, while `forecasting/dataset.py` reads both name-only and legacy ID-keyed saved series snapshots. | `test_workflow_and_replay` compares generated/replayed project IDs and rewrites a saved snapshot into legacy numeric project IDs and ID-keyed mixtures; `load_dataset` still resolves all five saved names. Scope acceptance test selects `Project A` with stored ID `project-a` and rejects same-Organization name ambiguity. |
| OP-02/03: Plotly uses saved names | `utils/cli_plot_forecast.py` exposes `--scope-name`; `utils/plot_forecast.py` resolves it through saved index scopes within Organization and rejects unknown or ambiguous names. | Plot unit tests cover unknown, ambiguous, and cross-Organization names. The unchanged trial's old saved `core-team-forecast` produced `/tmp/operator-trial-core-plot.html` using `--scope-name core`: complete, 1,000 full samples, one displayed path, 4,305,874 output bytes. |

Commands and results:

```text
venv/bin/python -m pytest econ-forecast/tests/synthetic econ-forecast/tests/forecasting econ-forecast/tests/utils/test_plot_forecast.py econ-forecast/tests/utils/test_plot_forecast_cli.py econ-forecast/tests/acceptance/test_scope_contract.py -q --tb=short
174 passed in 16.66s

venv/bin/python -m pytest econ-forecast/tests/synthetic/test_core.py econ-forecast/tests/acceptance/test_scope_contract.py econ-forecast/tests/acceptance/test_forecast_workflow.py econ-forecast/tests/utils/test_plot_forecast.py -q --tb=short
38 passed in 16.88s

./econ-forecast/utils/forecast.sh --config /tmp/operator-trial-name-only.yaml --unit month --offset 2 --periods 1 --validate-only
exit 0; valid; 2 requested/completed scopes; 10 series; origin 2026-06-28

./econ-forecast/utils/plot_forecast.sh --run /Users/sumandra/projects/talium/forecasting/forecast-trial/artifacts/core-team-forecast --org-id talium.ai --scope-type team --scope-name core --model gpt-5.6-sol --sample-paths 1 --output /tmp/operator-trial-core-plot.html
exit 0; complete; scope_name core; 1,000 full samples; 4,305,874 bytes
```

The broad acceptance run in my restricted environment had 100 passes. Its resource benchmark could not inspect child processes because macOS denied `psutil`'s `sysctl` process list (`PermissionError`). The one wrapper assertion initially expected the Organization ID where the name-only Plotly summary now correctly returns `Organization A`; that assertion was corrected and the affected wrapper test passed in the targeted rerun. Browser interaction could not run in that restricted environment because `PLAYWRIGHT_BROWSERS_PATH` was unset.

The parent subsequently ran the complete suite with approved macOS process access and provisioned headless Chromium:

```text
PLAYWRIGHT_BROWSERS_PATH=/private/tmp/forecast-playwright venv/bin/python -m pytest econ-forecast/tests -q -s --basetemp=/private/tmp/operator-fixes-parent-suite
258 passed in 41.76s
```

The resource benchmark measured cold/repeat durations 10.5776/10.9845 seconds, sampled RSS 334,462,976/338,903,040 bytes, and output about 55.48 MB. Plot generation took 0.2932 seconds; browser load took 0.4482 seconds, with 113,704,960 bytes RSS and 4,431,526-byte HTML. The parent acceptance note records the final cross-workstream review and visual checks.

The operator guide's separate fresh name-only workflow generated users, roster, series, a complete three-scope forecast with omitted origin `2026-08-29`, and Organization/Team/Project plots under `/tmp/forecast-guide.5s8xUa`. It produced 12 `.npy` path arrays and 12 PNG charts. The guide evidence checked 15 requested summary rows and 12 series against saved JSON; see `operator-report-docs-evidence.md` for exact commands and artifact inspection.
