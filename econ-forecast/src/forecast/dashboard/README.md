# Dashboard forecast: presets, runs and the export

Plan: [forecast in the dashboard](../../../../docs/plan/2026-10-05-forecast-dashboard-integration.md)
(cited as `§N`; the contract is §3.2, §3.4 and §3.8). This package turns a Layer 1 series
dataset into the precomputed forecast the dashboard shows: one forecast, one cost run per
scenario preset, and one JSON file, `forecast_export.json`, that the storage loader reads. It
fits and prices nothing itself; it calls `forecast.forecasting` and `forecast.cost`, and reads
their saved runs only through `load_run` and `load_cost_run`.

```text
org-demo/series  ->  run_dashboard_forecast  ->  forecast run + one cost run per preset
org-demo/org, configs  ->  map_scopes  ->  dashboard ids          |
                                                                  v
                                       build_export -> validate_export -> forecast_export.json
```

## Command line

```bash
econ-forecast/utils/dashboard_forecast.sh --org-demo <dir> [--presets <yaml>] [--output <dir>] [--force]
```

| Option | Meaning |
|---|---|
| `--org-demo` | org-demo directory with `series/`, `org/` and `configs/` (required) |
| `--presets` | presets file; default `econ-forecast/config/dashboard/presets.yaml` |
| `--output` | where the run and the export go; default `<org-demo>/forecast` |
| `--force` | replace an earlier run, and its export, in a non-empty output directory |

Order of work: list the run's scopes and map them to dashboard ids (a scope that cannot be
mapped stops here, before the output directory is touched); run the forecast and the cost
runs (about 40 s on the example org); build, validate and write the export.

Output on stdout is exactly one JSON line, logs are structured JSON on stderr.

| Exit | Meaning |
|---|---|
| 0 | `{"status": "complete", "exit_code": 0, "export_path", "bytes", "origin", "data_through", "org_id", "scope_count", "preset_count", "generated_at"}` |
| 1 | `ExecutionError`: `{"status": "failed", "exit_code": 1, "error", "stage"}`; `stage` is `forecast`, `cost` or `export`, or null (e.g. the output directory cannot be written) |
| 2 | `InputError`, command-line errors included: `{"status": "invalid", "exit_code": 2, "error", "stage"}`; `stage` is `scopes`, `forecast`, `cost` or null |

Log events: `dashboard.run.started`, `dashboard.forecast.completed`, `dashboard.cost.completed`,
`dashboard.run.completed`, `dashboard.export.started`, `dashboard.export.scope_exported`
(scope, preset count), `dashboard.export.completed` (path, bytes), `dashboard.export_completed` (the CLI's
success summary), and one error event (`dashboard.input_error` or
`dashboard.execution_error`) on failure.

The command line takes no price catalog or forecast template option. The cost runs read the
live price catalog (`TODO(#85)` in `runner.py`); the Python entry point accepts a pinned
catalog and a template.

## Refreshing the demo

The forecast is as of the dataset's last day (`data_through`); the run it fits ends at the previous
month end. A demo therefore goes stale a day after it is generated, and shows an old "current
period" once it is a month old. `utils/synth_demo_refresh.sh` rebuilds it in one command: it generates the org
dataset, runs this command on it, and with `--dsn` loads it into a `*_synth` database or with
`--dump` builds the AWS seed dump (see the
[org README](../synthetic/org/README.md#quick-start) for the options and what each stage needs).

The `*_synth` database must already exist.

```bash
utils/synth_demo_refresh.sh --force --end-date today --dsn postgresql+psycopg://postgres@localhost:5433/econ_synth
```

## Layout of a run

```text
<output>/forecast.yaml          the forecast config the run used
<output>/scenarios/<id>.yaml    one scenario file per non-baseline preset, effective the day after the data ends
<output>/forecast/              the forecast run (origin = last day of the month before the data ends, or the last
                                day itself when the data ends on a month end; two calendar months, §2 D1)
<output>/cost/<id>/             one cost run per preset (`baseline` has no scenario)
<output>/forecast_export.json   the export
```

`--force` removes exactly these five entries and nothing else in the directory.

## Scope mapping (§3.2)

| Forecast scope | Dashboard scope | `scope_id` | `name` |
|---|---|---|---|
| organization | `org` | `directory_org_id` of `org/manifest.json` | the series org id |
| team | `team` | `team_slug` of the Layer 1 team name; must be a `team_id` in `org/directory_teams.jsonl` | the Layer 1 team name |
| project | `repo` | `repo_id` of the row of `org/repositories.jsonl` labelled `<org.github_owner>/<repos[].name>` | that label |

The id of a team is defined by `forecast.synthetic.org.ids.team_slug`, not by matching display
names (the example directory spells Layer 1's `QA` as `Qa`). That import is the one allowed
use of `forecast.synthetic` in this package, in `scopes.py`; it is loaded only when the
mapping runs. Directory teams that Layer 1 does not know (the parent `Engineering`) are not
exported. The org dataset must belong to the series dataset's org (`org_id` in both
manifests), the org row of `budgets.jsonl` (when there is one) and every directory team row
must carry the manifest's `directory_org_id`, and no two scopes may share an id. Any missing
or malformed file, unmapped scope or ambiguity is an `InputError` with stage `scopes`.

## The export: `forecast_export.json`, schema `dashboard-forecast/1.0`

Canonical JSON: sorted keys, no spaces, UTF-8, one trailing newline. **No floats.** Money is
an integer count of micro-dollars (`int(rint(usd * 1e6))`, computed in float64 and rounded
once), tokens are integers, percentiles are `numpy.quantile` with its default linear method.
Two exports of the same run differ only in `generated_at`.

```json
{
  "schema": "dashboard-forecast/1.0",
  "generated_at": "2026-10-08T09:30:15Z",
  "timezone": "Asia/Kolkata",
  "origin": "2026-09-30", "data_through": "2026-10-08",
  "band": {"low": 10, "high": 90},
  "quantile_grid": [0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60, 65, 70, 75, 80, 85, 90, 95, 100],
  "dates": ["2026-10-01", "...", "2026-11-30"],
  "periods": [{"start": "2026-10-01", "end": "2026-10-31", "from": "2026-10-09"},
              {"start": "2026-11-01", "end": "2026-11-30", "from": "2026-11-01"}],
  "presets": [{"id": "baseline", "label": "Current trajectory", "group": null, "description": "...", "assumptions": ["fixed_mix_estimate"]}],
  "scopes": [{
    "scope_type": "team", "scope_id": "development", "name": "Development",
    "series": {"baseline": {
      "status": "complete",
      "daily": {"expected": [0], "low": [0], "high": [0]},
      "periods": [{
        "cumulative": {"expected": [0], "quantiles": [[0]]},
        "total": {"expected": 0, "quantiles": [0]},
        "tokens_by_model": [{"model": "claude-opus-5", "expected": 0, "p90": 0}]
      }]
    }}
  }]
}
```

| Field | Meaning |
|---|---|
| `generated_at` | UTC, whole seconds; the only field that differs between reruns |
| `timezone` | the series manifest's timezone; the API buckets actual spend by day in it |
| `origin`, `data_through` | forecast cutoff (history after it is not used for fitting) and the last day of data |
| `band` | the two percentiles (both entries of `quantile_grid`) behind every `low` and `high` |
| `quantile_grid` | percentiles stored for each total and each cumulative date (21 entries) |
| `dates` | every date of the forecast run, both months, consecutive; the arrays under `daily` align with it |
| `periods[i]` | the two calendar months of the forecast run; `from` is the first date counted as forecast (below) |
| `presets[]` | in `presets.yaml` order, `baseline` first; `group` is null for baseline; `assumptions` is the sorted union, over the preset's cost run's scopes, of the codes the cost module lists (`paths/index.json`), unfiltered: the reader decides which to show, and a code that applies to only some scopes is listed for the preset |
| `scopes[]` | in run order: organization, teams, projects |
| `series.<preset id>` | one entry for every preset in `presets`, for every scope |
| `status` | the cost run's scope status: `complete`, `conditional`, `inactive` carry numbers; `incomplete` is the entry `{"status": "incomplete"}` and nothing else |
| `daily` | per date in `dates` (elapsed days included): the mean and the `band` percentiles of the scope's daily USD |
| `periods[i].cumulative` | per date from `from` through `end`: the mean, and the 21 grid percentiles (`quantiles[date][grid]`), of the per-sample running sum from `from` |
| `periods[i].total` | the last cumulative entry (`expected`, `quantiles`): the landing figure and the input of the budget probability |
| `periods[i].tokens_by_model` | per model of the scope, sorted by model id: `expected` and `p90` of the per-sample token total over `from`..`end` |

A scope's USD path is the per-sample sum of its series' cost-run paths; percentiles are taken
after summing, never added. Every quantile row is non-decreasing across the grid, and every
quantile (and the mean) is non-decreasing across dates, because the cumulative sum of
non-negative daily costs never falls; the builder checks it on the rounded integers.

**Tokens.** Token paths are not stored by the cost run. A series' token path is its USD path
divided by its per-date effective rate, `sum(usd) / sum(tokens)` over the agent rows and the
five token types of the cost run's `daily_expected.jsonl`, so scenario presets need no second
kernel. This holds to float precision (about 1e-15 relative) and the figures are rounded to
whole tokens last. A date with no tokens must have no USD (the tokens are 0 there); USD without
tokens, or tokens at a zero rate, is an `ExecutionError`, never a silent zero. For the current
period `tokens_by_model` covers the remaining days only (`from`..`end`): actual tokens to date
are not in the export.

### `from` and the month-end case

`periods[i].from` is `data_through + 1` when that date lies inside the period, else the
period's `start`. Cumulative and total cover `from`..`end`: the landing figure is the spend to
date plus `total`, which the API adds.

When `data_through` is the last day of a month the origin is `data_through` itself and both
periods are future months: `from == start` for both, nothing is special-cased, and no period
contains `data_through`. A period is the current one exactly when `from > start`; with
month-end data the API has no current period (no `actual_to_date`, no runway) and treats both as future.

### Validation

`validate_export(doc)` checks a whole document independently of the builder (schema id, band in
the grid, array lengths against `dates` and `periods`, non-decreasing grids across quantiles
and dates, integer types and no floats, `total` equal to the last cumulative entry, preset ids
unique and equal to every scope's series keys, statuses, sorted models). The builder calls it
before returning, and `ExecutionError` (stage `export`) stops it from writing a bad file.

## Python API

| Name | Role |
|---|---|
| `run_dashboard_forecast(series_dir, presets_path, output_dir, *, forecast_template=None, force=False, catalog=None)` | the batch run; see `runner.py` |
| `series_scopes(series_dir)` | the scopes a run over that dataset will forecast |
| `map_scopes(org_demo, run_scopes, *, org_id)` | dashboard ids for those scopes |
| `build_export(result, scopes, *, series_dir, now=None)` | the document; `now` is a timezone-aware datetime for `generated_at` |
| `validate_export(doc)`, `write_export(doc, output_dir)`, `export_forecast(result, scopes, *, series_dir, now=None)` | validate, write atomically (temporary file, `os.replace`), or all three |

## Errors

`InputError` (CLI exit 2): bad presets, series dataset, template, output location or org
dataset, a scope that cannot be mapped (stage `scopes`), an input the forecast or cost stage
rejects. `ExecutionError` (exit 1): a stage did not complete, a run that cannot be read or
whose parts disagree, a number the export cannot represent, a document that fails validation,
a file that cannot be written (stage `export`). A run stops at the first failure and leaves
what it wrote; rerunning into the same directory needs `--force`.

## Limits

- Presets apply to the whole organization (D5); there is no per-team preset.
- The forecast ignores the current month's actuals (D1), so it can be up to 30 days stale.
- Everything is a cost-run figure at the price catalog the run read; the dashboard prices
  5-minute cache writes at the 1-hour rate (#81, 0.33% at the seam).
- The cost runs read the live price catalog, not the org dataset's pinned one (`TODO(#85)`).
- About 1 MB for the example org (10 scopes x 7 presets); every scope carries 21 percentiles
  for every date of both periods.
