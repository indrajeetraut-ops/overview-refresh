# Pricing a forecast into USD, and scenarios

Read the [cost module design](../../../docs/design/cost-module-design.md) (the single
authoritative design, cited below as `§N`) and the [model pricing module](../../../../docs/modules/model-pricing.md)
it reads prices from. This module prices a completed [forecasting](../forecasting/README.md)
run's saved token paths into USD, and applies named scenarios (model transfer, agent
transfer, growth) to those token paths before pricing the result the same way as the
baseline. Budget, evaluation, and reporting read this module's saved output through
`load_cost_run`; none of them price anything themselves.

## Token types and prices

A series' tokens are priced as five types, each at its own rate-sheet rate: `input` (uncached
input only), `cache_write_5m`, `cache_write_1h`, `cache_read`, and `output`
([cache-write contract](../../../docs/design/cache-write-contract-design.md)). A `null` rate for
a type with nonzero tokens makes the series `unpriced`, never zero; the catalog bills a missing
1-hour write at the 5-minute rate when a vendor publishes only that one. `mix.json`,
`daily_expected.jsonl` and `period_summary.json` are schema `/2.0`; a forecast run at manifest
schema 1 is rejected (rerun the forecast).

## What this module produces

One cost run mirrors one forecast run: the same scopes, dates, and samples, in USD
instead of tokens. A cost run without `--scenario` is the baseline; each named scenario
is its own separate cost run, comparable side by side with the baseline and with each
other. `forecast.cost` never refits a consumption forecast — it only prices (and,
for a scenario, redistributes) the forecast's own saved paths.

```text
forecast run (token paths)  +  daily dataset  +  model_pricing rate sheet  +  optional scenario.yaml
                                        │
                                        ▼
                              cost run (USD paths, summaries, report.md)
```

This run does not draw charts itself: `forecast.utils`'s `plot_cost` draws them
separately, into the run's own `charts/`, from `plot.yaml` (see `forecast.utils`'s
README). `report.md`'s Charts section links whatever `plot_cost` has drawn (reading
`charts/index.json`) and is omitted entirely for a run that has never been plotted.

`request.json` records the forecast run as a path relative to this cost run (the dataset
is reached through the forecast run's own link), so a
[pipeline workspace](../pipeline/README.md) — forecast run, cost runs and `dataset/`
moved together — keeps working. The pipeline runs the baseline and one cost run per
`cost/scenarios/<name>.yaml`, then plots them.

## CLI and shell workflow

The companion `econ-forecast/utils/cost.sh` calls `python -m forecast.cost.cli_cost`.
Python callers use `run_cost(...)`; consumers open a completed cost run with
`load_cost_run(...)`.

```bash
# Validate inputs and the rate sheet only; writes nothing.
./econ-forecast/utils/cost.sh --forecast-run artifacts/sep-forecast --validate-only

# Baseline: price the forecast run's own token paths as saved.
./econ-forecast/utils/cost.sh --forecast-run artifacts/sep-forecast \
  --output artifacts/sep-forecast-cost-baseline

# A named scenario -- see config/scenarios/ for one example per change type.
./econ-forecast/utils/cost.sh --forecast-run artifacts/sep-forecast \
  --scenario config/scenarios/team1-opus-to-sonnet.yaml \
  --output artifacts/sep-forecast-cost-team1-opus-to-sonnet

# Re-run into the same (existing) directory:
./econ-forecast/utils/cost.sh --forecast-run artifacts/sep-forecast \
  --output artifacts/sep-forecast-cost-baseline --force
```

An existing `--output` is refused unless `--force` is given; `--force` deletes and
recreates only that resolved directory (never the forecast run or the dataset it
reads, even if `--output` is pointed at one by mistake). `--validate-only` reads the
forecast run, the dataset, and (if given) the scenario, builds the rate sheet, and
lists any unpriced (unmatched/ambiguous) models on stdout — it writes nothing. A
completed run emits a JSON summary (scenario, per-scope status, expected USD per
period) on stdout and structured JSON logs on stderr; any scope `incomplete` (also
`--validate-only` with unpriced models) returns exit 2, input/execution failures
return 1, full success returns 0.

The callable API uses the same request and completed-run contracts:

```python
from pathlib import Path
from forecast.cost import CostRequest, load_cost_run
from forecast.cost.runner import run_cost

result = run_cost(
    Path("artifacts/sep-forecast"),
    CostRequest(scenario=Path("config/scenarios/team1-opus-to-sonnet.yaml")),  # or None
    Path("artifacts/sep-forecast-cost-team1-opus-to-sonnet"),
)
run = load_cost_run(result.output_dir)
run.request, run.index, run.rate_sheet, run.mix, run.period_summary  # parsed eagerly
run.daily_expected                                                    # parsed lazily
entry = run.index["series"][0]
if entry["status"] in ("qualified", "inactive"):
    usd = run.paths(entry)          # read-only float64 mmap: date x sample, USD
scope_total = run.scope_paths(run.index["scopes"][0])  # None if that scope is incomplete
```

`CostRequest` also accepts a `catalog:` (a `common.model_pricing.Catalog`), Python-API
only — there is no `--catalog` CLI flag. Passing one pins the rate sheet to that
catalog version instead of reading the live one at call time; tests use this to price
against a small fixture catalog. `run_cost` returns `CostResult`; invalid inputs raise
`InputError` and operational failures raise `ExecutionError`.

## Statuses

Every forecast series status is carried through unchanged except `qualified`, which
splits in two once pricing is attempted (§7):

| Cost series | Meaning |
|---|---|
| `qualified` | Matched in the rate sheet, and every token type with nonzero mix has a non-null rate on every forecast date. Has a USD path. |
| `unpriced` | Forecast-qualified, but its model is not `matched`, or a rate is missing for a token type its mix actually uses. No USD path. |
| `inactive` | Forecast's own all-zero path, reused as-is. |
| `insufficient_history` / `insufficient_startup` / `sampling_failed` | Unchanged from the forecast; no path. |

Scope statuses are the same four as forecasting (`complete`, `conditional`,
`inactive`, `incomplete`), computed from the *cost* series statuses above —
`unpriced` forces `incomplete` exactly like a missing forecast path does. An
`incomplete` scope never gets a USD total (`run.scope_paths(...)` returns `None`,
and there is no scope entry in `period_summary.json`).

## What does a USD sample mean?

Exactly what a forecast sample means (see the [forecasting README](../forecasting/README.md#what-does-a-sample-mean)),
just priced: **one simulated possible future**, not a historical observation or an
alternative planner-selected mix. `cost[date, sample] = tokens[date, sample] x
rate[date]`, where `rate[date]` is that series' mix (§5) blended against the rate
sheet's per-token-type price on that date (§2.2, §4) — never 0 for a token type
the series has no price for; that series is `unpriced` instead. Percentiles are
computed by summing each *sample's* cost over the period first, then taking the
percentile across samples — never by adding two series' own p90s (a scope's p90 is
not the sum of its models' p90s: the models' high and low days do not all land on
the same sample).

## Mix: how a series' rate is built

A series prices at a blend of its own recent history and its Organization's (§5):
each (scope, model) series' recent token-type/agent split is a ratio of
exponentially-decayed weighted sums (never a mean of daily ratios, `half_life_days
= 14`); a Team or Project blends that toward the Organization's own split for the
same model, weighted by `own_weight = active_days / (active_days +
own_mix_half_weight_days)` (`own_mix_half_weight_days = 7`) — the Organization
always uses its own, unblended. `mix.json` records this per series (`active_days`,
`own_weight`, the shares themselves, and each parent scope's attribution share);
`own_weight < 0.9` is thin evidence, called out in `report.md`.

## Scenarios

A scenario (`config/scenarios/*.yaml`, `scenario/1.0`, §6.1) declares one or more
changes — `model_transfer`, `agent_transfer`, `growth` — on one declared scope. Each
change applies, in order (every change acts on the previous one's result), to:

- the **declared scope** itself, at full weight (`k = 1`);
- that scope's **sub-scopes** (a Team's own Projects; an Organization's every Team
  and Project), each at `k = 1` too — every level under the declared scope moves the
  same fraction of *its own* tokens;
- the declared scope's **parent scopes** (a Team's Organization; a Project's Team and
  Organization), each at `k` = the declared scope's **attribution share** of that
  parent's usage for the affected model (§5, §6.3) — a fraction of the parent's own
  tokens, not the same fraction the declared scope moved.

Worked example (design's own, §6.3) — `config/scenarios/team1-opus-to-sonnet.yaml`
moves 30% of team1's Opus to Sonnet, and team1 is 40% of the Organization's Opus:

| Scope | Effect | `k` |
|---|---|---|
| team1 (declared) | moves 30% of its own Opus | 1 |
| team1's Projects (sub-scopes) | each moves 30% of its own Opus | 1 |
| Organization (parent) | moves 0.4 x 30% = **12%** of its own Opus | attribution share `s = 0.4` |
| team2, team3, and their Projects | unchanged | — |

Baseline and scenario runs always share the same *scope* set (§6.3) — a scenario can
add a new *series* within an existing scope (a destination model that scope didn't
have before, `scenario_added_series`) but never a new scope. `token_factor` (default
`1.0`) is tokens consumed at the destination per token moved; `1.0` conserves the
total tokens moved out of the source plus into the destination for that scope/model
pair (`scenario_constant_tokens`); anything else scales it
(`scenario_token_factor_manual`). `config/scenarios/` has one example per change type.

## Output directory

```text
sep-forecast-cost-baseline/
├── manifest.json          # written last; per-file schema id, bytes, sha256 (report.md/charts/ NOT listed)
├── request.json           # forecast_run ref (path relative to this run), scenario name, periods, settings
├── scenario.yaml           # copy of the scenario config; scenario runs only
├── rate_sheet.json         # exact rate-sheet/1.0 shape model_pricing returned; unchanged
├── mix.json                # shares, active_days, own_weight, attribution, per series
├── daily_expected.jsonl    # date/scope/model/agent -> USD + tokens (mean over samples)
├── period_summary.json     # expected/p10/p50/p90 USD, per series and per scope, per period
├── report.md                # not in the manifest; freely re-rendered by plot_cost
├── charts/                  # only if plot_cost has drawn here; not written by this run, not in the manifest
└── paths/
    ├── index.json          # AUTHORITATIVE identity (scope_type, scope_name, model), dates, statuses
    └── <scope>__<model>.npy   # float64 [dates, samples], USD; zero for inactive
```

Identity is by name (`scope_type`, `scope_name`, `model`) — there is no `scope_id` in
a cost run's own artifacts, unlike the forecast run it was priced from (§3.2, §8.1).
`paths/index.json` is authoritative; never parse identity from a `.npy` filename
(collisions get a `-2` suffix, purely cosmetic). Schema ids are
`<name>/<major>.<minor>`; a reader accepts an unknown minor and rejects an unknown
major.

## Known error: forecasting's allocation vs. this module's mix

Forecasting's own `allocations.json` (used only for its own token-category display)
computes an agent/category split as a **plain 28-day sum** — a different, simpler
estimator than this module's exponentially-weighted, parent-blended mix (§5). The two
will disagree, sometimes by a lot for a thin or fast-moving series. This is a known,
tracked error (design §11; forecasting should eventually adopt §5's estimator), not a
bug in either module — `report.md` notes it. Do not compare a forecast run's
allocation shares against a cost run's `mix.json` and expect them to match.

## Assumption labels

| Label | Attached to |
|---|---|
| `uncalibrated_model_estimate`, `excess_negative_correction`, `known_event_applied`, … | Carried from the forecast series (`fixed_allocation_shares` is dropped — replaced, see below). |
| `fixed_mix_estimate` | Every priced (`qualified` or `unpriced`) series; replaces the forecast's `fixed_allocation_shares`. |
| `continued_inactivity` | Every `inactive` series. |
| `scenario_constant_tokens` / `scenario_token_factor_manual` | A series changed by a transfer, by `token_factor = 1.0` / anything else. |
| `scenario_mix_borrowed` | A destination series with no weighted usage anywhere (own scope or Organization) for its model — priced with another model's mix in the same scope instead of a $0 rate. |
| `scenario_added_series` | A destination series that did not exist in that scope before the scenario. |
| `scenario_propagated_by_share` | A series changed only because its scope is a parent of the declared scope (`k` = attribution share, not `1`). |
