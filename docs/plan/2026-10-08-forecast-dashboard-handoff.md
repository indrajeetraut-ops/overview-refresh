# Forecast in the dashboard: handoff for T4

Written 2026-10-08 for the session that picks up T4. Issue #85.
Plan and contract: [2026-10-05-forecast-dashboard-integration.md](2026-10-05-forecast-dashboard-integration.md). Read it first; this note only adds what the plan does not say.

## Where things stand

| | |
|---|---|
| Branch | `feat/forecast-dashboard-integration-2`, local only, 16 commits ahead of `main` (`f76471a`). Not pushed; pushing needs the user's say-so. |
| Done | T0 (issue #85), T1, T2 (example org: 120 days, budgets $200 per developer, usage curves on two teams), T3 (`forecast.dashboard`: presets and runs). |
| Next | **T4: `forecast.dashboard` export and CLI.** T5–T8 are not started. |
| Decisions | D1–D3 and D5–D12 confirmed by the user on 2026-10-08. D4 (calibration) is dropped; §3.3 is intentionally empty. |

## How the user wants the work done

- `talium-impl-workflow` for each task: written brief, sonnet implementer, handshake before any code, tests, persisted report, coordinator review with a Major / Minor list, then land.
- **An independent verifier subagent after every task** (user, 2026-10-08). On T3 it ran three passes: verify against the plan and brief by reproducing, send Major findings back to the implementer, fix small ones directly, resume the same verifier to re-check the fixes. It must not edit the repo.
- Scoped commits, each ending `Refs #85`. No AI-tool attribution anywhere. Never `git -C`. Do not push.
- When a task is done: mark it in the plan (§4, status row) and give the user a short summary with the measured numbers.

## Environment

- A new worktree has no `venv`. Create it and install **both** requirements files before running anything:
  `python3 -m venv venv && venv/bin/pip install -r requirements.txt -r econ-forecast/requirements.txt`.
  If an `econ-forecast/utils/*.sh` wrapper runs first, it creates a lean `venv` without tree-sitter and 126 tests under `tests/analysis/causal_graph` fail.
- Tests: `venv/bin/python -m pytest <path> -q -p no:cacheprovider`. Known unrelated failure: `econ-forecast/tests/utils/test_plot_forecast_browser.py` (Playwright's Chromium is not installed). Database-backed tests skip without `TALIUM_TEST_DSN`.
- Example dataset for acceptance runs (about 5 minutes, 217 MB; write it to a scratch directory, never into the repo):
  `econ-forecast/utils/synth_org.sh --config-dir econ-forecast/config/synthetic/org --output <scratch>/org-demo --end-date today`.
  The series step alone takes 4 seconds (`synt_gen.sh --op series-create`), so anything that needs only `series/` can iterate without the full chain.

## What T3 left for T4 to build on

Package `econ-forecast/src/forecast/dashboard/`:

- `run_dashboard_forecast(series_dir, presets_path, output_dir, *, forecast_template=None, force=False, catalog=None) -> DashboardRunResult`. `series_dir` is `<org-demo>/series`.
- `DashboardRunResult`: `data_through`, `origin`, `org_id`, `output_dir`, `forecast_dir`, `presets` (tuple of `PresetSpec`), `cost_dirs` (preset id → directory), `scopes` (tuple of `RunScope(scope_type, name)`; the organization's name is its org id).
- Output layout: `forecast.yaml`, `scenarios/<id>.yaml`, `forecast/`, `cost/<id>/`. On the example dataset: 10 scopes, 7 cost runs, about 19 seconds.
- Errors: `forecast.dashboard.errors.InputError` (CLI exit 2) and `ExecutionError` (exit 1), with `stage`.
- `DEFAULT_PRESETS_PATH` → `econ-forecast/config/dashboard/presets.yaml` (schema `dashboard-presets/1.0`).
- Tests: `econ-forecast/tests/dashboard/` (95). `test_runner.py` has module-scoped fixtures that build a small Layer 1 dataset and a pinned, tiered price catalog; reuse them rather than writing new ones. Report: `docs/test/reports/forecast-dashboard-{junit.xml,test-report.md}`, regenerated with `utils/gen_test_report.py`.

## Points to settle in the T4 brief

1. **Export shape.** §3.4's JSON example predates §3.8. The "Additions" table at the end of §3.8 wins: `periods[i].cumulative` carries one `quantile_grid` row per date (`low` / `high` are grid entries, not separate arrays), `periods[i].tokens_by_model` is added, and there is no `calibration`.
2. **Package boundary against §3.2.** T3 kept `forecast.dashboard` free of `forecast.synthetic.*` imports. §3.2 derives a team's `scope_id` from `forecast.synthetic.org.ids.team_slug`. Either allow that one import in `scopes.py` or map by name through `org/directory_teams.jsonl`; say which in the brief. Facts from a generated org: `org/budgets.jsonl` has the org row with `scope_id` `default`; `org/directory_teams.jsonl` rows carry `name` and `team_id` (`Development` → `development`) and include a parent team (`Engineering`) that Layer 1 does not know; `org/repositories.jsonl` rows carry `repo_id` such as `github:acme/auth-service`; `configs/org.yaml` `repos[].project` links a project to its repository.
3. **`presets[].assumptions`.** The export lists them; `presets.yaml` has no such field. Derive them from each cost run's own artifacts.
4. **Data ending on a month end.** Then the origin equals `data_through` and both forecast periods are future months, so §3.1's "the current period contains `data_through`" has no period to point at. Define what the export writes (`periods[].from` equal to `start` for both) and test it. If it changes what T6 must do, tell the user.
5. **Tokens per model.** USD paths divided by the per-date effective rate from the cost run's `daily_expected.jsonl` (§3.8), so presets need no second kernel.
6. **Reading runs.** Only through `load_run` / `load_cost_run`, one scope in memory at a time.
7. **CLI.** `cli_dashboard_forecast.py` and `econ-forecast/utils/dashboard_forecast.sh`, as in the plan's T4. Like the other forecast CLIs it configures structured logging to stderr and prints one JSON summary on stdout.

## Known and left as is

- With `force`, an input only the forecaster or the cost module rejects is found after the earlier run has been cleared.
- `TODO(#85)` in `runner.py`: the cost run reads the live price catalog while an org dataset pins its own under `<org-demo>/pricing/`.
- The forecast band's width depends on the date the series ends (plan F7).
- #81: the dashboard prices 5-minute cache writes at the 1-hour rate, 0.33% at the seam.
