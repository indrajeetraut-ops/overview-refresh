# Forecast plotting utility — enhancements design

**Status:** design draft (for review/edit). **Date:** 2026-09-23. **Revised:** 2026-09-27 — cost
charts, one `plot.yaml`, charts inside the run, shared plotly.js, workspace + pipeline (§14);
decisions in [plot brainstorm](../notes/plot-brainstorm.md). Issue: #78.
**Companion plan:** [plot-forecast-enhancements-plan](../plan/plot-forecast-enhancements-plan.md).
**Audience:** implementer. Decisions are **[decided]** unless marked **[open]**.
**Process:** no implementation coding until the user enters implementation mode; then follow the
`talium-impl-workflow` skill (sonnet subagent, handshake, worktree, tests + persisted report,
Major/Minor review).

## 1. Purpose & scope

Improve the local Plotly experimentation utility (`forecast.utils`) so a saved forecast or cost run
reads clearly and every chart comes from one config.

- **In scope:** (1) overlay the real observed series against the forecast; (2) a configurable
  uncertainty band; (3) a cleaner CLI; (4) one `plot.yaml` driving forecast **and cost** charts;
  (5) both HTML and PNG output, HTML sharing one `plotly.min.js`; (6) charts written into the run's
  own `charts/` with a naming convention; (7) workspace layout convention + a pipeline runner (§14);
  (8) README + examples; (9) tests.
- **Non-goals:** no refitting, no path regeneration, no pricing or budget math (cost charts only read a
  saved cost run). Forecast and cost CLIs no longer draw charts (§14.5) and get no plot flags.
  Combined multi-panel-across-entries export is **deferred** (§12). Baking observed history into the
  forecast run is **out of scope** — the run already records where its input came from (§3).

## 2. Decisions (locked)

- Real data auto-loads from the run's recorded input; optional `--dataset` override; unavailable →
  forecast-only. No suppress flag (nothing to hide when data is absent).
- Central line = **mean** ("expected"); band = a **configurable percentile pair**, default
  **p10/p90** — for forecast and cost charts alike.
- CLI: `--artifact` replaces `--run`+`--org-id` (org derived from the run); `--scope-type` /
  `--scope-name` stay separate; `--sample-paths` → `--samples`; default `--format both`.
- Two CLIs, one `plot.yaml`: `plot_forecast.sh` draws its `forecast:` section, `plot_cost.sh` its
  `cost:` section. `plot.yaml` says **what** to draw; the run comes from the CLI.
- Charts are written into **the run's own `charts/`** (chart next to its data), named
  `<scope>__<view>__<measure>` (§7). `--output` is removed.
- HTML via Plotly, referencing **one shared `plotly.min.js`** (not embedded); PNG via matplotlib
  (`presentation._render_png`, no kaleido). HTML and the shared JS are committed in examples.
- Resilient batch: a failing entry is recorded and the batch continues; `result.json` is written
  regardless of success or failure.

## 3. Real-data (actuals) overlay **[decided]**

The observed series is not stored in the forecast run; the run records where its input came from:
`resolved_config.yaml` → `input.dataset_dir` (relative to the run, §14.3), with per-file hashes in
manifest `input_sources` (both written by `forecasting.runner.run_forecast`).

- **Sourcing.** Default: read `input.dataset_dir` from `resolved_config.yaml`, `load_dataset` it,
  and re-aggregate to the same scope×model daily totals the forecaster used — reuse
  `forecasting.history.build_histories` / `forecasting.dataset.load_dataset` so the real line is
  exactly what the forecast saw, not a re-derivation. Override with `--dataset <dir>`. If the dataset is absent, moved, or unreadable → **forecast-only**,
  with a one-line note in the log and the chart subtitle. Integrity: when the dataset is found at
  the recorded location, verify it against manifest `input_sources`; a hash mismatch is surfaced
  as a subtitle/log note (still plotted, marked "actuals may differ from the fitted input").
- **Layout.** Real observed daily totals as a thin, distinct line up to the forecast origin;
  forecast mean + band from the origin forward; the **origin seam** marked (vertical rule). Real
  line = **raw** observed totals (honest/spiky), not smoothed.
- **Per panel.** Each scope×model panel gets its own actuals line for that same scope×model.
- `result.json` records `forecast_data.source ∈ {auto, override, unavailable}` and per-plot
  `actuals_overlaid: bool`.

## 4. Band & central line **[decided]**

- Central line stays the **mean** (the "expected" line).
- Band is a percentile pair `[low, high]`, applied pointwise per date (as today), default
  `[10, 90]`. Validated `0 < low < high < 100` (integers). CLI `--band LOW HIGH`; config
  `band: {low, high}`. The band label and subtitle state the actual percentiles used.
- Samples (`--samples N`) unchanged in meaning: N individual sample paths per model, capped at
  the available count.

## 5. CLI surface **[decided]**

```
plot_forecast --artifact <forecast-run> --config <plot.yaml> [--dataset <dir>] [--force]
plot_forecast --artifact <forecast-run> --scope-type <type> [--scope-name <name>]
              [--model <id> ...] [--samples N] [--band LOW HIGH]
              [--dataset <dir>] [--format html|png|both] [--force]
plot_cost     --cost-run <cost-run> --config <plot.yaml> [--force]
```

`utils/plot_cost.sh` → `python -m forecast.utils.cli_plot_cost` (new). `plot_forecast.sh` keeps its
name and module.

| Option | Req? | Meaning / default |
|---|---|---|
| `--artifact <run-dir>` | required (`plot_forecast`) | Saved forecast run; charts go to `<run>/charts/`. Org derived from it (error if >1 org). |
| `--cost-run <run-dir>` | required (`plot_cost`) | Saved cost run; charts go to `<run>/charts/`, `report.md` re-rendered (§7). |
| `--config <yaml>` | required (`plot_cost`); mode switch (`plot_forecast`) | `plot.yaml` (§6). Mutually exclusive with `--scope-type --scope-name --model --samples --band --format`. |
| `--scope-type` | single-plot | `organization` \| `team` \| `project`. |
| `--scope-name <name>` | conditional | Must be omitted for `organization`; `team`/`project`: omit = one file per scope of that type. |
| `--model <id>` | optional (repeatable) | Exact model ID. Omit = all models in scope. |
| `--samples N` | optional | Sample paths per model. Default `0`. |
| `--band LOW HIGH` | optional | Band percentiles. Default `10 90`. |
| `--dataset <dir>` | optional | Actuals-source override. Default: the run's recorded dataset link (§3, §14.3). |
| `--format html\|png\|both` | optional | Default `both`. |
| `--force` | optional | Replace an existing `<run>/charts/`; without it an existing `charts/` is refused. |
| `--help` | — | Usage without reading any dataset; marks each option required/optional. |

Single-plot mode is the one-entry case of a `forecast:` section (no `plotly_js`, so the JS is written
into `charts/`, §8).

## 6. `plot.yaml` **[decided]**

One section per kind; a new kind (e.g. budget) adds a section. No run paths — the CLI supplies the run.

```yaml
schema_version: 1
plotly_js: ../assets/plotly.min.js   # optional; relative to this YAML; omit = copy into each charts/
defaults:                            # every section; any entry may override
  format: both                       # html | png | both
  band:
    low: 10
    high: 90
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
| `scope_type`, `scope_name` | both | as §5; omitted `scope_name` for `team`/`project` expands to every scope of that type in the run |
| `models` | forecast | exact model IDs; omit = all models in scope |
| `views` | cost | subset of `total`, `breakdown`, `per-model`; default `[total, breakdown]` |
| `format`, `band`, `samples` | both | override `defaults`; `breakdown` ignores `band`/`samples` (expected cost only) |

- `plotly_js` resolves relative to the YAML (like `users_file`/`roster_file` in synthetic configs).
- A section may be absent; the CLI for an absent section leaves `charts/` untouched and exits `0`.
- `forecast-data`, `artifact`, `output_dir`, `config-default`, `plots` of the old shape are removed;
  `schema_version` stays `1` (not in production, no compatibility kept).

## 7. Output contract **[decided]**

Charts live in the run they draw: `<run>/charts/`, created by the plot CLI. Neither run reader looks at
`charts/` (forecast manifest no longer lists charts, §14.5; cost manifest excludes presentation, §14.5).

```text
<run>/charts/
├── <scope>__<view>__<measure>.html
├── <scope>__<view>__<measure>.png
├── plotly.min.js            # only when plot.yaml has no plotly_js
├── index.json
└── result.json
```

### Naming

`<scope>__<view>__<measure>`, each part slugged (lowercase, non-alphanumeric → `-`, repeats collapsed).

| Part | Values |
|---|---|
| `scope` | `organization-<org_id>`, `team-<name>`, `project-<name>` |
| `view` | forecast: `models` (selected models stacked); cost: `total`, `breakdown`, `<model>` (per-model) |
| `measure` | `tokens` (forecast), `cost` |

| Entry | Files |
|---|---|
| forecast `{scope_type: team, scope_name: team1}` | `team-team1__models__tokens.{html,png}` |
| cost `{scope_type: organization, views: [total, per-model]}`, org `talium.ai` | `organization-talium-ai__total__cost.*`, `organization-talium-ai__claude-opus-5__cost.*`, … |
| cost `{scope_type: project}` (5 projects, default views) | 10 stems: `project-<name>__total__cost`, `project-<name>__breakdown__cost` |

`view` is the slug; the JSON records keep the original `scope_name` and, for a per-model cost chart,
the model ID in `model` (`null` otherwise). Two entries producing the same stem
is a **config error** (fail loud, before any file is written).

### `result.json`

One `plots` item per chart stem (an entry may expand to several), with `view` and `measure`:

```json
{
  "schema_version": 1,
  "operation": "plot",
  "status": "complete | partial | failed",
  "created_at": "…",
  "elapsed_seconds": 0.0,
  "artifact": "..",
  "forecast_data": {"source": "auto | override | unavailable", "dataset": "<path relative to charts/ | null>"},
  "counts": {"requested": 3, "succeeded": 2, "failed": 1},
  "versions": {"…": "…"},
  "plots": [
    {
      "scope_type": "team",
      "scope_name": "core",
      "view": "models",
      "measure": "tokens",
      "stem": "team-core__models__tokens",
      "model": null,
      "models": ["claude-sonnet-5"],
      "samples": 0,
      "band": [10, 90],
      "format": "both",
      "status": "complete",
      "outputs": {"html": "team-core__models__tokens.html", "png": "team-core__models__tokens.png"},
      "plotted_models": ["claude-sonnet-5"],
      "unavailable_models": [],
      "actuals_overlaid": true,
      "error": null
    }
  ]
}
```

`forecast_data` and `actuals_overlaid` appear for forecast charts only. `error`, when present:
`{"type": "input | execution", "message": "…"}`.

### `index.json`

```json
{
  "schema_version": 1,
  "operation": "plot",
  "created_at": "…",
  "status": "partial",
  "plots": [
    {
      "scope_type": "team",
      "scope_name": "core",
      "view": "models",
      "measure": "tokens",
      "model": null,
      "files": ["team-core__models__tokens.html", "team-core__models__tokens.png"],
      "status": "complete"
    }
  ]
}
```

`index.json` = what can I open (the cost `report.md` reads it, §14.5); `result.json` = full record.

### Resilience & exit codes

- A per-entry failure (unknown scope/model, no representable paths) is captured in that entry's
  `error` and does not abort the batch.
- Exit `0` all succeeded · `2` partial or failed · `1` could not start (unreadable config/run, existing
  `charts/` without `--force`, stem collision — nothing written).
- `result.json`/`index.json` are written for `0` and `2`; `plot_cost` re-renders `report.md` for `0`
  and `2` (it links what `index.json` lists). A `1` failure reports on stderr only.
- Every CLI prints its summary as one JSON line on stdout (as today); the pipeline records it (§14.4).

## 8. Rendering **[decided]**

One computed series set per panel (mean, band-low, band-high, optional samples, actuals) feeds two
renderers so HTML and PNG show the same thing.

| Chart | Source | Builder |
|---|---|---|
| forecast `models` | forecast run paths (+ actuals) | `utils/plot_forecast.py` (as today) |
| cost `total` / `<model>` | `CostRun.scope_paths` / `CostRun.paths` | `cost/presentation.py` `_fan_*`, gaining `band` + `samples` args (was fixed p10–p90) |
| cost `breakdown` | expected daily cost per model | `cost/presentation.py` `_breakdown_*` (unchanged) |

- **HTML** — Plotly with `include_plotlyjs=<relative path>` from the chart file to the JS: `plotly_js`
  when set, else `charts/plotly.min.js`. Either file is written from `plotly.offline.get_plotlyjs()`
  when missing (so its version matches the installed plotly).
- **Target.** `<run>/charts/` is the only allowed target: the "output must be outside the source
  forecast run" guard in `plot_forecast.py::export_html` is removed.
  Works offline; a chart is a few KB–hundreds of KB instead of ~4.3 MB. Atomic write.
- **PNG** — matplotlib via `forecast.forecasting.presentation._render_png` (Agg backend, no kaleido).

Multi-model forecast panels stack vertically as today. The subtitle states status, samples, band
percentiles, whether actuals were overlaid, and unavailable models.

## 9. Validation & errors **[decided]**

- Config: `schema_version == 1`; at least one of `forecast`/`cost`; entry `scope_type`/`scope_name`
  legal (§5); `models` only in `forecast`, `views` only in `cost` and from the allowed set; band
  `0<low<high<100`; `format ∈ {html,png,both}`; `samples ≥ 0`; no stem collisions after scope
  expansion.
- Selection errors reuse the existing `build_figure` validation; in batch mode they become per-entry
  `error`s rather than aborts.
- Structured JSON logging to stderr (existing `plot.*` events), extended with per-entry events and an
  actuals-source event.

## 10. README & examples **[decided]**

- `src/forecast/utils/README.md`: both CLIs, `plot.yaml` (§6), output contract (§7), shared JS (§8).
- `src/forecast/pipeline/README.md` (new): workspace layout + naming convention and pipeline usage (§14).
- `examples/` reorganized into workspaces (§14.6); HTML and `examples/assets/plotly.min.js` committed;
  the `*.html` gitignore rules removed.
- Operator guide `docs/operations/README-forecast.md` plot commands updated.

## 11. Tests **[decided]**

Follow the repo test-report framework (`docs/test-results/`, `test_forecast.sh`). Cover, as before:
actuals overlay (auto, override, unavailable, hash-mismatch note); band validation; CLI parsing incl.
conditional `--scope-name` and `--config` exclusivity; resilient batch; exit codes. New:

- `plot.yaml` sections, defaults/override, scope expansion, stem collision after expansion.
- Naming: the §7 examples produce exactly those stems.
- `charts/` inside the run; refused without `--force`, replaced with it; run readers still load.
- HTML references the JS by relative path (with and without `plotly_js`); no embedded plotly.js
  (file size well under 1 MB).
- Cost charts: band/samples honored; `breakdown` ignores them; `report.md` links exactly the
  `charts/index.json` files, and has no chart section without it.
- Relative dataset / forecast-run links resolve after moving a workspace + `dataset/` together (§14.3).
- Pipeline: stages in order, pass-through flags reach `forecast.sh`, stops at first failing stage.

## 12. Deferred / open

- **[deferred]** `combine:` — several entries as stacked panels in one file. Not needed while
  per-scope multi-model panels exist and many files are cheap; layout/scaling questions unsolved.
- **[deferred]** Persisting an observed-history summary inside the forecast run (self-contained
  plots without the input dataset) — a forecasting-module change, revisit if datasets routinely
  move.

## 13. Handshake resolutions

Resolved during the pre-coding handshake (implementer questions, coordinator answers), before
implementation started. Recorded here so the design stays authoritative for review.

- **Actuals sourcing reads two run artifacts, not one.** §3 names `resolved_config.yaml ->
  input.dataset_dir`, but `build_histories` also needs the forecast `origin` (only in
  `request.json`) and `history.lookback_days` / `history.minimum_days` /
  `history.minimum_active_days` / `allocation.lookback_days` (in `resolved_config.yaml`).
  `actuals.py` reads both files.
- **Actuals window = the lookback-bounded suffix, not full dataset history.** The plotted real
  line is exactly the `SeriesHistory.dates`/`.values` window `build_histories` returns for that
  scope x model (bounded by `history.lookback_days`, ending at the origin) — literally what the
  forecaster consumed, not a re-query of the dataset's full history. Plotting more history than
  the forecaster saw is a future extension, not part of this change.
- **Hash-integrity compare is auto-mode only.** The dataset-hash check against manifest
  `input_sources` runs only when the dataset was found at its recorded location. Under an
  explicit `--dataset` override, no comparison is attempted (a
  deliberately different dataset would otherwise always "mismatch").
- **Exit-code mechanism: status return, not exception-type dispatch.** `run_plots()` returns a
  status (`complete`/`partial`/`failed`); the CLI maps status to exit code (0 / 2). Structural
  ("could not start") problems — config parse, artifact load, slug collision — are raised
  *before* the per-entry loop and abort with exit 1, nothing written. Per-entry problems (unknown
  scope/model, no representable paths) are always captured into that entry's `result.error` and
  never abort the batch. No new exception hierarchy: a plain status return plus raise-before-loop
  is enough.
- **Operator-guide examples are in scope.** The ~6 worked command blocks in
  `docs/operations/README-forecast.md`'s "Interactive Plotly views" section use the old CLI
  surface (`--run`/`--org-id`/`--sample-paths`/`--output <file>`) and are rewritten to the new
  surface as part of this change, rather than left stale next to a "see also" pointer.
- **The real synthetic -> forecast -> plot integration test is required, not optional.** A test
  that runs the actual pipeline (synthetic dataset -> `run_forecast` -> plot) and asserts the
  actuals overlay equals an independent `build_histories` call against that same dataset is the
  only thing that proves the load-bearing design claim ("the overlay is exactly what the
  forecast saw") rather than a hand-built fixture's coincidental agreement. Fixtures pairing a
  run with a dataset are always built fresh under one `tmp_path` per test.

## 14. Workspace and pipeline **[decided]**

### 14.1 Terms

| Term | Meaning |
|---|---|
| dataset | Daily usage data, synthetic or real; exists independently of any forecast. `dataset/<name>/`; synthetic: `synth-<name>` (e.g. `synth-curve-guided`), real: any name. |
| workspace | One forecast (one dataset × one forecast window) and everything derived from it: cost runs (baseline + many scenarios), later budget, and their charts. Explicitly named by the operator. Next month and the month after are two workspaces. |
| link | A path to another artifact, recorded **relative to the recording run's directory**; the target's `manifest.json` hash (already recorded) is the integrity check. |

### 14.2 Layout convention

```text
dataset/
└── synth-curve-guided/                 # generated separately (synt_gen.sh)
assets/
└── plotly.min.js                       # one shared copy; plot.yaml plotly_js points here
<workspace>/
├── pipeline.yaml
├── plot.yaml                           # fixed name; the pipeline's plot stage uses it
├── pipeline_result.json                # written by every pipeline run (§14.4)
├── forecast/
│   ├── forecast.yaml                   # input.dataset_dir: ../../dataset/synth-curve-guided
│   └── run/                            # forecast run (+ charts/)
├── cost/
│   ├── scenarios/<name>.yaml
│   ├── baseline/                       # cost run, no scenario (+ charts/)
│   └── <name>/                         # one cost run per scenario file (+ charts/)
└── budget/                             # later
```

`baseline` and `scenarios` are reserved scenario file stems (the pipeline fails on either: `cost/scenarios`
would be overwritten).

### 14.3 Links

| Run file | Field | Today | Change |
|---|---|---|---|
| forecast `resolved_config.yaml` | `input.dataset_dir` | absolute | relative to the forecast run |
| forecast `request.json` | `dataset.path` | absolute | relative to the forecast run |
| cost `request.json` | `forecast_run.path` | as given on the CLI (cwd-relative) | relative to the cost run |
| cost `request.json` | `dataset` | copy of the forecast's | **removed** (no consumer; cost → forecast run → dataset) |

- `forecasting.artifacts.load_run` returns `SavedRun.dataset["path"]` resolved against the forecast run
  root, so every consumer (`cost/request.py`, the cost `--force` protect list in `cost/runner.py`,
  `utils/actuals.py`) gets a usable path. An absolute recorded path still resolves to itself.
- Cost still refuses a dataset whose manifest hash differs (`cost/request.py`); plot actuals still warn (§3).

### 14.4 Pipeline

`utils/pipeline.sh --config <workspace>/pipeline.yaml` → `python -m forecast.pipeline.cli_pipeline`.
A dumb orchestrator: it knows the layout (§14.2), calls the module CLIs, and holds no module settings.
Workspace = the directory holding `pipeline.yaml`; every CLI runs with that as its working directory.
Rule: a CLI argument that neither the module's own config nor the layout supplies goes in
`pipeline.yaml` under that stage and is passed through verbatim as `--<key> <value>` (scalars only).

```yaml
stages: [forecast, cost, plot]     # any subset; always run in this order
forecast:                          # forecast.sh flags, passed through
  unit: month
  offset: 1
  periods: 1
```

| Stage | Commands (paths relative to the workspace) |
|---|---|
| forecast | `forecast.sh --config forecast/forecast.yaml --output forecast/run --force` + `forecast:` flags |
| cost | `cost.sh --forecast-run forecast/run --output cost/baseline --force`; per `cost/scenarios/<name>.yaml`: `cost.sh --forecast-run forecast/run --scenario cost/scenarios/<name>.yaml --output cost/<name> --force` |
| plot | `plot_forecast.sh --artifact forecast/run --config plot.yaml --force`; per cost run directory: `plot_cost.sh --cost-run cost/<run> --config plot.yaml --force` |

- A listed stage re-runs and replaces its outputs (hence `--force`). Only `--config` on the command line.
- Stops at the first command that exits non-zero (`2` counts as failure); later commands and stages are
  recorded as `skipped`. Pipeline exit code = that command's code, else `0`.
- No synth stage, no staleness check (re-running `forecast` without `cost` leaves cost runs priced
  from the old forecast; their recorded `forecast_run.manifest_sha256` shows it). Budget becomes a
  stage when the budget module is revised.

**Result — `<workspace>/pipeline_result.json`**, written on every run, success or failure (also printed
as one JSON line on stdout). Each command's stderr streams through to the terminal as it runs.

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

| Field | Consumer |
|---|---|
| `status`, stage/command `status` | operator: what ran, what failed, what was skipped |
| `argv` | operator: rerun one step by hand |
| `exit_code`, `summary` | operator: the module's own error (`error`, `stage`) without scrolling logs |
| `stderr_tail` | last 50 stderr lines of a failed command (`null` on success) |
| `elapsed_seconds` | operator: where time goes |

A configuration error (unreadable `pipeline.yaml`, unknown stage, reserved scenario stem) is recorded
as `status: failed` with a top-level `"error": {"type": "input", "message": "…"}`, no stages run, exit `1`.

### 14.5 Changes to other modules (surgical)

| Module | Change |
|---|---|
| `forecast.forecasting` | Remove `write_charts` and its call in `write_presentation` (`presentation.py`), the `stages["charts"]` timing and `chart_count` in `runner.py`; `report.md` never linked charts. Record dataset links relative; `load_run` resolves them (§14.3). |
| `forecast.cost` | `render_report` no longer writes charts; its chart section lists `charts/index.json` entries when present, else is omitted. `report.md` and `charts/` are **not** listed in the cost manifest (so `plot_cost` can re-render the report). `_fan_*` gain `band`/`samples`. `forecast_run.path` relative; `dataset` removed from `request.json`; paths from `SavedRun` used as resolved (§14.3). |
| `forecast.utils` | §5–§9; new `cli_plot_cost.py`; `plot_config.py` scope rules (§5); `export_html` guard removed (§8). |
| `forecast.pipeline` (new) | `cli_pipeline.py`, `README.md`; `utils/pipeline.sh`. |

### 14.6 Examples

Move, no regeneration of datasets (not committed):

| Today | After |
|---|---|
| `examples/plots/curve-guided/{forecast.yaml, forecast/, plots.yaml}` + `examples/cost/*` | workspace `examples/curve-guided/` (forecast, cost baseline + 3 scenarios) |
| `examples/plots/known-event-spike/` | workspace `examples/known-event-spike/` (forecast only) |
| `<example>/dataset` (gitignored) | `examples/dataset/synth-curve-guided`, `examples/dataset/synth-baseline` |
| — | `examples/assets/plotly.min.js` |
| `examples/plots/quarterly.yaml` | removed (superseded by the workspace `plot.yaml` files) |
| `examples/plots/README.md`, `examples/cost/README.md` | one `examples/README.md` (workspaces, how to reproduce with the pipeline, cost results table) |
| `examples/plots/.gitignore`, `examples/cost/.gitignore` | one `examples/.gitignore`: `dataset/` only |

Built with the pipeline in `examples-test/` (datasets regenerated there; `synthetic/`, `curve/` copied
unchanged), verified, then `examples/` is removed from git and `examples-test/` renamed to `examples/`.
