# Local forecast and cost plotting utility

For a complete first run through generation, forecasting, and plotting, see the [operator guide](../../../docs/operations/README-forecast.md). To run forecast, cost and plots over a whole workspace in one command, see the [pipeline README](../pipeline/README.md).

**Status: implemented.** Two CLIs read completed runs through the shared verified readers and draw charts into the run's own `charts/`:

| CLI | Draws | From |
|---|---|---|
| `utils/plot_forecast.sh` (`forecast.utils.cli_plot_forecast`) | token forecasts: mean, band, samples, real-data overlay | a forecast run (`--artifact`), `forecast:` section of `plot.yaml` or single-plot flags |
| `utils/plot_cost.sh` (`forecast.utils.cli_plot_cost`) | USD: total and per-model fans, per-model breakdown | a cost run (`--cost-run`), `cost:` section of `plot.yaml` |

Neither refits ETS, regenerates paths, or reprices; neither touches anything in the run outside `charts/` (except that `plot_cost` re-renders `report.md` so it links the charts). Behavior is specified in the [plotting-utility enhancements design](../../../docs/design/plot-forecast-enhancements-design.md) §5–§11 and §14; see the [forecasting README](../forecasting/README.md) and [cost README](../cost/README.md) for artifact and sample meanings.

## Why Plotly / matplotlib for files

Plotly supports hover values, zoom/pan, reset, and trace visibility in a local browser. Each HTML chart loads Plotly from **one shared `plotly.min.js`** by relative path (`include_plotlyjs=<relative path>`), so a chart is a few KB to a few hundred KB instead of ~4.3 MB and still needs no CDN, server, API key, or network access. The JS is written from the installed plotly's `get_plotlyjs()` when missing, so its version always matches.

PNG export uses the matplotlib Agg-backend helper (`forecasting.presentation._render_png`) -- no `kaleido`. Plotly is imported only by the plotting utility, never by the forecast or cost engines. One computed series set per chart feeds both renderers, so HTML and PNG show the same thing. Browser automation is a test dependency only.

## `plot_forecast.sh`

```text
plot_forecast --artifact <forecast-run> --config <plot.yaml> [--dataset <dir>] [--force]
plot_forecast --artifact <forecast-run> --scope-type <type> [--scope-name <name>]
              [--model <id> ...] [--samples N] [--band LOW HIGH]
              [--dataset <dir>] [--format html|png|both] [--force]
```

```bash
./econ-forecast/utils/plot_forecast.sh \
  --artifact econ-forecast/artifacts/september-forecast \
  --scope-type team --scope-name team1 --model claude-sonnet-5 --samples 10 --band 5 95
# -> econ-forecast/artifacts/september-forecast/charts/team-team1__models__tokens.{html,png}
```

| Option | Required? | Meaning / default |
|---|---|---|
| `--artifact <run-dir>` | required | Saved forecast run; charts go to `<run>/charts/`. The Organization is derived from it (error if >1). |
| `--config <yaml>` | mode switch | `plot.yaml`; draws its `forecast:` section. Mutually exclusive with `--scope-type --scope-name --model --samples --band --format`. |
| `--scope-type` | single-plot | `organization`, `team`, or `project`. |
| `--scope-name <name>` | conditional | Must be omitted for `organization`; `team`/`project`: omit = one file per scope of that type. |
| `--model <id>` | optional, repeatable | Exact model ID. Omit = all models in the scope, stacked as panels. |
| `--samples N` | optional | Sample paths per model to display. Default `0`. |
| `--band LOW HIGH` | optional | Band percentiles, `0 < LOW < HIGH < 100`. Default `10 90`. |
| `--dataset <dir>` | optional, both modes | Actuals-source override. Default: the run's recorded dataset link. |
| `--format` | optional | `html`, `png`, or `both` (default). |
| `--force` | optional, both modes | Replace an existing `<run>/charts/`; without it an existing `charts/` is refused. |
| `--help` | -- | Usage without reading any dataset. |

Single-plot mode is the one-entry case of a `forecast:` section, with no `plotly_js`, so the JS is written into `charts/`. Both modes produce the same file for the same entry. The shell script works from any current directory; relative paths are relative to the caller's directory.

## `plot_cost.sh`

```text
plot_cost --cost-run <cost-run> --config <plot.yaml> [--force]
```

| Option | Required? | Meaning |
|---|---|---|
| `--cost-run <run-dir>` | required | Saved cost run (baseline or scenario); charts go to `<run>/charts/`, then `report.md` is re-rendered. |
| `--config <yaml>` | required | `plot.yaml`; draws its `cost:` section. |
| `--force` | optional | Replace an existing `<run>/charts/`. |
| `--help` | -- | Usage without reading any run. |

| View | Chart | Source |
|---|---|---|
| `total` | expected daily cost + band (+ samples) for the scope | the scope's summed USD paths |
| `per-model` | one chart per model with a cost path in the scope (qualified/inactive series) | that series' USD paths |
| `breakdown` | expected daily cost per model, stacked | `daily_expected.jsonl`; ignores `band`/`samples` |

After drawing (exit `0` or `2`) `report.md` is re-rendered: its Charts section links exactly the files `charts/index.json` lists. A cost run that was never plotted has no Charts section. `report.md` and `charts/` are not in the cost manifest, so `load_cost_run` still loads the run afterwards.

## `plot.yaml`

One file for both CLIs; one section per kind. It carries no run paths -- the CLI supplies the run.

```yaml
schema_version: 1
plotly_js: ../assets/plotly.min.js   # optional; relative to this YAML; omit = charts/plotly.min.js
defaults:                            # every section; any entry may override
  format: both                       # html | png | both
  band: {low: 10, high: 90}
  samples: 0
forecast:                            # tokens; one file per scope, selected models stacked
  - scope_type: organization
    samples: 8
  - scope_type: team                 # scope_name omitted = one file per team
  - scope_type: project
    scope_name: auth_service_rewrite
    models: [claude-opus-5]
cost:                                # USD
  - scope_type: organization
    views: [total, breakdown, per-model]
  - scope_type: team
    scope_name: team3                # views omitted = [total, breakdown]
```

| Field | Section | Rule |
|---|---|---|
| `scope_type`, `scope_name` | both | `scope_name` forbidden for `organization`; omitted for `team`/`project` = every scope of that type in the run |
| `models` | forecast | exact model IDs; omit = all models in scope |
| `views` | cost | subset of `total`, `breakdown`, `per-model`; default `[total, breakdown]` |
| `format`, `band`, `samples` | both | override `defaults`; `breakdown` ignores `band`/`samples` |

- At least one of `forecast`/`cost` is required. A CLI whose section is absent leaves `charts/` untouched and exits `0` -- so one `plot.yaml` serves a workspace's forecast run and all its cost runs.
- Validation: `schema_version == 1`; `models` only under `forecast`, `views` only under `cost`; band `0 < low < high < 100`; `samples >= 0`; unknown keys (including the old `artifact`, `output_dir`, `forecast-data`, `config-default`, `plots`) are rejected.

## Output contract

```text
<run>/charts/
├── <scope>__<view>__<measure>.html
├── <scope>__<view>__<measure>.png
├── plotly.min.js            # only when plot.yaml has no plotly_js
├── index.json
└── result.json
```

**Naming** -- `<scope>__<view>__<measure>`, each part slugged (lowercase, non-alphanumeric -> `-`, repeats collapsed):

| Part | Values |
|---|---|
| `scope` | `organization-<org_id>`, `team-<name>`, `project-<name>` |
| `view` | forecast: `models`; cost: `total`, `breakdown`, `<model>` (per-model) |
| `measure` | `tokens` (forecast), `cost` |

Examples: forecast `{scope_type: team, scope_name: team1}` -> `team-team1__models__tokens.{html,png}`; cost `{scope_type: organization, views: [total, per-model]}` in org `talium.ai` -> `organization-talium-ai__total__cost.*`, `organization-talium-ai__claude-opus-5__cost.*`, ...; cost `{scope_type: project}` over 5 projects -> 10 stems. Two entries producing the same stem (after scope expansion) is a **config error**, rejected before anything is written.

- **`index.json`** -- what can I open: one item per chart with `scope_type`, `scope_name` (as recorded in the run), `view`, `measure`, `model` (the model ID for a per-model cost chart, else `null`), `files`, `status`.
- **`result.json`** -- the full record: overall `status` (`complete`/`partial`/`failed`), `counts`, `versions`, and one `plots` item per stem with the selection, `band`, `samples`, `format`, `outputs`, per-chart `status` and `error` (`null`, or `{"type": "input"|"execution", "message": ...}`). Forecast charts add `forecast_data` (actuals source) and per-chart `models`, `plotted_models`, `unavailable_models`, `actuals_overlaid`.
- **Resilience** -- a per-chart failure (unknown scope/model, no representable paths) is recorded in that chart's `error` and does not abort the batch.
- **Exit codes** -- `0` all succeeded; `2` partial or failed (`index.json`/`result.json` written; `plot_cost` re-renders `report.md`); `1` could not start (unreadable config/run, existing `charts/` without `--force`, stem collision) -- nothing written, reported on stderr only.
- **`--force`** replaces exactly `<run>/charts/` (refused if it is a symlink or not a directory); nothing else in the run is touched.

## Real-data (actuals) overlay

The observed series is not stored in the forecast run; the run records where its input came
from. By default the plotting utility reads `resolved_config.yaml` -> `input.dataset_dir`,
resolves it against the run (the link is recorded relative to the run), loads that dataset, and reuses `forecasting.history.build_histories` -- the same function the
forecaster used -- to aggregate it to the same scope x model daily totals the forecaster
consumed, over the same lookback window. That means the overlay is exactly what the forecast
saw, not a re-derivation.

- **Override** with `--dataset <dir>` (allowed with `--config` too) to point at a different
  copy of the input.
- **Unavailable:** if the dataset is absent, moved, or unreadable, the plot falls back to
  forecast-only with a one-line note in the structured log and the chart subtitle. This never
  fails the plot.
- **Integrity:** when the dataset is found at its recorded (auto) location, its hashes are
  compared against the run's manifest `input_sources`. A mismatch is surfaced as a note ("actuals
  may differ from the fitted input") -- still plotted, just flagged. This check only applies in
  auto mode: an explicit `--dataset` override is a deliberate choice of input, not drift.
- **Layout:** the real observed daily total is drawn as a thin, distinct raw line up to the
  forecast origin, with a vertical rule marking the seam; the forecast mean and band continue
  from there. Each scope x model panel gets its own actuals line for that same scope x model.
  `result.json` records `forecast_data.source` (`auto`/`override`/`unavailable`) once per run
  and each plot's `actuals_overlaid: bool`.

## Band & central line

The central line is always the **mean** ("expected"). The band is a percentile pair applied
pointwise per date, same as before but now configurable: `--band LOW HIGH` (CLI) or `band:
{low, high}` (`plot.yaml`), both `0 < low < high < 100`, default `10 90`. The subtitle and legend
state the actual percentiles used (e.g. "Bands are pointwise p5-p95"). `--samples`/`samples:`
is unchanged in meaning: N individual sample paths per model, capped at the available count,
display-only -- the mean and band always use every saved sample.

## What the graph shows

Each model panel displays:

- The real observed daily **total tokens**, up to the forecast origin, when available (see
  above).
- The mean daily total tokens across all saved samples, from the forecast origin forward.
- A pointwise band (default p10-p90), the configured central-percentage range of simulated
  values for each day, using linear percentile interpolation.
- Individual sample paths when requested, with sample IDs visible in hover/legend labels.
- Exact dates and scope/model labels, full and displayed sample counts, band percentiles,
  actuals status, and relevant status/assumptions.

A sample is a complete possible daily future; paths are not regenerated for display. A
pointwise band is not a guarantee that a whole month stays inside it, nor a monetary
confidence estimate.

Partial runs may show useful model panels while visibly listing unavailable components. They must not silently present a complete scope total. Inactive components may be shown as explicitly assumed zero. The utility does not impose new forecasting eligibility rules.

Open the exported HTML file manually to hover for date/model/token values, zoom or pan, reset axes, and toggle traces through the legend. Open the PNG for a static, shareable chart with the same content. The command does not automatically launch a browser on a Mac or AWS instance. HTML must work with networking disabled.

## Feedback and logging

Each CLI prints `result.json`'s content as one JSON line on stdout (for an absent section: a short `complete` summary). Structured JSON stderr logs selection, run/dataset reading, actuals resolution, per-chart progress, the shared JS write, and completion/failure (`plot.*` events). Logs avoid raw user records. The [pipeline](../pipeline/README.md) records the stdout line as each command's `summary`.

## Tests

`tests/utils/` covers: `plot.yaml` sections, defaults/override, scope expansion and stem collisions (`test_plot_config.py`); the §7 naming examples; `charts/` inside the run, refused without `--force` and replaced with it, run readers still loading, HTML referencing the shared JS by relative path and staying well under 1 MB (`test_plot_force.py`, `test_plot_forecast.py`); CLI parsing, `--config` exclusivity and conditional `--scope-name` (`test_plot_forecast_cli.py`); the actuals overlay (auto, override, unavailable, hash-mismatch note; `test_actuals.py`); cost views, band/samples honored, `breakdown` ignoring them, and `report.md` linking exactly the `index.json` files (`test_plot_cost.py`). `test_plot_forecast_browser.py` opens the HTML offline in a provisioned Playwright Chromium; it needs the browser installed. Reports: `docs/test/reports/` at the repository root.
