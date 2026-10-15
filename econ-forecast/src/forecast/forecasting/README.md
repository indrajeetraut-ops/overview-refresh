# Forecasting daily token consumption

For a complete first run through generation, forecasting, and plotting, see the [operator guide](../../../docs/operations/README-forecast.md).

The module provides the forecast library, CLI/shell wrapper, completed-run reader, data-driven ETS paths with a dated known-event overlay, allocation/summary artifacts, and minimal Markdown/PNG presentation. Budget and scenario calculation remain separate modules.

Read the [forecasting model design](../../../docs/design/forecasting-model-design.md) (the single authoritative design) and the [commented configuration](../../../config/forecasting/forecast.yaml). The model is data-driven **ETS** (statsmodels) plus a **dated known-event overlay**; the earlier `weekday_bootstrap` model is retired.

## What this module produces

Forecasting reads daily usage and coverage from one shared dataset, automatically aggregates requested Organization, Team, and Project scopes, and predicts daily consumption separately for each scope/model. A separate reporting component produces the default `report.md` from saved outputs; charts are drawn separately by the plotting utility (`forecast.utils`) into the run's own `charts/`, from `plot.yaml`. Budget and scenario modules reuse those outputs without refitting the consumption model. An interactive question/query API is deferred.

Reporting will have its own functional spec and detailed design. For early local experimentation, the [Plotly utility](../utils/README.md) reads saved outputs and writes a self-contained interactive HTML graph with hover, zoom/pan, reset, and model/path visibility without a server or network dependency.

The target is total tokens:

```text
total tokens = input + cache_write_5m + cache_write_1h + cache_read + output
```

Stored `input` is uncached input only; cache writes (5-minute and 1-hour) and cache reads are separate types, so the total is unchanged. A dataset or forecast run at schema version 1 is rejected with a regenerate/rerun message. A model used through several agents is sampled on their combined user/scope/model consumption; agent/category shares provide the later breakdown. Organization, Team, and Project views overlap and are not guaranteed to reconcile; do not add them together as disjoint usage.

## CLI and shell workflow

The companion `econ-forecast/utils/forecast.sh` calls `python -m forecast.forecasting.cli_forecast`. Python callers use `run_forecast(...)`, and consumers open completed outputs with `load_run(...)`.

Install the pinned repository environment on macOS or Linux:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
```

From the repository root, using the repository virtual environment with the pinned requirements installed:

```bash
./econ-forecast/utils/forecast.sh \
  --config econ-forecast/config/forecasting/forecast.yaml \
  --origin 2026-08-29 --unit month --offset 1 --periods 1 \
  --validate-only

./econ-forecast/utils/forecast.sh \
  --config econ-forecast/config/forecasting/forecast.yaml \
  --origin 2026-08-29 --unit month --offset 1 --periods 1 \
  --output econ-forecast/artifacts/september-forecast

# Re-run into the same (existing, non-empty) directory:
./econ-forecast/utils/forecast.sh \
  --config econ-forecast/config/forecasting/forecast.yaml \
  --origin 2026-08-29 --unit month --offset 1 --periods 1 \
  --output econ-forecast/artifacts/september-forecast --force
```

An existing `--output` is refused unless `--force` is given; `--force` deletes and recreates only that resolved directory.

When `--origin` is omitted, the origin is the dataset's declared last day, including a covered zero-consumption day. Explicit `--origin` overrides it for a historical cutoff. The resolved origin is saved in `request.json` and shown in `report.md`. Team and Project scopes in YAML use `name`, resolved within `default_org_id` or an explicit `org_id`; stored internal IDs remain artifact metadata. This example matches the 60-day synthetic dataset starting July 1 and requests September. Weeks use Monday–Sunday; months use their actual dates. `--offset 3 --periods 1` requests only the third future calendar period. Intermediate dates are sampled internally so a sample ID has stable preceding draws, but do not enter the requested period's totals. Horizon inputs belong on the CLI, not in YAML. These wrapper forms are exercised outside the repository working directory by the acceptance suite.

Config-relative input paths resolve against the YAML directory. Output paths resolve against the caller's working directory. `--help` must work without reading a dataset. Validation creates no forecast directory. A completed run emits JSON summary on stdout, structured JSON logs on stderr, and a saved report; incomplete scopes return exit 2. Execution/storage failures return 1; full success, help, and successful validation return 0.

The callable API uses the same request and completed-run contracts:

```python
from datetime import date
from pathlib import Path

from forecast.forecasting import ForecastRequest, load_run, run_forecast

request = ForecastRequest(origin=date(2026, 8, 29), unit="month", offset=1, periods=1)
result = run_forecast(
    Path("econ-forecast/config/forecasting/forecast.yaml"),
    request,
    Path("econ-forecast/artifacts/september-forecast"),
)
saved = load_run(result.output_dir)
entry = saved.index["series"][0]
if entry["status"] in {"qualified", "inactive"}:
    paths = saved.load_paths(entry)  # read-only float64 mmap: date × sample
```

`run_forecast` returns `ForecastResult`; invalid inputs raise `InputError` and operational failures raise `ExecutionError`. A partial analytical run is still a completed readable artifact set.

Mac and Linux use the same staged-data batch workflow. No GPU, managed forecasting service, or database connection is required; path generation works offline after dependencies and data are staged. Measure the revised user-level workload on the intended host before setting instance capacity or runtime guarantees.

## History, startup, and inactivity

- Within declared collection coverage, no usage means zero; a declared outage means unknown consumption. Malformed token records are validation errors, not zero usage.
- `history.minimum_active_days` is a startup gate: X consecutive positive calendar days; the maintained YAML sets 4. Set start to the first day of the first qualifying streak. X=7 requires weekend activity too. This replaces the earlier idea of counting scattered active days.
- Require `history.minimum_days`, with a minimum allowed value of 14; the maintained YAML sets 14 consecutive covered days ending on origin, bounded by that established start, lookback, and the last outage. Streak days count toward those 14 days. They can cross month boundaries.
- Once qualified, later zero/low usage does not restart the startup gate. Established models may phase out, retire in practice, or restart. Preserve real low positive consumption; there is no cutoff that declares small amounts negligible.
- Entirely zero covered history has a valid continued-inactivity outcome. For an established series, an entirely zero recent allocation window also gives currently inactive / zero expected consumption. Do not revive it using old activity or old allocation shares.
- Inactivity is an assumption, not proof that a project is completed or a calibrated 100% budget-confidence statement. Historical activity is separate report context, including the last active date.

## Model: data-driven ETS

```yaml
model:
  method: ets        # statsmodels ETSModel, additive error, one fit per scope/model
  seasonal: auto     # auto = learn from data | none = force off
  trend: auto        # auto = learn from data | none = force off
```

`auto` runs a tiered fallback; the first numerically accepted fit wins:

| Usable days | Tiers tried, in order |
|---|---|
| 14–27 | level-only |
| 28+ | weekly-seasonal (period 7) + damped trend → weekly-seasonal → level-only |

- `seasonal: none` collapses to level-only **even with `trend: auto`** — there is no trend-only tier (known limitation).
- `trend: none` skips the trend tier.
- If every allowed tier fails, the series is `sampling_failed` (no hidden average fallback).
- There is **no forecast-side weekend setting**. A weekly pattern appears only if the data has one and the seasonal tier learns it.

`diagnostics.json` records each series' `fit_attempts` (trend/seasonal/damped, convergence, iterations, warnings, reason).

## Uncertainty: joint residual bootstrap

Each qualified series keeps its one-step fit residuals. Per scope, residuals are aligned by date across the scope's active models. For every future date and path, **one historical date** is drawn and every model gets its residual from that same date; ETS then simulates forward from the final fitted state. This keeps same-day cross-model relationships.

- The draw window is the dates shared by all active models in the scope; it needs **≥ 14 shared error dates**, otherwise the scope's models are `sampling_failed`. Inactive models don't shorten it.
- Draws are independent across dates, so multi-day bursts are underrepresented.

**Nonnegative correction.** Simulated daily values are clipped with `max(0, ·)` (not fed back into the model state). If the clipped share of a series' requested-horizon tokens exceeds `uncertainty.max_negative_correction_ratio` (default 0.05), the series stays `qualified` and keeps its paths but carries the `excess_negative_correction` assumption: treat it as unsuitable for budget risk.

## Known events (declared future spikes)

A one-off spike in *history* washes out of any forecast. A spike you **know is coming** (release, migration) is declared and applied after simulation:

```yaml
known_events:              # optional; every key optional; ≤ 1 event per org/team/project/agent
  org:      {start_date: 2026-09-14, end_date: 2026-09-18, multiplier: 1.4, spread: 0.15}
  teams:    {team1: {start_date: 2026-09-21, end_date: 2026-09-23, multiplier: 1.6, spread: 0.2}}
  projects: {payment_service_improvement: {start_date: 2026-09-24, end_date: 2026-09-25, multiplier: 2.0, spread: 0.0}}
  agents:   {claude: {start_date: 2026-09-14, end_date: 2026-09-18, multiplier: 2.0, spread: 0.3}}
```

| Field | Meaning |
|---|---|
| `multiplier` | Expected size (≥ 0). |
| `spread` | Absolute half-width (`0 ≤ spread ≤ multiplier`). Each path draws one factor `~ Uniform(multiplier ± spread)` for the whole window. |

- **Anti-washout:** one factor per path per window, so the event shows in the mean **and** the band (`spread: 0` shifts the band; `spread > 0` fans it out).
- **Scope precedence** (most specific wins): project → its team → org. **Agent** events apply to that agent's models everywhere and **multiply** with the scope event. Example: org ×1.4 and claude ×2.0 → claude models ×2.8, codex models ×1.4.
- No model-level events. A scope event moves only its own series — a project event does not yet roll up into its team/org totals (deferred).
- Zero (inactive) paths stay zero. Applied events are recorded in `diagnostics.json` → `known_events` (level, name, dates, multiplier, spread, factor min/mean/max) and add the `known_event_applied` assumption.

Worked example with committed artifacts: [`examples/known-event-spike`](../../../examples/known-event-spike).

## Statuses

| Series | Meaning |
|---|---|
| `qualified` | Fitted; paths saved (may carry `excess_negative_correction`). |
| `inactive` | Valid all-zero history or recent window; zero paths. |
| `insufficient_history` | Covered-history floor not met. |
| `insufficient_startup` | No qualifying startup streak. |
| `sampling_failed` | All ETS tiers failed, nonfinite output, or < 14 shared error dates. |

| Scope | Meaning |
|---|---|
| `complete` | All models qualified. |
| `conditional` | Qualified + inactive. |
| `inactive` | All inactive. |
| `incomplete` | A nonzero component has no usable path → partial run, CLI exit 2. |

## Output directory

One new directory represents one run:

```text
forecast-2026-10/
├── report.md                 # Default human-readable results and assumptions
├── charts/                   # Only if `plot_forecast` has drawn charts here (not written by the run itself)
├── manifest.json             # Versions, checksums, completion and scope statuses
├── resolved_config.yaml      # Settings actually used (input.dataset_dir relative to this run)
├── request.json              # Origin, requested scopes, calendar window, dataset link (relative to this run)
├── daily_expected.jsonl      # Daily averages split into token categories
├── period_summary.json       # Requested-period means and token percentiles
├── allocations.json          # Joint agent/category shares and their evidence
├── diagnostics.json          # Eligibility, ETS fit attempts, residual window, corrections, known events, resources
└── paths/
    ├── index.json
    ├── organization-talium-ai__gpt-5-6-sol__684d2cc7c4799f03.npy
    ├── team-team1__claude-sonnet-5__ad1a7a8455b47dc1.npy
    └── team-team1__gpt-5-6-sol__725634798d132b21.npy
```

Names explain the scope and model. Internal IDs disambiguate stored identities. `paths/index.json` is authoritative for the exact Organization, scope type/ID, model, dates, sample columns, and associated file; do not parse identity from display names. Existing run directories are not overwritten unless `--force` is given. The manifest is published after required artifacts are written and identifies partial results explicitly.

The dataset link (`resolved_config.yaml` `input.dataset_dir`, `request.json` `dataset.path`) is recorded relative to the run directory, so a run moved together with its dataset (same relative position, e.g. a [pipeline workspace](../pipeline/README.md)) still finds it; `load_run` resolves it against the run root, and an older run's absolute path still resolves to itself. The run writes no charts; `plot_forecast` draws them into `charts/` ([utils README](../utils/README.md)).

Paths are float64 numeric NumPy arrays. They can be read in slices using memory mapping. Save total-token paths plus shares, rather than duplicating every category's paths. For 100 series × 90 days × 1,000 samples, numerical values occupy 72,000,000 bytes (~68.7 MiB); process memory and other output files are additional.

## What does a sample mean?

**A sample is one simulated possible future**, also called a path. It is not a historical observation, session, user, or alternative planner-selected model mix.

For one scope/model file, imagine only three samples:

| Forecast date | Sample 0 | Sample 1 | Sample 2 |
|---|---:|---:|---:|
| October 1 | 100,000 | 140,000 | 80,000 |
| October 2 | 110,000 | 90,000 | 95,000 |
| October 3 | 90,000 | 130,000 | 85,000 |
| Three-day total, calculated from the array | 300,000 | 360,000 | 260,000 |

Each **row** in the file is a future date, each **column** a complete possible future, and each **cell** is total tokens on that day for that scope/model in that future. The displayed total row is an explanation, not an extra date stored in the array. October 2 / Sample 1 means 90,000 tokens in possible future 1. Forecast statistical quantities may be fractional even though historical token counters are integers.

With the maintained YAML's 1,000 samples there are 1,000 equally weighted columns. Row averages produce expected daily usage. Column sums produce possible period totals. The distribution of those totals provides period percentiles. Keep each column together across dates and models within the same scope. Combining a day or model from sample 1 with another from sample 42 destroys that shared path identity.

Different futures come from the ETS simulation driven by the joint residual bootstrap (above): each path re-uses historical one-step errors, the same drawn date across all active models in the scope, then applies any declared known event. More samples reduce Monte Carlo noise; they do not create more historical evidence or establish calibration.

The residual window is the dates shared by every active model in the scope. For example, if one model has 90 usable days and another has 21, only errors from their common 21-day window are drawn (each model is still fit on its own full history). Reports show each model's history window and the shared residual window.

## Why split total tokens into categories?

Paths contain one total-token quantity per scope/model. Pricing requires input, cache-write (5m, 1h), cache-read, and output quantities because their billing rates can differ. We therefore use recent joint agent/category proportions to allocate predicted totals. This fixed-share approximation does not model future category or agent mix changes.

Use `allocation.lookback_days`, set to 28 in the maintained YAML, intersected with usable fitting history. Sum token quantities per agent/category, then divide by their overall total. Do not average daily percentages: a tiny day must not influence the proportions as much as a heavy day. Hold shares fixed across dates and samples.

For recent totals of 100,000 input, 800,000 cache-read, and 100,000 output tokens (no cache writes), the shares are 10%, 80%, and 10%; a 500,000-total-token path day becomes 50,000 input, 400,000 cache-read, and 50,000 output. With cache writes (600 input, 5,982 5m, 193,418 1h, 800,000 cache-read, 30,000 output) the five shares are 0.000583, 0.005808, 0.187784, 0.776699, and 0.029126. One contributing agent has a 100% agent share. Multiple agents retain their individual joint category proportions.

This does not forecast changes in cache efficiency, input/output ratio, or agent mix. Scenarios may change those assumptions. Model mix itself comes from the separately forecast model totals. When an established series has no recent consumption, report continued inactivity and zero expectation without falling back to older shares. Small positive recent usage supplies its own proportions.

## The three summary and allocation files

These JSON examples illustrate the content in readable form. The frozen release contract defines the authoritative versioned schemas and statuses; JSONL stores each record on one physical line.

### `daily_expected.jsonl`: what do we expect each day?

Each row is a date/scope/model/agent contribution. It averages saved paths and applies category shares. This supports daily consumption charts and model comparisons; averages alone cannot answer overrun probabilities.

```json
{
  "schema_version": 1,
  "date": "2026-09-01",
  "org_id": "talium.ai",
  "scope_type": "team",
  "scope_id": "payments-team",
  "model": "gpt-5.6-sol",
  "agent_type": "codex",
  "tokens": {
    "input": 640.8,
    "cache_write_5m": 6385.5,
    "cache_write_1h": 206484.3,
    "cache_read": 852106.9,
    "output": 32028.8
  },
  "total_tokens": 1097646.3,
  "status": "qualified"
}
```

### `period_summary.json`: what does the whole period look like?

It contains expected totals and supported percentiles for the requested period. First sum daily model quantities within each sample, then compute percentiles across sample totals. Do not add daily or per-model p90 values. Incomplete scopes cannot receive an unlabeled partial total or complete-scope percentile.

```json
{
  "schema_version": 1,
  "entries": [{
    "org_id": "talium.ai",
    "scope_type": "team",
    "scope_id": "payments-team",
    "model": "gpt-5.6-sol",
    "start": "2026-09-01",
    "end": "2026-09-30",
    "period_type": "requested",
    "status": "qualified",
    "expected_tokens": 26053131.356144723,
    "p10": 24005713.92490661,
    "p50": 25944309.382883526,
    "p90": 28387890.778596934,
    "assumptions": [
      "uncalibrated_model_estimate",
      "fixed_allocation_shares",
      "common_residual_window"
    ]
  }]
}
```

In this illustrative excerpt, p90 describes the 90th percentile of sampled period consumption: approximately 90% of samples are at or below about 28.39 million tokens. These are token quantities, not dollar budgets. The implementation uses NumPy's linear percentile interpolation. A scope aggregate uses the same entry shape with `model: null`; it is emitted only for a complete, conditional, or inactive scope.

### `allocations.json`: how do totals become billable quantities?

It records joint agent/category shares, actual estimation dates, historical token totals supporting the shares, and the fixed-share limitation. It is an assumption record, not another forecast. Budget uses it to price paths; scenarios can change it.

```json
{
  "schema_version": 1,
  "entries": [{
    "series_id": "725634798d132b21ad2b83ccbc6d27f048fe8d40a46fa825bd09c21a620e5625",
    "status": "qualified",
    "window_start": "2026-08-02",
    "window_end": "2026-08-29",
    "historical_total": 21099661.0,
    "shares": [{
      "agent_type": "codex",
      "input": 0.000583,
      "cache_write_5m": 0.005808,
      "cache_write_1h": 0.187784,
      "cache_read": 0.776699,
      "output": 0.029126
    }],
    "historical_tokens": [{
      "agent_type": "codex",
      "input": 12303.0,
      "cache_write_5m": 122546.0,
      "cache_write_1h": 3962183.0,
      "cache_read": 16388025.0,
      "output": 614604.0
    }],
    "assumptions": [
      "uncalibrated_model_estimate",
      "fixed_allocation_shares",
      "common_residual_window"
    ]
  }]
}
```

Join an allocation entry to `paths/index.json` through `series_id`; scope/model identity is authoritative in the index. Sum shares across all agent/category pairs to get one. An inactive entry has status `inactive`, zero historical total, empty shares/historical tokens, and the `continued_inactivity` assumption. Failed entries do not fabricate shares.

## Can a $40,000 Team budget last, and when might it run out?

The saved token paths support this question through the **budget module**, with supplied pricing and allocation assumptions. Assume the full $40,000 is available at the requested period's start. The raw token array by itself cannot answer a dollar question.

```text
Team's model paths + saved agent/category shares + applicable prices
                              │
                              ▼
                Daily dollar cost in each sample
                              │
                              ▼
          Cumulative cost and first budget-crossing date
                              │
                              ▼
       Budget confidence, overrun risk, conditional timing
```

For every sample:

1. Allocate total tokens into agent/category quantities and apply applicable prices. Each of the five token types is priced at its own rate.
2. Sum daily costs across the Team's models, retaining the same sample ID.
3. Accumulate costs over the requested period. A sample stays within budget when its period total is **at most $40,000**; it overruns when the total is **greater than $40,000**.
4. For an overrunning sample, identify the first date cumulative cost exceeds $40,000. Daily data provides a date, not an exact exhaustion time.

If 600 of 1,000 priced paths stay within budget, estimated budget confidence is 60% and overrun risk is 40%, conditional on the recorded assumptions. Confidence here is the model-estimated probability of budget sufficiency, not a separate statistical confidence interval for that estimate.

Report an **expected exhaustion/crossing date and percentile range conditional on an overrun occurring**. Calculate the mean crossing-day offset using only the 400 crossing samples in that example, then present it as a date under the budget/report's eventual rounding convention. Do not assign a date to the 600 noncrossing samples. If none crosses, no conditional crossing-date estimate exists. Zero observed crossings in a finite simulation do not prove that a real overrun is impossible.

Check scope readiness first:

| Models contributing to the Team | Interpretation |
|---|---|
| All qualified | Budget calculation can use their paths under recorded assumptions. |
| Qualified plus inactive | Include zero for inactive models and explicitly condition results on continued inactivity. |
| Any nonzero insufficient or sampling-failed component | Complete Team risk is unavailable; show the missing component and useful individual results. |
| All inactive | Zero expected usage under continued inactivity; not empirically established 100% budget confidence. |

Budget/scenario calculations and report presentation reuse the saved paths. Changing prices or a scenario mix does not require generating a new consumption forecast. Neither a budget command nor an interactive question API is specified by this walkthrough.

## Limitations visible in the report

| Limitation | Where it shows |
|---|---|
| Uncalibrated quantiles (`uncalibrated_model_estimate`) | Every qualified entry. Synthetic tests establish behavior, not real accuracy. |
| Fixed category/agent shares (`fixed_allocation_shares`) | Allocation; scenarios may change them. |
| Shared residual window (`common_residual_window`) | Errors only from dates common to the scope's active models; independent across days. |
| Nonnegative clipping (`excess_negative_correction`) | Only when clipped share > `max_negative_correction_ratio`. |
| Declared events (`known_event_applied`) | Operator judgment; no parent roll-up yet. |
| No trend-only tier | `seasonal: none` + `trend: auto` fits level-only. |

Assumption labels: `uncalibrated_model_estimate`, `fixed_allocation_shares`, `common_residual_window`, `known_event_applied`, `excess_negative_correction`, `continued_inactivity`.
