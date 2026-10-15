# forecast.pipeline — workspace pipeline

Runs the forecast, cost and plot CLIs over one **workspace** with one command. Design:
[plot-forecast-enhancements-design §14](../../../docs/design/plot-forecast-enhancements-design.md).

```bash
utils/pipeline.sh --config <workspace>/pipeline.yaml
```

The pipeline is a thin orchestrator: it knows the workspace layout, calls the module wrappers
(`utils/forecast.sh`, `utils/cost.sh`, `utils/plot_forecast.sh`, `utils/plot_cost.sh`) as
subprocesses, and holds no module settings. `--config` is its only option.

## Terms

| Term | Meaning |
|---|---|
| dataset | Daily usage data, synthetic or real, independent of any forecast: `dataset/<name>/`. Synthetic ones are named `synth-<name>` (e.g. `synth-curve-guided`). |
| workspace | One forecast (one dataset × one forecast window) and everything derived from it: cost runs (baseline + scenarios), their charts, later budget. Named by the operator; next month is a new workspace. |
| link | A path to another artifact, recorded **relative to the recording run's directory**. The target's `manifest.json` hash is the integrity check. |

## Layout

```text
dataset/
└── synth-curve-guided/                 # generated separately (utils/synt_gen.sh)
assets/
└── plotly.min.js                       # one shared copy; plot.yaml plotly_js points here
<workspace>/
├── pipeline.yaml
├── plot.yaml                           # fixed name; the plot stage uses it
├── pipeline_result.json                # written by every pipeline run
├── forecast/
│   ├── forecast.yaml                   # input.dataset_dir: ../../dataset/synth-curve-guided
│   └── run/                            # forecast run (+ charts/)
└── cost/
    ├── scenarios/<name>.yaml
    ├── baseline/                       # cost run, no scenario (+ charts/)
    └── <name>/                         # one cost run per scenario file (+ charts/)
```

`baseline` and `scenarios` are reserved scenario file names (`cost/scenarios/baseline.yaml` would
overwrite `cost/baseline`); the pipeline refuses them as a configuration error.

### Links

| Run file | Field | Recorded as |
|---|---|---|
| forecast `resolved_config.yaml` | `input.dataset_dir` | relative to the forecast run |
| forecast `request.json` | `dataset.path` | relative to the forecast run |
| cost `request.json` | `forecast_run.path` | relative to the cost run |

A workspace moved together with `dataset/` (same relative position) keeps working.

### Charts

Each run's charts live in its own `charts/` (written by the plot CLIs, never by the forecast or
cost run), named `<scope>__<view>__<measure>.{html,png}` — e.g. `team-team3__breakdown__cost.html`,
`organization-talium-ai__models__tokens.png`. `charts/index.json` lists what can be opened; a cost
run's `report.md` links exactly those files. HTML loads the shared `assets/plotly.min.js` by a
relative path, so charts open offline. Details: [utils README](../utils/README.md).

## `pipeline.yaml`

```yaml
stages: [forecast, cost, plot]     # any subset; always run in this order
forecast:                          # forecast.sh flags, passed through
  unit: month
  offset: 1
  periods: 1
```

- `stages` — required, non-empty subset of `forecast`, `cost`, `plot`.
- A stage block (`forecast:`, `cost:`, `plot:`) holds CLI flags that neither the module's own
  config nor the layout supplies; each `key: value` (scalars only) is appended to every command of
  that stage as `--key value`. Flags the layout sets (`--config`, `--output`, `--force`,
  `--forecast-run`, `--scenario`, `--cost-run`, `--artifact`) are refused.

| Stage | Commands (cwd = workspace) |
|---|---|
| forecast | `forecast.sh --config forecast/forecast.yaml --output forecast/run --force` + `forecast:` flags |
| cost | `cost.sh --forecast-run forecast/run --output cost/baseline --force`; per `cost/scenarios/<name>.yaml`: `cost.sh --forecast-run forecast/run --scenario cost/scenarios/<name>.yaml --output cost/<name> --force` |
| plot | `plot_forecast.sh --artifact forecast/run --config plot.yaml --force`; per cost run directory in `cost/` (baseline first): `plot_cost.sh --cost-run cost/<run> --config plot.yaml --force` |

- A listed stage re-runs and replaces its outputs.
- The first command that exits non-zero (`2` counts) stops the run; later commands and stages are
  recorded as `skipped`. The pipeline exits with that command's code, else `0`.
- No synth stage and no staleness check: re-running `forecast` without `cost` leaves the cost runs
  priced from the old forecast (their `forecast_run.manifest_sha256` shows it).

## `pipeline_result.json`

Written to the workspace on every run, success or failure, and printed as one JSON line on stdout.
Each command's stderr streams through to the terminal as it runs.

```json
{
  "schema_version": 1,
  "status": "complete | failed",
  "started_at": "2026-09-27T16:00:00+00:00",
  "elapsed_seconds": 41.2,
  "stages": [
    {
      "stage": "cost",
      "status": "complete | failed | skipped",
      "commands": [
        {
          "argv": ["utils/cost.sh", "--forecast-run", "forecast/run", "--output", "cost/baseline", "--force"],
          "status": "complete | failed | skipped",
          "exit_code": 0,
          "elapsed_seconds": 6.3,
          "summary": {"…": "the CLI's stdout JSON line, parsed; null if none"},
          "stderr_tail": null
        }
      ]
    }
  ]
}
```

| Field | Use |
|---|---|
| `status`, stage/command `status` | what ran, what failed, what was skipped |
| `argv` | rerun one step by hand (from the workspace, with `utils/` = `econ-forecast/utils/`) |
| `exit_code`, `summary` | the module's own error (`error`, `stage`) without scrolling logs |
| `stderr_tail` | last 50 stderr lines of a failed command (`null` otherwise) |
| `elapsed_seconds` | where time goes |

A configuration error (unreadable `pipeline.yaml`, unknown stage, bad stage block, reserved
scenario name) is recorded as `status: failed` with a top-level
`"error": {"type": "input", "message": "…"}`; nothing runs and the exit code is `1`.

## Examples

`econ-forecast/examples/` holds two workspaces built with this pipeline (`curve-guided/`: forecast,
cost baseline + three scenarios; `known-event-spike/`: forecast only) — see its README for how to
reproduce them.
