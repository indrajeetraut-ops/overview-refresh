# Run synthetic usage, forecasts, and local plots

This local workflow creates synthetic users, a Team roster, daily token usage, a forecast, and interactive Plotly views. It needs Python 3.11+, pinned repository dependencies, and local disk. Output is **tokens**, not dollars. Budget calculation, scenarios, and a general query API are separate future work.

The latest persisted test evidence is in the [test-results index](../test-results/README.md),
with a readable per-test report and machine-readable JUnit XML.

Run commands from the repository root. Each command writes a new output path; existing outputs are refused. To recreate one, add `--force` (`synt_gen.sh`, `forecast.sh`, `curve.sh`, `cost.sh`) — it deletes and recreates only the resolved output directory. The plot CLIs (`plot_forecast.sh`, `plot_cost.sh`) write into the run's own `charts/`; `--force` replaces only that directory.

## First run

```bash
cd /Users/sumandra/projects/talium/forecasting/code-econ
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
WORK=$(mktemp -d /tmp/forecast-guide.XXXXXX)
mkdir -p "$WORK/config/synthetic" "$WORK/config/forecasting" "$WORK/artifacts"
cp econ-forecast/config/synthetic/*.yaml "$WORK/config/synthetic/"
cp econ-forecast/config/forecasting/forecast.yaml "$WORK/config/forecasting/"

./econ-forecast/utils/synt_gen.sh --op user-create \
  --config "$WORK/config/synthetic/users.yaml" --output "$WORK/artifacts/users"
./econ-forecast/utils/synt_gen.sh --op roster-create \
  --config "$WORK/config/synthetic/roster.yaml" --output "$WORK/artifacts/roster"
./econ-forecast/utils/synt_gen.sh --op series-create \
  --config "$WORK/config/synthetic/series.yaml" --output "$WORK/artifacts/series-60d"

# Include a Project in this same forecast before validation.
venv/bin/python - "$WORK/config/forecasting/forecast.yaml" <<'PYCODE'
import pathlib, sys, yaml
path = pathlib.Path(sys.argv[1])
config = yaml.safe_load(path.read_text())
config["scopes"].append({"type": "project", "name": "payment_service_improvement"})
path.write_text(yaml.safe_dump(config, sort_keys=False))
PYCODE

./econ-forecast/utils/forecast.sh \
  --config "$WORK/config/forecasting/forecast.yaml" \
  --unit month --offset 1 --periods 1 --validate-only
./econ-forecast/utils/forecast.sh \
  --config "$WORK/config/forecasting/forecast.yaml" \
  --unit month --offset 1 --periods 1 \
  --output "$WORK/artifacts/september-forecast"
```

The copied YAML retains its relative references to `$WORK/artifacts/`. Paths *inside YAML* resolve relative to that YAML file; CLI paths resolve relative to the shell's current directory. The template makes 100 users in `talium.ai`, rosters 50 across three Teams, and covers July 1–August 29, 2026. Team and Project **names** are the operator identifiers. Set `team_name` in roster definitions, `name` in Project definitions and forecast Team/Project scopes, and those same Project names in `project_mix`. There are no manual Team or Project IDs in operator configuration.

### Migrating an existing config copy

| Earlier operator input | Name-based input |
|---|---|
| Roster Team `team_id` plus `team_name` | Keep `team_name`; remove `team_id` |
| Project `project_id` plus `name` | Keep `name`; remove `project_id` |
| `project_mix` keys containing old Project IDs | Replace each key with its Project name |
| Forecast Team/Project scope `id` | Use `name` with the saved Team/Project name |
| Plot `--scope-id` | Use `--scope-name` |

Existing saved datasets and runs remain unchanged. Their internal IDs are resolved through saved name metadata; the migration applies to **operator configuration and commands**, not saved artifacts.

## Calendar request

Omitting `--origin` selects the dataset's **declared last day**, August 29 in this example, including a covered day with zero usage. The resolved date is saved in `request.json` and shown in `report.md`. Explicit `--origin YYYY-MM-DD` chooses an earlier historical cutoff; later observations are excluded. A collection outage on the chosen date remains an eligibility issue and does not move the cutoff backward.

| Option | Default/example | Meaning |
|---|---|---|
| `--origin` | Omitted → declared dataset end | Last historical date used, inclusive |
| `--unit` | `month` | `week` (Monday–Sunday) or calendar `month` |
| `--offset` | `1` | First future calendar period to publish |
| `--periods` | `1` | Number of consecutive periods to publish |
| `--validate-only` | Off | Check data/config/history eligibility without writing a run |

For the example origin, month offset 1 publishes September 1–30. Week offset 1 publishes August 31–September 6. Month offset 3 publishes November 1–30. Intervening dates are sampled to preserve stable sample IDs across offset requests but excluded from published totals.

## Configuration reference

| Copied file | Main settings | Output |
|---|---|---|
| `users.yaml` | `count`, `org_domain`, locale, seed | Fake identities |
| `roster.yaml` | Saved users path; unique `team_name`, positive `count`, seed | Fixed membership |
| `series.yaml` | Saved users/roster, dates, seeds, Team profiles, Project names/mixes, agents/models, events | Usage and coverage |
| `forecast.yaml` | Dataset path, Organization and named scopes, history, ETS model, allocation, uncertainty, optional known events, workers | Forecast paths and summaries |

Series defaults from the maintained template:

| Parameter | Value | Meaning |
|---|---:|---|
| `daily_variation_pct` | 30% | Uniform volume factor 0.70–1.30 |
| `cache_hit_ratio` | 0.95 | Share of processed input read from cache |
| `cache_hit_variation_pp` | ±5 percentage points | Cache-hit ratio varies 0.90–1.00 (capped at 1) |
| `cache_write_ratio` | 0.997 | Share of non-cache-read input that is a cache write |
| `cache_write_1h_share` | 0.97 | Share of cache writes at the 1-hour lifetime |
| `output_ratio` | 0.03 | Approximate output / processed input |
| `daily_zero_probability` | 0.10 | Ordinary inactivity is drawn per user on every day |
| `weekend_reduction_probability` | not set (default 0) | No weekend drop. Opt in with a value > 0 — see [`examples/synthetic/weekend-drop.yaml`](../../examples/synthetic/weekend-drop.yaml) |

The template follows **one team per project** (each Project appears in exactly one Team's `project_mix`), matching the forecasting scope tree.

```text
processed_input = input + cache_write_5m + cache_write_1h + cache_read
total_tokens = processed_input + output
output ≈ 0.03 × processed_input (per-record rounding applies)
Σ agent usage_share = 1
For each agent: Σ model share = 1
For each Team: Σ project_mix share = 1
```

Stored `input` is uncached input only; cache writes (`cache_write_5m`, `cache_write_1h`) and `cache_read` are separate. Datasets and forecast runs written before this contract (schema 1) are rejected: regenerate the dataset and rerun the forecast. `daily_input_tokens` sets processed input for an active day (weekdays and, by default, weekends alike), before output. Adding a model divides that daily total across models. One active user-day chooses one Project across its agent/model records. Generation keeps zero-consumption days covered; an absent/outage coverage record in real data means **unknown**, not zero.

The maintained forecast YAML sets lookback 90 days; four consecutive positive calendar days establish startup; at least 14 consecutive covered days ending at origin are needed for a qualified history; allocation window 28 days; 1,000 uncertainty paths, seed 42; one worker. These values are required YAML inputs, not loader defaults.

### Forecast model (ETS)

```yaml
model: {method: ets, seasonal: auto, trend: auto}   # auto = learned from data; none = off
uncertainty: {paths: 1000, seed: 42, max_negative_correction_ratio: 0.05}
```

| Usable days | What `auto` fits (first that converges) |
|---|---|
| 14–27 | level-only |
| 28+ | weekly-seasonal + damped trend → weekly-seasonal → level-only |

- There is **no weekend setting in `forecast.yaml`**. If the data has a weekly pattern (e.g. generated with a weekend drop), the seasonal tier learns it; if not, none is imposed.
- `seasonal: none` forces level-only even with `trend: auto` (no trend-only tier).
- Paths come from a **joint residual bootstrap**: each future day draws one historical error date shared by all active models in the scope (needs ≥ 14 shared dates, else `sampling_failed`).
- Negative simulated values are clipped to 0. If clipping removes more than 5% of a series' requested tokens, the series stays `qualified` but is flagged `excess_negative_correction` — don't use it for budget risk.
- `diagnostics.json` records history windows, ETS `fit_attempts`, the residual window, corrections, applied known events, and failure reasons. `inactive` means assumed continued inactivity; quantiles are uncalibrated model estimates.

### Declaring a known future spike

A one-off spike in history washes out of the forecast. A spike you expect (release, migration) is declared in `forecast.yaml`:

```yaml
known_events:
  org:      {start_date: 2026-09-14, end_date: 2026-09-18, multiplier: 1.4, spread: 0.15}
  # teams:    {team1: {…}}
  # projects: {payment_service_improvement: {…}}
  # agents:   {claude: {…}}
```

- Each path draws one factor from `Uniform(1.25, 1.55)` for the whole window → the spike shows in the mean **and** widens the band.
- Most specific scope wins (project → team → org); an agent event multiplies on top for that agent's models.
- A project/team event changes only its own series; it does not yet roll up into team/org totals.
- Worked, committed example: [`examples/known-event-spike`](../../examples/known-event-spike).

## Changing a scenario

Keep the saved users and roster, copy `series.yaml` in the same config directory, and write a fresh series output. With unchanged start date and seeds, extending `series.days` preserves the earlier record prefix. Change `profile_seed` to reassign profiles or `usage_seed` to vary activity and consumption independently. For a new forecast, point the copied `forecast.yaml` at the new series directory.

| Series setting | Effect |
|---|---|
| `series.days` | Number of consecutive calendar dates from `start_date` |
| `events.spike_rules` | Named weekday date-level rules. Omit a rule's range for the full series or configure an inclusive range. Team and technology targets constrain proportional additive load. |
| `events.zero_periods` | Inclusive forced-zero dates; coverage remains complete |

## Runtime and deployment

| Workload | Observed local example | Planning guidance |
|---|---|---|
| Three synthetic generation stages | Under 1 second of generator time on a measured macOS run | Allow dependency installation and local disk separately |
| Forecast runtime | Measure after changing the dataset and path count | ETS fitting scales with series count × tiers tried; measure on the target host |

A CPU-only local or AWS batch host can run the wrappers after Python dependencies and data are staged. No database, GPU, provider key, or runtime network connection is required. On a remote Linux host, copy exported HTML to your local machine (for example, `scp user@host:/absolute/path/team1-claude.html .`) and open it in a browser. These measured figures are example observations, not capacity guarantees.

## Output files

The commands create this directory hierarchy (one `.npy` and chart per usable scope/model):

```text
$WORK/artifacts/
├── users/
│   ├── config.yaml
│   ├── users.jsonl
│   ├── report.md
│   └── manifest.json
├── roster/
│   ├── config.yaml
│   ├── teams.jsonl
│   ├── inputs/
│   ├── report.md
│   └── manifest.json
├── series-60d/
│   ├── config.yaml
│   ├── usage.jsonl
│   ├── coverage.jsonl
│   ├── user_profiles.jsonl
│   ├── generation_events.jsonl
│   ├── inputs/
│   ├── report.md
│   └── manifest.json
└── september-forecast/
    ├── resolved_config.yaml
    ├── request.json
    ├── report.md
    ├── manifest.json
    ├── daily_expected.jsonl
    ├── period_summary.json
    ├── allocations.json
    ├── diagnostics.json
    ├── paths/
    │   ├── index.json
    │   └── *.npy
    └── charts/
        └── *.png
```

| File | Purpose |
|---|---|
| `config.yaml`, `resolved_config.yaml` | Effective stage settings and forecast config |
| `inputs/` | Saved identity and roster snapshots for replay |
| `users.jsonl`, `teams.jsonl` | Synthetic identity and Team roster |
| `usage.jsonl`, `coverage.jsonl` | Consumption and collection coverage, including covered zero days |
| `user_profiles.jsonl`, `generation_events.jsonl` | Assigned profiles and applied generation events |
| `request.json` | Resolved origin, requested dates, selected scopes |
| `paths/index.json`, `*.npy` | Scope/model map and float64 date × sample trajectories |
| `daily_expected.jsonl` | Daily expected token categories and agent allocation |
| `period_summary.json` | Period and requested-window totals and whole-period p10/p50/p90 |
| `allocations.json` | Fixed recent agent/category shares and evidence |
| `diagnostics.json` | Eligibility, ETS fit attempts, residual window, corrections, applied known events, failures, runtime |
| `report.md`, `charts/*.png` | Human-readable table report and static model charts |
| `manifest.json` | Completion marker, hashes, versions, and artifact inventory |

### What a forecast sample represents

Each `paths/*.npy` file belongs to one selected scope and model. Its rows are requested dates, and its columns are simulated possible futures, numbered by `sample_ids` in `paths/index.json`. A cell is the model's projected **total tokens** on that date in that future. A sample is not a user, agent, historical observation, or alternative model mix. The default 1,000 columns represent 1,000 possible day-by-day trajectories, not 1,000 observed token counts.

The forecast uses those trajectories in different ways:

- **Daily expectation:** Average all sample columns for each date. `daily_expected.jsonl` then divides that expected scope/model total among agents and token categories using fixed shares estimated from recent historical usage. The results may be fractional even though recorded usage tokens are integers.
- **Period expectation and percentiles:** Sum the dates **within each sample column** to get one possible total for the period. `period_summary.json` reports the mean and p10/p50/p90 across those whole-period totals. For a complete scope aggregate, combine matching sample IDs across its models before calculating percentiles; do not add model or daily percentiles.
- **Charts:** The static charts and Plotly views show daily means and pointwise p10–p90 bands. Plotly may display a few individual trajectories, but its means and bands still use all saved samples. A daily band is not a whole-period range.

These are model-based, uncalibrated uncertainty estimates. More simulated samples reduce sampling noise; they do not add historical evidence or guarantee that future consumption falls within the reported ranges. Agent/category shares stay fixed across the simulated period, and no separate agent path files are saved.

The forecast report has a scope-status table, a models/evidence-window table, and a requested-window totals table. Totals distinguish model rows from a `scope aggregate`; unavailable components have no numeric total. All scope views overlap, so do not add Organization, Team, and Project totals. Do not sum per-model percentiles to derive an aggregate percentile. Saved JSON remains the precise source; report values are formatted to three decimal places.

JSONL stores one complete JSON object per physical line. This is the first usage row from the fresh guide run, expanded for readability:

```json
{
  "agent_type": "claude",
  "billing_mode": "api",
  "date": "2026-07-01",
  "model": "claude-opus-5",
  "org_id": "talium.ai",
  "project_id": "19421d46-f064-56d8-a0bf-bfa6a4bed589",
  "team_id": "4500f1dc-b228-5567-baec-a2d8f58b19fb",
  "tokens": {
    "cache_read": 162487,
    "cache_write_1h": 46338,
    "cache_write_5m": 1434,
    "input": 87,
    "output": 6280
  },
  "user_id": "0986e000-1beb-5460-9a52-a7d0d8ac9142"
}
```

This illustrates the shape of a requested-window `period_summary.json` Team model entry. The numeric values are examples; a new run will calculate its own values. The file encloses entries in a root object with `schema_version: 1`:

```json
{
  "assumptions": [
    "uncalibrated_model_estimate",
    "fixed_allocation_shares",
    "common_residual_window"
  ],
  "end": "2026-09-30",
  "expected_tokens": 23104314.51563751,
  "model": "gpt-5.6-sol",
  "org_id": "talium.ai",
  "p10": 20574790.543073297,
  "p50": 22913512.03673075,
  "p90": 25914331.27148447,
  "period_type": "requested",
  "scope_id": "b4d8e005-d79c-59c2-911d-3125c0637fe6",
  "scope_type": "team",
  "start": "2026-09-01",
  "status": "qualified"
}
```

The p90 is a percentile of **whole-period token totals**, not a budget probability or calibrated guarantee. A `model: null` summary row identifies a complete scope aggregate.

Saved JSON may contain internal `team_id`, `project_id`, or `scope_id` fields for stable identity and integrity checks. Those are artifact details; select scopes by name in operator inputs. `paths/index.json` supplies the exact model/file map and sample ordering; filenames are descriptive, not a parser contract. A completed partial run still has a complete manifest and explicit unavailable statuses. A directory without a complete manifest is unreadable.

## Interactive Plotly + PNG views

Charts are drawn into the run they show: `plot_forecast.sh` writes `<forecast-run>/charts/`,
`plot_cost.sh` writes `<cost-run>/charts/` and re-renders that run's `report.md` to link them.
An existing `charts/` is refused unless `--force` (which replaces only `charts/`). Neither the
forecast nor the cost run itself draws charts.

`--artifact` names the saved forecast run (the Organization is derived from it). `--samples` is
optional. **Leave it out for the main graph only:** a daily mean line and a configurable
uncertainty band (`--band LOW HIGH`, default `10 90`) for each selected model. When the run's
recorded input dataset is still readable (the link is relative to the run), the real observed
series is overlaid up to the forecast origin automatically (`--dataset <dir>` overrides it);
otherwise the chart falls back to forecast-only with a note. The band still uses every saved
simulation sample.

```bash
RUN="$WORK/artifacts/september-forecast"

# Main graph only; no individual sample lines.
./econ-forecast/utils/plot_forecast.sh --artifact "$RUN" --scope-type organization

# Ten sample lines for one Team model, band widened to p5-p95 (replaces charts/).
./econ-forecast/utils/plot_forecast.sh --artifact "$RUN" --force \
  --scope-type team --scope-name team1 --model claude-sonnet-5 --samples 10 --band 5 95

# One file per Project (scope name omitted), every available model; PNG only.
./econ-forecast/utils/plot_forecast.sh --artifact "$RUN" --force \
  --scope-type project --format png

# Several plots from one plot.yaml (forecast: section).
./econ-forecast/utils/plot_forecast.sh --artifact "$RUN" --config "$WORK/plot.yaml" --force

# Cost charts (cost: section of the same plot.yaml); report.md then links them.
./econ-forecast/utils/plot_cost.sh --cost-run "$WORK/artifacts/sep-cost-baseline" \
  --config "$WORK/plot.yaml"

open "$RUN/charts/team-team1__models__tokens.html"  # macOS
```

`--scope-name` must be omitted for Organization; for Team or Project, omitting it writes one
file per Team/Project in the run. Each file stacks the selected models (omit `--model` for all
available). `--config` is mutually exclusive with `--scope-type --scope-name --model --samples
--band --format`; `--dataset` and `--force` work with both modes.

Files are named `<scope>__<view>__<measure>`, e.g. `team-team1__models__tokens.html`,
`team-team3__breakdown__cost.png`, `organization-talium-ai__claude-opus-5__cost.html` (a cost
`per-model` view). `charts/index.json` lists what can be opened; `charts/result.json` is the
full record (per-chart status, actuals source, band, counts, timing, versions). HTML loads
Plotly from one shared `plotly.min.js` by relative path (`plotly_js` in `plot.yaml`, else
`charts/plotly.min.js`), so charts are small and open offline. `plot.yaml`, the cost views
(`total`, `breakdown`, `per-model`) and the output contract: [utils README](../../src/forecast/utils/README.md).

An incomplete scope/model remains listed in the chart subtitle and `result.json` as unavailable. If at least one selected series has paths, the chart shows those usable panels and the command exits 2 for a partial result. A chart with nothing plottable fails (recorded in `result.json`, no file for it) without aborting the rest of a batch.

| `--samples` setting | What the chart shows for each selected model |
|---|---|
| Omitted or `0` | Mean line and pointwise band; no individual sample lines. |
| Positive number `N` | The same mean and band, plus the first `N` saved sample trajectories as individual lines, capped at the number saved. For example, `20` shows sample IDs 0–19 when at least 20 exist. |

This option changes display only: it neither generates new samples nor selects them at random. The mean and band always use **all** saved paths. With 1,000 saved paths and one selected model, `--samples 20` draws 20 individual futures while calculating the mean and band from all 1,000.

Hover for dates/tokens; drag to zoom, double-click to reset, and click the legend to toggle traces. HTML opens offline without a server (it needs the shared `plotly.min.js` beside it at the recorded relative path). PNG is a static matplotlib chart with the same mean/band/samples/actuals content. Export does not launch a browser automatically.

### Whole workspace in one command

A workspace holds one forecast and everything derived from it (cost baseline, scenarios,
charts). `utils/pipeline.sh --config <workspace>/pipeline.yaml` runs its forecast, cost and plot
stages in order and writes `<workspace>/pipeline_result.json`; see the
[pipeline README](../../src/forecast/pipeline/README.md) for the layout, `pipeline.yaml` and the
result file.

## Results and problems

The CLIs emit one JSON summary on stdout and structured progress/errors on stderr. Exit 0 means everything requested succeeded, exit 2 means an invalid/partial plot request (or a partial forecast/synthetic result), and exit 1 means the run could not start at all (unreadable config/artifact, a structural config error, or an execution/storage failure) — nothing is written in that case for the plotting utility. Plot selection can likewise return partial exit 2 while preserving usable panels and writing `index.json`/`result.json`. `--help` needs no dataset.

For a bad path, check YAML-relative versus shell-relative resolution. For an unknown name, inspect saved Team/Project names within the selected Organization. For insufficient history, inspect coverage, startup streak, origin, and gaps in `diagnostics.json`; `--validate-only` does not generate paths. For retries use a new output path, or the same one with `--force`. For a failed pipeline run, `pipeline_result.json` names the failing command (`argv`), its `summary.error` and the last 50 stderr lines.

Full schemas and implementation details: [synthetic](../../src/forecast/synthetic/README.md), [forecasting](../../src/forecast/forecasting/README.md), [Plotly utility](../../src/forecast/utils/README.md), [pipeline](../../src/forecast/pipeline/README.md), and [forecasting model design](../design/forecasting-model-design.md).
