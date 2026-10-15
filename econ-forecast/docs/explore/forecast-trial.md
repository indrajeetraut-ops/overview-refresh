# Forecast trial: daily values and absent Project paths

Read-only investigation on 2026-09-18. The trial workspace is `/Users/sumandra/projects/talium/forecasting/forecast-trial`; it is separate from the code repository. No trial config or artifact was changed.

## Observed cause

The active `scopes` in `forecast-trial/config/forecasting/forecast.yaml` are Organization `talium.ai` and Team name `core`. The example Project `cloud-cost` is commented out. The saved run `artifacts/core-team-forecast/request.json` confirms exactly two resolved scopes: Organization `talium.ai` and Team ID `32`. `paths/index.json` has five model series for each, none for Project. Forecasting only generates paths for requested scopes; it does not automatically generate Project paths when Project usage exists in the input.

The source dataset `artifacts/series-120/usage.jsonl` does contain Project attribution. The saved dataset configuration and verified loader expose these Project names/IDs:

| Project name | Saved ID | Associated Team in this synthetic configuration |
|---|---:|---|
| `cloud-cost` | `12` | `infra` |
| `agent-core` | `14` | `infra` |
| `auto-review` | `21` | `dev-ops` |
| `release_activity` | `22` | `dev-ops` |
| `agent_core` | `31` | `core` |
| `itasca` | `32` | `core` |
| `guardian` | `33` | `core` |
| `AI_cost` | `41` | `aiops` |

The operator-facing forecast config selects Project **names**, not IDs. To forecast the three Projects associated with `core`, add these active entries under `scopes`:

```yaml
  - type: project
    name: agent_core
  - type: project
    name: itasca
  - type: project
    name: guardian
```

Save the config, run `forecast.sh --validate-only`, then generate to a **new** output directory; the existing `core-team-forecast` is immutable. The existing run used `--origin 2026-06-28 --unit month --offset 2 --periods 1`, requesting August 2026. A temporary config with those three Project entries and the same request passed `forecast.sh --validate-only` on 2026-09-18: exit 0, status `valid`, five requested scopes, zero rejected scopes. This only checks inputs and history/workload; it does not fit ETS or guarantee a completed generated run.

## Why `daily_expected.jsonl` contains fractions

The filename is `daily_expected.jsonl` (one JSON object per line). Historical `usage.jsonl` token counts are nonnegative integers. In [`runner.py`](../../src/forecast/forecasting/runner.py), each saved path array has one simulated total-token value per date/sample; `np.mean(arr, axis=1)` produces the daily expected total. The runner multiplies that mean by recent joint agent/category shares from [`allocation.py`](../../src/forecast/forecasting/allocation.py). Means and proportional allocations can be fractional even though any realized token count is an integer. The trial's first row has total `2998319.8400677713`, an expected value, not a measured usage count. Rounding each component in the saved artifact would lose conservation and precision for later summaries/pricing.

## Source and evidence

- Trial config: `forecast-trial/config/forecasting/forecast.yaml`; saved request and index: `forecast-trial/artifacts/core-team-forecast/request.json`, `paths/index.json`; saved Project catalog: `forecast-trial/artifacts/series-120/config.yaml`.
- Selection logic: [`forecasting/config.py`](../../src/forecast/forecasting/config.py) and [`forecasting/runner.py`](../../src/forecast/forecasting/runner.py) resolve active Team/Project names against saved scope metadata. [`forecasting/dataset.py`](../../src/forecast/forecasting/dataset.py) builds Project scopes and aggregates each usage row into its applicable Organization, Team, and Project views.
- `git status --short` in the code repository was clean before this note. The investigation did not rerun full forecasting or alter trial artifacts.
