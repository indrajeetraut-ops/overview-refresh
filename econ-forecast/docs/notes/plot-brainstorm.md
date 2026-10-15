# Plot — brainstorm notes (unified forecast + cost charts)

Scratchpad for the plot design revision. Upstream: [plot-forecast-enhancements-design](../design/plot-forecast-enhancements-design.md),
[cost-module-design §8.3](../design/cost-module-design.md).

## Problem (grounded 2026-09-27)

- HTML charts are produced but not committed: `examples/plots/.gitignore` (`*/plots/*.html`) and
  `examples/cost/.gitignore` (`*/charts/*.html`). Reason: `include_plotlyjs=True` embeds plotly.js
  7.0.0 (4.29 MB; 1.32 MB gzip) in every file — `utils/plot_forecast.py:433`, `cost/presentation.py:240`.
  `curve/preview.py:89` uses `"cdn"` (~10 KB files, committed).
- Cost charts have no config: `cost/presentation.py::_write_charts` always writes every chart
  (per series fan, per scope total fan + model breakdown), PNG + HTML — 54 × 2 files per example run.
- Forecast run also writes its own charts: `forecasting/presentation.py::write_charts` → `charts/*.png`
  (hash-suffixed names, one per series, PNG only), called from `forecasting/runner.py:726`.

## Decisions

| D# | Decision | Notes / who / date |
|---|---|---|
| D1 | HTML charts load plotly.js from **one shared file**, not embedded per chart. | user, 2026-09-27 |
| D2 | HTML charts **and** the shared `plotly.min.js` are committed in examples; drop the `*.html` gitignore rules. | user, 2026-09-27 |
| D3 | **Plot utility owns all cost charts** (Option A): cost CLI writes data + `report.md` only; one `plot.yaml` drives forecast and cost charts. | user, 2026-09-27; refined by D5 |
| D4 | Forecast run stops writing its own `charts/*.png` (`forecasting/presentation.py::write_charts`); charts come only from `plot.yaml`. | user, 2026-09-27 (Q1) |
| D5 | **One `plot.yaml`; every CLI can point to it** and produce the plots it can (forecast CLI, cost CLI, plot CLI). One rendering code path. | user, 2026-09-27 |
| D6 | `report.md` is generated together with plots: it links the charts where they are found, or has no chart section. | user, 2026-09-27 (Q2) |
| D7 | Formalize an **artifact layout + naming convention** (workspace: dataset → forecast → cost → budget, `plot.yaml`; each section its own config) so every plot is easy to locate. Written as a README convention now; a pipeline runner that enforces it comes later. | user, 2026-09-27 |
| D9 | **Workspace** = one forecast (one dataset × one forecast window) and everything derived from it: many cost scenarios, budget, plots. A different window on the same dataset (e.g. next month vs. month after) is a different workspace. | user, 2026-09-27 |
| D10 | Workspace names are **explicit** (chosen by the operator / future pipeline), not derived from dataset or window. Not decided here. ~~`workspaces/<dataset>/<window>/`~~ withdrawn. | user, 2026-09-27 |
| D11 | Dataset is independent of any forecast period. A workspace **references** its dataset (link), never copies it. | user, 2026-09-27 |
| D12 | Datasets live at `dataset/<name>/`. Synthetic: `synth-<curve-name>`; real data: any name. Examples: move (no regeneration) to `examples/dataset/<name>/`. | user, 2026-09-27 |
| D13 | Links (run → dataset, cost run → forecast run) are **paths relative to the run's own directory**; existing content hashes stay as the integrity check. Change in forecasting (`resolved_config.yaml`, `request.json`) and cost (`request.json`). | user, 2026-09-27 (Q9) |
| D14 | Integrity hashes live in the run's `manifest.json`; that is sufficient. | user, 2026-09-27 |
| D15 | `plot.yaml` has **one section per kind** (`forecast:`, `cost:`), not a `kind:` field per entry; new kinds (budget…) add sections. | user, 2026-09-27 (Q4) |
| D16 | plot.yaml draft 2 accepted: shared `defaults` (format, band, samples); band/samples apply to cost too; views per §draft; omitted `scope_name` = one file per scope (drops `project-all-projects`); collisions fail before writing. Keep it simple — no object model until needed. | user, 2026-09-27 (Q4) |
| D17 | Add a **pipeline runner**: `utils/pipeline.sh` wrapping `python -m forecast.pipeline.cli_pipeline`, config-driven, operator selects which stages to run. | user, 2026-09-27 |
| D18 | Pipeline draft 1 accepted (workspace.yaml = forecast window only; stages forecast→cost→plot; Python API calls; --force; no synth stage; no staleness check yet). Plan adds `src/forecast/pipeline/README.md`: workspace layout + naming convention (D7) and pipeline usage. | user, 2026-09-27 (Q10) |
| D19 | **No package move, nothing removed.** Plot code stays in `forecast.utils`; `plot_forecast.sh` / `cli_plot_forecast.py` keep their names. Cost chart rendering moves from `cost/presentation.py` into `forecast/utils/` beside the forecast renderer. ~~new `forecast.plot` package~~ withdrawn — churn without a behavior gain. | user, 2026-09-27 (Q6) |
| D20 | **Surgical.** Minimal changes to forecast/cost code; no schema-version bumps or backward compatibility (not in prod, change is cheap). Existing design doc edited in place, only the affected sections. | user, 2026-09-27 (Q5) |
| D21 | ~~D5 `--plots` flag on forecast.sh / cost.sh~~ withdrawn: no plot flags on forecast/cost CLIs. | user, 2026-09-27 |
| D22 | Add `utils/plot_cost.sh` → `forecast.utils.cli_plot_cost` (reads the `cost:` section of plot.yaml for one cost run). `plot_forecast.sh` reads the `forecast:` section. | user, 2026-09-27 |
| D23 | Pipeline is **config-driven, no option sprawl**: `pipeline.sh --config <workspace>/workspace.yaml` only; stages selected in the config; a listed stage re-runs and replaces its outputs. ~~`--stages`, `--force`, `--workspace` flags~~ withdrawn. | user, 2026-09-27 |
| D24 | **Pipeline is a dumb orchestrator.** Its config = `stages` + plotting only; never forecast/cost/budget settings. It shells out to the module CLIs (forecast.sh, cost.sh, plot_forecast.sh, plot_cost.sh) using the layout convention. Supersedes the D18/D23 "Python API calls" and `forecast:` window block. | user, 2026-09-27 |
| D25 | **Rule:** arguments a module CLI needs (beyond what its own config holds and the layout convention supplies) go in the pipeline config, per stage, passed through verbatim as flags. Module configs are untouched. Forecast horizon = `forecast:` flags block in pipeline config. ~~horizon in forecast.yaml~~ withdrawn (not surgical). | user, 2026-09-27 |
| D26 | Cost manifest no longer lists `report.md` / `charts/` (presentation) so `plot_cost` can re-render the report. Forecast manifest no longer lists charts (D4); its `report.md` stays listed (unchanged). | coordinator, grounded in `cost/artifacts.py`; accepted by user 2026-09-27 |
| D27 | Plot CLIs: `--output` removed; charts go to `<run>/charts/`, refused if present unless `--force` (pipeline passes it). `plot.yaml` optional `plotly_js` (relative to YAML), else a copy per `charts/`. `forecast-data` dropped from plot.yaml (`--dataset` flag stays). Chart builders stay in `cost/presentation.py` (supersedes D19's "moves"). | coordinator; accepted by user 2026-09-27 (Q3, Q7) |
| D28 | Examples rebuilt **with the pipeline** in a fresh `examples-test/` (datasets regenerated there with synt_gen.sh; `synthetic/` and `curve/` copied unchanged); verified; then old `examples/` removed from git (`git rm`) and `examples-test/` renamed to `examples/`. Careful delete: only after verification, only inside the project. D26/D27 accepted by user. | user, 2026-09-27 |
| D29 | Review applied (Major 1–8, Minors except §2/§13 restructuring). `pipeline.yaml` has **no `plot:` key** — `plot.yaml` at the workspace root by convention (naming revisited later). Cost `request.json` drops `dataset`. | user, 2026-09-27 |
| D30 | **Pipeline must record the result of the operation, including errors**: `<workspace>/pipeline_result.json` on every run (per stage/command: argv, status, exit code, parsed CLI JSON summary, stderr tail on failure, timing; skipped items listed), also printed on stdout. | user, 2026-09-27 |
| D8 | Datasets (synthetic or real) are separate from the workspace sections; every forecast refers to exactly one dataset. Synthetic generation stays separate. | user, 2026-09-27 |

## Open questions

| Q# | Question | Status |
|---|---|---|
| Q1 | Forecast run's own `charts/*.png` — move to the plot utility too (same as D3)? | → D4 |
| Q2 | `report.md` (cost, forecast) links charts under `charts/` — what does it link once charts move out? | → D6 |
| Q3 | Shared plotly.min.js location | → D27 |
| Q4 | Unified `plot.yaml` shape | → D15, D16 |
| Q5 | Doc | → D20 |
| Q7 | How each CLI uses `plot.yaml` | → D21, D22, D27 |
| Q8 | Workspace layout, naming, README | → design §7, §14 |
| Q9 | → D13 (closed). Provenance paths are absolute today (`request.json` `dataset.path`, `resolved_config.yaml` `input.dataset_dir` → `/Users/.../projects/talium/forecasting/...`): record relative to the run + keep manifest sha? | closed |
| Q10 | Pipeline config shape, stages, overwrite rules. | → D18 |
| Q6 | Module home and CLI name | → D19 |

## External-module items to decide together

| Module | Item |
|---|---|
| cost | remove chart writing from `presentation.py`; report + manifest/reader no longer know `charts/` |
| forecasting | (Q1) remove `write_charts` from the run? |
| examples | regenerate plots + cost examples with committed HTML and one `plotly.min.js` |

## Drafts

### Workspace layout (Q8, draft 1)

```text
<workspace>/                          # one pipeline workspace, e.g. examples/curve-guided/
├── plot.yaml                         # one; every CLI's --plots
├── assets/plotly.min.js              # one shared copy; every HTML chart references it
├── forecast/
│   ├── forecast.yaml                 # input.dataset_dir -> one dataset (outside the workspace)
│   └── run/                          # forecast run: data + report.md + charts/
├── cost/
│   ├── scenarios/<scenario>.yaml
│   ├── baseline/                     # cost run, no scenario
│   └── <scenario>/                   # cost run per scenario (dir name = scenario file stem)
└── budget/
    ├── budget.yaml
    └── <budget-run>/
datasets/<dataset>/                   # synthetic or real, shared by workspaces
```

### Chart naming (draft 1)

`<run>/charts/<scope-slug>__<view>__<measure>.{png,html}` + `charts/index.json`

| Part | Values |
|---|---|
| scope-slug | `organization-<org>`, `team-<name>`, `project-<name>` (slugged) |
| view | `<model-slug>`, `all-models`, `total`, `breakdown` |
| measure | `tokens` (forecast), `cost` |

### plot.yaml (Q4, draft 2)

```yaml
schema_version: 2
defaults:                     # every section, overridable per entry
  format: both                # html | png | both
  band:
    low: 10
    high: 90
  samples: 0
forecast:                     # tokens; one file per scope, selected models stacked
  - scope_type: organization
    samples: 8
  - scope_type: team          # scope_name omitted = one file per team
  - scope_type: project
    scope_name: auth_service_rewrite
    models: [claude-opus-5]
cost:                         # USD; applied to every cost run given
  - scope_type: organization
    views: [total, breakdown, per-model]
  - scope_type: team
    scope_name: team3         # views omitted = [total, breakdown]
```

### Pipeline (Q10, draft 1)

`<workspace>/workspace.yaml` marks a workspace root; everything else by the D7 layout convention.

```yaml
schema_version: 1
forecast:                 # window; flags cli_forecast takes today
  unit: month
  offset: 1
  periods: 1
```

```text
pipeline.sh --workspace <dir> [--stages forecast,cost,plot] [--force]
```

| Stage | Reads | Runs | Writes |
|---|---|---|---|
| forecast | forecast/forecast.yaml (its input.dataset_dir = the dataset link) | run_forecast | forecast/run/ |
| cost | forecast/run/, cost/scenarios/*.yaml | run_cost: baseline + one per scenario file | cost/baseline/, cost/<stem>/ |
| plot | plot.yaml, forecast/run/, cost/*/ | render_plots | <run>/charts/, re-render <run>/report.md |

### Minimal code changes (draft)

| Where | Change |
|---|---|
| forecasting/runner.py:723-727 | drop the `write_charts` call (+ `write_charts` in presentation.py); report never linked charts |
| forecasting/runner.py:716 (+ request.json dataset path) | write dataset path relative to the run dir |
| cost/presentation.py `render_report` | stop calling `_write_charts`; `_charts_section` lists charts from `charts/index.json` if present, else no section. Chart builders stay here; plot utility imports them (+ band param) |
| cost/runner.py `_request_doc` | forecast_run.path relative to the cost run dir |
| cost/request.py:235, utils/actuals.py | resolve recorded paths against the run dir (absolute still works) |
| utils/plot_config.py, run.py | plot.yaml `forecast:`/`cost:` sections; output into `<run>/charts/`; shared plotly.min.js; re-render cost report.md |
| new forecast/pipeline/ + utils/pipeline.sh + README | runner |

### workspace.yaml (draft 2) — ~~withdrawn~~ (D24)

```yaml
schema_version: 1
stages: [forecast, cost, plot]   # subset, run in this order
forecast:
  unit: month
  offset: 1
  periods: 1
```

### pipeline config (draft 3)

```yaml
stages: [forecast, cost, plot]
plot: plot.yaml
```

Horizon (CLI-only today): → D25, pass-through block.

### pipeline config (draft 4, accepted)

```yaml
stages: [forecast, cost, plot]
forecast:            # forecast.sh flags, passed through verbatim
  unit: month
  offset: 1
  periods: 1
plot: plot.yaml
```

cost needs no block: `--forecast-run`, `--scenario` (one run per `cost/scenarios/*.yaml` + baseline), `--output` all come from the layout.
