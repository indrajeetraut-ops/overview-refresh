# Operator report and documentation evidence — 2026-09-17

Status: ready for independent review. This note covers OP-04/05 presentation and current operator documentation, not final parent acceptance. Historical release snapshot JSON/hash files were left unchanged.

## Requirement mapping

| Requirement | Implementation | Check/result |
|---|---|---|
| OP-04 totals table, named scope grouping, distinct model/aggregate | `presentation.write_report`, requested-window group headings and token table | Complete fresh run: 3 scope groups, 12 model rows, 3 aggregates. Saved `period_summary.json` has 15 requested rows; every formatted number and status matched the report. |
| OP-04 evidence table and reasons | `presentation.write_report`, grouped model/evidence table and adjacent notes | Complete run: 12 series; real partial trial: 15 series including 5 `correction_failed` with saved history/sampling windows and reasons. Every saved index status/diagnostic date/reason matched the report. |
| OP-04 missing values and partial/inactive | Missing requested numeric summaries create presentation-only unavailable model and incomplete aggregate rows; inactive saved zero summaries remain explicit | Real partial trial project `cloud-cost`: 5 unavailable model rows and one unavailable incomplete aggregate, no fabricated zero. Tests include real `run_forecast` partial with empty summary, inactive, complete, qualified and fit-failed cases. |
| OP-04 escaping and formatting | `_safe` escapes HTML, Markdown pipes, newlines and backslashes; `_number` formats finite numbers with commas and three decimals | Unit tests cover hostile Organization/scope/model names, null sampling, exact numeric strings, aggregate labels. |
| OP-01/02/03 current documentation | Operator guide and module READMEs/functional/detailed designs use name-based inputs and omitted-origin behavior; internal artifact IDs remain documented as stored metadata | Fresh copied-config workflow completed with name-only Team/Project configs, omitted origin resolved to 2026-08-29, three scoped Plot exports succeeded. Migration table explains config changes without rewriting saved datasets. |
| OP-05 concise guide, defaults, equations, tree | `docs/operations/README-forecast.md` | Maintained series YAML defaults matched parameter table (30%, 0.80 ±5 percentage points, 0.03, 0.10/0.60, 0.25). Generated file tree matched actual outputs. Four relative links exist. Markdown fences balanced. |

## Exact fresh workflow

Workspace: `/tmp/forecast-guide.5s8xUa` (canonicalized as `/private/tmp/forecast-guide.5s8xUa` in some logs). The exact guide sequence copied maintained synthetic/forecast YAML to this new workspace, ran:

```bash
./econ-forecast/utils/synt_gen.sh --op user-create --config "$WORK/config/synthetic/users.yaml" --output "$WORK/artifacts/users"
./econ-forecast/utils/synt_gen.sh --op roster-create --config "$WORK/config/synthetic/roster.yaml" --output "$WORK/artifacts/roster"
./econ-forecast/utils/synt_gen.sh --op series-create --config "$WORK/config/synthetic/series.yaml" --output "$WORK/artifacts/series-60d"
# Then appended {type: project, name: payment_service_improvement} to the copied forecast YAML as in the guide.
./econ-forecast/utils/forecast.sh --config "$WORK/config/forecasting/forecast.yaml" --unit month --offset 1 --periods 1 --validate-only
./econ-forecast/utils/forecast.sh --config "$WORK/config/forecasting/forecast.yaml" --unit month --offset 1 --periods 1 --output "$WORK/artifacts/september-forecast"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type organization --sample-paths 0 --output "$WORK/artifacts/organization.html"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type team --scope-name team1 --model claude-sonnet-5 --sample-paths 10 --output "$WORK/artifacts/team1-claude.html"
./econ-forecast/utils/plot_forecast.sh --run "$WORK/artifacts/september-forecast" --org-id talium.ai --scope-type project --scope-name payment_service_improvement --model claude-sonnet-5 --model gpt-5.6-sol --sample-paths 1 --output "$WORK/artifacts/project-two-models.html"
```

All commands exited 0. Forecast result was `complete`, 3/3 scopes, 12 series, origin 2026-08-29. Organization/Team/Project HTML outputs each reported `complete`; their full sample count was 1,000 and displayed counts were 0, 10, and 1 respectively. Actual output directories contain the guide's listed files and nested `inputs/` snapshots. Forecast paths have 12 `.npy` files and charts have 12 PNG files. The report is at `/tmp/forecast-guide.5s8xUa/artifacts/september-forecast/report.md`.

## Partial saved-result inspection

Parent generated a real analytical partial run from the existing trial dataset at `/private/tmp/op-parent-trial-qb0p383f/forecast`. It completed with Organization and Team `core` totals, while Project `cloud-cost` had five `correction_failed` models and no saved numeric summary for that Project. To preserve the original manifest-bound run, the latest presentation code re-rendered its saved index, diagnostics, summary and request into `/tmp/operator-partial-report-review/report.md`. The resulting totals table shows all five models with `unavailable` numeric cells and an `incomplete` scope aggregate; reasons remain next to those rows. A comparison script checked all 12 saved numeric requested summaries, 15 series statuses, available dates and reasons against this rendered report. The same check passed for all 15 saved numeric requested summaries and 12 series in the complete guide run. No numeric values were recomputed by presentation.

## Rendering and tests

```bash
venv/bin/python -m pytest econ-forecast/tests/forecasting/test_presentation.py -q
# 11 passed
```

The Markdown guide and both reports were parsed with `markdown-it-py` table support and rendered to `/tmp/operator-guide-review.html`, `/tmp/operator-complete-report-review.html`, and `/tmp/operator-partial-report-review.html`. Each report produced seven HTML tables; the guide produced seven tables and six code blocks. Source Markdown and HTML are ready for visual review. Screenshot capture was blocked in this agent's sandbox: bundled Playwright Chromium was absent, and launching system Chrome headless exited SIGABRT. Parent/reviewer visual inspection remains required.

`git diff --check` passed after documentation edits. No commit, push, snapshot regeneration, or saved-run artifact modification was performed.
