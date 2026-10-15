# Forecast plotting utility — enhancements: implementation plan

> Revision 1 below (sections Context … Out of scope) is implemented. **Revision 2 (2026-09-27)** — one
> `plot.yaml` for forecast + cost, charts in the run, shared plotly.js, workspace + pipeline — is at the
> end of this file.

## Context
Enhance the `forecast.utils` Plotly experimentation utility per
[plot-forecast-enhancements-design](../design/plot-forecast-enhancements-design.md): real-data
overlay, configurable bands, cleaner CLI, batch config, HTML+PNG output, and an output-directory
contract (`index.json` + `result.json`). No forecasting-engine or artifact-format changes.

**Process:** implement only after the user enters implementation mode; then follow the
`talium-impl-workflow` skill (sonnet subagent, pre-coding handshake, worktree, tests + persisted
report, coordinator Major/Minor review against the design).

## Decisions (locked)
See design §2. Highlights: `--artifact` (org derived); `--output`/`output_dir` is a fresh run
directory; central = mean, band configurable (default p10/p90); actuals auto-load with override
and forecast-only fallback; one run per config, one file per entry, convention-named; HTML=Plotly,
PNG=matplotlib; resilient batch with `result.json` always written.

## Changes

### `src/forecast/utils/actuals.py` (new)
- `load_actuals(saved_run, *, dataset_override) -> (ActualsSource, series_lookup)`: resolve the
  dataset (override → `resolved_config.yaml` `input.dataset_dir` → unavailable), `load_dataset` +
  `build_histories`, and expose observed daily totals keyed by (scope_type, scope_id, model) over
  the historical window. Verify against manifest `input_sources`; return source ∈
  {auto, override, unavailable} and a hash-mismatch flag. Never raises on absent data — returns
  `unavailable`.

### `src/forecast/utils/plot_forecast.py`
- `build_figure`: add `band=(low, high)` (default (10,90)) replacing the hardcoded 0.10/0.90;
  accept an optional actuals lookup and draw the real line + origin seam per panel; extend the
  subtitle (band percentiles, actuals status). Keep mean central line and sample paths.
- Add `build_matplotlib_figure(...)` producing the same content for PNG.
- `export_html`: keep; add `export_png(figure, target)` delegating to
  `forecasting.presentation._render_png`. Factor a small `render(figure_bundle, stem, formats)`
  that writes `.html`/`.png` and returns the produced file list.

### `src/forecast/utils/plot_config.py` (new)
- `PlotConfig` / `PlotEntry` models (pydantic, `Strict` style like `synthetic/config.py`):
  `schema_version`, `artifact`, `output_dir`, optional `forecast-data.dataset`, `config-default`,
  `plots[]`. Resolve paths relative to the YAML. Validate per design §9 (band bounds, scope rules,
  format enum, samples ≥ 0, slug uniqueness). `slug(entry) -> str` for the filename convention.

### `src/forecast/utils/run.py` (new)
- `run_plots(spec) -> result_dict`: shared driver for single-plot and config modes. Creates the
  fresh output dir (refuse non-empty), iterates entries, captures per-entry errors, writes each
  entry's file(s), then writes `index.json` and `result.json`. Returns the result dict + status.

### `src/forecast/utils/cli_plot_forecast.py`
- New arg surface (design §5): `--artifact`, `--scope-type`, `--scope-name`, `--model`,
  `--samples`, `--band LOW HIGH`, `--dataset`, `--format`, `--output`, `--config`, `--help`.
  Mutually exclusive: `--config` vs. the selection flags. Rich help marking optional vs required.
- Single-plot mode builds a one-entry spec and calls `run_plots`; config mode loads `PlotConfig`
  and calls `run_plots`. Preserve/extend the `plot.*` structured JSON logging and exit codes
  (0/2/1 per design §7).

### `utils/plot_forecast.sh`
- No change needed (passes args through). Confirm `--help` and both modes work via the wrapper.

### Docs & examples
- Rewrite `src/forecast/utils/README.md` (design §10).
- Add `examples/plots/README.md` + a worked config (e.g. `examples/plots/quarterly.yaml`) using a
  real saved run; reference from the utility README and `docs/operations/README-forecast.md`.

### Tests (`tests/utils/`)
- Per design §11. Reuse existing forecast-run fixtures; add a fixture pairing a run with its input
  dataset for the actuals path. Assert on `result.json`/`index.json` content, produced files, exit
  codes, and forecast-only fallback. Record the run via `test_forecast.sh` into
  `docs/test-results/`.

## Sequencing
1. Handshake: brief the implementer subagent against this plan + the design; resolve concerns.
2. `plot_config.py` + validation (pure, testable first).
3. `actuals.py` (+ fixture) — actuals sourcing and fallback.
4. `plot_forecast.py` band + actuals + matplotlib figure + PNG export.
5. `run.py` driver + output-dir contract (`index.json`, `result.json`, slugs, resilience).
6. `cli_plot_forecast.py` new surface + mutual exclusion + help + exit codes.
7. README rewrite + examples.
8. Tests (may be a parallel subagent) + persisted report.
9. Coordinator review vs. design → Major/Minor list.

## Risks / watch-items
- Actuals aggregation must match the forecaster's scope×model totals — reuse `build_histories`,
  do not re-derive; verify on the paired fixture.
- `--output` semantics change (path → directory): update every doc/example and the operator guide.
- PNG figure must not drift from the HTML figure — build both from one computed bundle.
- Keep Plotly imported only by the utility (arch-doc boundary); matplotlib PNG via the existing
  presentation helper, no kaleido.

## Out of scope (design §12)
Combined multi-panel-across-entries export; baking observed history into the forecast run.

---

# Revision 2 — unified plotting, workspace, pipeline (2026-09-27)

## Context
Design: [plot-forecast-enhancements-design](../design/plot-forecast-enhancements-design.md) §5–§11 and
§14 (cited as §N); decisions D1–D30 in [plot brainstorm](../notes/plot-brainstorm.md). Covers the plot
utility, the surgical forecasting and cost changes it needs (§14.5), a new pipeline runner, and the
examples rebuild.

**Process:** start only after explicit approval of this plan; then run the `talium-impl-workflow` skill
(sonnet subagent, handshake, worktree, tests + persisted report, Major/Minor review).

## Deliverables
- `plot.yaml` with `forecast:` / `cost:` sections (§6); charts in `<run>/charts/` named
  `<scope>__<view>__<measure>` (§7); HTML referencing one shared `plotly.min.js` (§8).
- `utils/plot_cost.sh` → `forecast.utils.cli_plot_cost` (new); `plot_forecast.sh` updated (§5).
- Forecast and cost runs write no charts; relative links (§14.3, §14.5).
- `src/forecast/pipeline/` (`cli_pipeline.py`, `README.md`) + `utils/pipeline.sh`; `pipeline_result.json` (§14.4).
- `examples/` rebuilt as workspaces with the pipeline; HTML + `plotly.min.js` committed (§14.6).
- READMEs and operator guide updated.

## Conventions (repo)
- Python 3.11; no new dependencies (plotly, matplotlib, pydantic, PyYAML, structlog present).
- Structured JSON logging (structlog) to stderr; one JSON summary line on stdout per CLI.
- CLI entries `cli_plot_cost.py`, `cli_pipeline.py`; wrappers follow `utils/cost.sh` (`_forecast_env.sh`).
- Surgical: touch only what §14.5 lists; no schema-version bumps, no backward compatibility (D20).
- Identity by name; hashes only as integrity checks in manifests.
- Deferred work in code gets `TODO(<issue or §>)`.
- Commits reference #78; no tool attribution in commit messages.

## Tasks

Dependencies in brackets. T1, T3 can start in parallel.

### T0 — Raise issue (needs approval: outward-facing)
- Raised as **#78**. Commits reference it.

### T1 — Forecasting: no charts, relative dataset link [—]
Files: `forecasting/runner.py`, `forecasting/presentation.py`, `forecasting/artifacts.py`, forecasting README.
- Remove `write_charts`, its call in `write_presentation`, `stages["charts"]`, `chart_count` (§14.5).
- Write `resolved_config.yaml` `input.dataset_dir` and `request.json` `dataset.path` relative to the run
  (`os.path.relpath`, §14.3).
- `load_run` returns `SavedRun.dataset["path"]` resolved against the run root.

Accept: a new run has no `charts/` and its manifest lists none; both recorded paths are relative;
moving run + dataset together (same relative position) → `load_run(...).dataset["path"]` exists and
`load_dataset` reads it; an old run with absolute paths still loads.

### T2 — Cost: no charts, report reads `charts/index.json`, relative link [T1]
Files: `cost/presentation.py`, `cost/artifacts.py`, `cost/runner.py`, `cost/request.py`, `cost/schemas.py`, cost README.
- `render_report` stops calling `_write_charts`; chart section lists `charts/index.json` entries
  (labels from `scope_type`, `scope_name`, `view`, `model`) when the file exists, else omitted (§14.5, §7).
- `report.md` and `charts/` are not listed in the manifest; reader no longer checks them.
- `_fan_matplotlib` / `_fan_plotly` take `band` and `samples` (were fixed p10–p90).
- `request.json`: `forecast_run.path` relative to the cost run; `dataset` removed (schema + writer).
- `request.py` / `runner.py` (`--force` protect list) use the resolved `SavedRun.dataset["path"]`.

Accept: cost run has no `charts/`, report has no chart section, `load_cost_run` loads; with a
hand-written `charts/index.json` the report links exactly those files; re-rendering `report.md` after
the run → `load_cost_run` still loads; `_fan_*` with band (5, 95) plots p5/p95; same dollar figures as
before on the curve-guided forecast.

### T3 — `plot.yaml` model [—]
Files: `utils/plot_config.py`.
- Top level: `schema_version: 1`, optional `plotly_js` (resolved relative to the YAML), `defaults`
  (`format`, `band`, `samples`), optional `forecast` and `cost` lists (at least one) (§6).
- Entries: `scope_type`, optional `scope_name`; forecast `models`; cost `views` ⊂ {total, breakdown,
  per-model}, default `[total, breakdown]`; per-entry `format`/`band`/`samples` override.
- Scope rules: `scope_name` forbidden for `organization`; omitted for `team`/`project` = expand (§5).
- Old keys (`artifact`, `output_dir`, `forecast-data`, `config-default`, `plots`) rejected.
- `stem(scope, view, measure)` per §7; collision detection after expansion (expansion needs the run's
  scope list, so it runs in T4 before any write).

Accept: the §6 example validates; `models` under `cost` and `views` under `forecast` rejected; `scope_name`
on organization rejected; the three §7 naming examples produce exactly the listed stems.

### T4 — Output contract and HTML [T3]
Files: `utils/run.py`, `utils/plot_forecast.py`.
- Write into `<run>/charts/`; refuse an existing `charts/` unless `--force` (then replace it); section
  absent → leave `charts/` untouched, exit 0 (§6, §7).
- Expand entries against the run's scopes; collisions → exit 1 before writing.
- `index.json` / `result.json` per §7 (one item per stem, `view`, `measure`, `model`); exit 0 / 2 / 1.
- HTML: `include_plotlyjs=<relative path>` to `plotly_js` or `charts/plotly.min.js`; write the JS from
  `get_plotlyjs()` when missing. Remove the outside-the-run guard in `export_html` (§8).

Accept: HTML files contain a `<script src="…plotly.min.js">` with a correct relative path and are
< 1 MB; exactly one JS copy written; existing `charts/` refused without `--force`; forecast and cost
readers still load the run after plotting.

### T5 — `plot_forecast.sh` [T4]
Files: `utils/cli_plot_forecast.py`.
- `--output` removed; `--artifact` required in both modes; `--config` excludes exactly
  `--scope-type --scope-name --model --samples --band --format`; `--dataset`, `--force` allowed with both (§5).
- Config mode draws the `forecast:` section; single-plot mode = one-entry `forecast:` section.
- Stem `<scope>__models__tokens`; actuals behavior unchanged (§3).

Accept: `--help` without reading data; single-plot and config mode write the same file for the same
entry; `--scope-type team` without `--scope-name` → one file per team.

### T6 — `plot_cost.sh` [T2, T4]
Files: new `utils/cli_plot_cost.py`, `utils/plot_cost.sh`; chart builders reused from `cost/presentation.py`.
- `--cost-run`, `--config`, `--force` (§5). Load with `load_cost_run`; draw `cost:` entries: `total`
  (scope paths), `per-model` (series paths), `breakdown` (expected cost; ignores band/samples) (§8).
- After charts: re-render `report.md` via `cost.presentation.render_report` for exit 0 and 2 (§7).

Accept: default views on 9 scopes → 18 stems; `per-model` on the organization → one file per model with
`model` set in both JSON files; report links exactly the `index.json` files.

### T7 — Pipeline [T5, T6]
Files: new `src/forecast/pipeline/{__init__.py,cli_pipeline.py,README.md}`, `utils/pipeline.sh`.
- `--config <workspace>/pipeline.yaml` only. Workspace = its directory; each CLI runs with cwd = workspace.
- `stages` subset of forecast, cost, plot, run in that order; stage blocks → `--<key> <value>` (scalars).
- Commands exactly as the §14.4 table; reserved scenario stems `baseline`, `scenarios` → config error.
- Call the `utils/*.sh` wrappers via `subprocess`; stream stderr through and keep its last 50 lines;
  parse the stdout JSON line as `summary`.
- Stop at the first non-zero exit; remaining commands/stages `skipped`.
- Write `pipeline_result.json` (§14.4) on every run, including config errors; print it as one JSON line.
- README: workspace terms, layout tree, chart naming, links, `pipeline.yaml`, result file, examples.

Accept: a failing cost scenario → `pipeline_result.json` status `failed`, that command's `exit_code`,
`summary.error`, `stderr_tail`, later commands `skipped`, pipeline exit = that code; unknown stage →
exit 1, result with top-level `error`; `forecast:` flags reach `forecast.sh` argv.

### T8 — Docs [T5–T7]
- `src/forecast/utils/README.md`: both CLIs, `plot.yaml`, output contract, shared JS.
- `src/forecast/cost/README.md`, `src/forecast/forecasting/README.md`: no charts in runs; links relative.
- `docs/operations/README-forecast.md`: plot commands; pointer to the pipeline README.

### T9 — Examples rebuild [T7] (delete step needs user confirmation)
- Build `examples-test/` (§14.6): copy `synthetic/`, `curve/` unchanged; regenerate
  `dataset/synth-curve-guided` and `dataset/synth-baseline` with `synt_gen.sh`; workspaces
  `curve-guided/` (forecast yaml from `examples/plots/curve-guided/forecast.yaml`, 3 scenarios from
  `examples/cost/scenarios/`) and `known-event-spike/`, each with `pipeline.yaml` + `plot.yaml`
  (`plotly_js: ../assets/plotly.min.js`); `examples/.gitignore` = `dataset/`; one `examples/README.md`.
- Run `pipeline.sh` on each workspace.
- Verify: both `pipeline_result.json` `complete`; runs load via `load_run` / `load_cost_run`; every
  `charts/index.json` matches its directory; HTML opens offline (browser test); cost expected/p10/p90
  per scope equal the current `examples/cost/README.md` table.
- Show the user the exact delete list; on confirmation `git rm -r examples`, `git mv examples-test
  examples`, fix links that pointed at `examples/plots/…` / `examples/cost/…` (grep).

## Tests
| File | Covers |
|---|---|
| `tests/forecasting/` (existing, extended) | T1: no charts, relative links, `load_run` resolution, old absolute runs |
| `tests/cost/test_presentation.py`, `test_artifacts.py`, `test_request.py` | T2 |
| `tests/utils/test_plot_config.py` | T3 |
| `tests/utils/test_plot_force.py`, `test_plot_forecast.py` | T4: charts/ contract, JS link, sizes |
| `tests/utils/test_plot_forecast_cli.py` | T5 |
| `tests/utils/test_plot_cost.py` (new) | T6 |
| `tests/pipeline/test_pipeline.py` (new) | T7, with stub CLIs for failure paths + one real synthetic → forecast → cost → plot run |
| `tests/utils/test_plot_forecast_browser.py` | HTML renders offline with the shared JS |

Test report persisted under `docs/test/reports/`.

## Out of scope
- Workspace naming conventions (D10; later).
- Synth stage, staleness detection, budget stage (§14.4).
- Combined multi-entry panels (§12).
- Restructuring design §2/§13.

## Verification (end to end)
1. `pytest econ-forecast/tests -q` green (report persisted).
2. `pipeline.sh --config examples/curve-guided/pipeline.yaml` → exit 0, `pipeline_result.json` complete.
3. `examples/curve-guided/cost/team3-cheaper-models/report.md` links `charts/team-team3__breakdown__cost.html`, which opens offline.
4. `du -sh examples` — no HTML file > 1 MB; one `plotly.min.js`.
5. Break one scenario file → pipeline exit ≠ 0; `pipeline_result.json` names the command, its error, and the skipped rest.
