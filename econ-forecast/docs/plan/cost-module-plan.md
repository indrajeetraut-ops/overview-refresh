# Cost module (`forecast.cost`) — implementation plan

## Context
Budget, evaluation, and reporting need forecast token paths priced in USD, and planners need named
scenarios (model transfer, agent transfer, growth). The design is agreed:
`docs/design/cost-module-design.md` (cited as §N). This plan covers the cost module and the two upstream
changes it needs (`common.model_pricing` rate sheet, forecasting reader/manifest). Budget is a separate
design and plan.

**Process:** start only after explicit approval of this plan; then run the `talium-impl-workflow`
skill (sonnet subagent, handshake, worktree, tests + persisted report, Major/Minor review).

## Deliverables
- `econ-forecast/src/forecast/cost/` package (§10) with module `README.md`.
- `econ-forecast/config/scenarios/` example scenarios.
- `econ-forecast/utils/cost.sh` → `python -m forecast.cost.cli_cost`.
- `econ-forecast/tests/cost/`.
- `common.model_pricing.rate_sheet` API + tests + design doc update.
- Forecasting: dataset identity in the run; reader tolerant of unknown fields.
- Architecture doc updated (§11).
- One GitHub issue for the allocation known error (T0). Tracking issue: #74.

## Conventions (repo)
- Python 3.11; deps already in `requirements.txt`: numpy, pydantic, PyYAML, structlog, matplotlib,
  plotly. No new deps.
- Structured JSON logging via structlog, configured in the CLI, as in forecasting.
- CLI entry `cli_cost.py`; wrapper follows `utils/forecast.sh`.
- Arithmetic float64; rate-sheet decimal strings converted once.
- Identity by name `(scope_type, scope_name, model)`; `org_id` once per run (§8.2).
- Every output field has a consumer (§8.1); schema ids `<name>/<major>.<minor>` in the manifest (§8.1).
- Deferred work in code gets `TODO(<issue or §>)`.
- Commits reference #74.

## Tasks

Dependencies in brackets. T1 and T2 can run in parallel with T3.

### T0 — Raise issue (needs approval: outward-facing)
- Cost module tracking issue already exists: **#74**.
- **Forecasting allocation vs cost mix** — raised as #76; known error (§11): forecasting's 28-day plain-sum split
  differs from cost's exponentially weighted, parent-blended mix. Fix: forecasting adopts the §5
  estimator. Not fixed in this plan.

### T1 — `common.model_pricing.rate_sheet` [—]
Files: `src/common/model_pricing/catalog.py` (or a new `rate_sheet.py`), `docs/modules/model-pricing.md`,
`src/common/model_pricing/README.md`.
- `rate_sheet(org_id, model_ids, start, end) -> RateSheet` (`rate-sheet/1.0`, §4), built on
  `load_catalog()` (latest) → `for_org` → `resolve` → `rates` per date, collapsed into segments.
- Segments cover `start…end`, inclusive, no gaps/overlaps; adjacent equal rates merge; retired → all `null`.
- Non-`matched` models carry `status`, `reason`, `candidates`; no segments.

Accept: price change mid-range → two segments; retired mid-range → `null` segment; ambiguous and
unmatched entries; JSON round-trip matches §4.

### T2 — Forecasting: run facts for cost, tolerant reader [—]
Files: `forecasting/runner.py`, `forecasting/artifacts.py`, forecasting design §10.
- Record the dataset manifest sha256 in the run.
- `SavedRun` exposes `origin`, `periods` (requested + calendar), `dataset` (path + sha256),
  `history_lookback_days` (cost design §3.1).
- `load_run` ignores unknown fields (additive changes don't break readers).
- `allocations.json` and the category split stay as is (known error, T0 issue).

Accept: existing forecasting tests green; new run records dataset hash; `SavedRun` returns the four facts;
reader accepts an index with an extra field.

### T3 — Package skeleton, errors, schemas, constants [—]
Files: `__init__.py`, `errors.py`, `schemas.py`.
- `InputError`, `ExecutionError` as in forecasting.
- Pydantic models for every file in §8.2 and the scenario config (§6.1).
- Method constants (§3.2).

Accept: example JSON in §8 validates; unknown fields ignored by readers.

### T4 — Request: forecast run, dataset, scenario [T2, T3]
File: `request.py`.
- Open the forecast via `load_run`; derive org, dates, samples, periods, series, statuses.
- Verify the dataset against the forecast's recorded hash.
- Load + validate scenario (§6.4): name = file name, `from` in scope, `0 < fraction ≤ 1`,
  `effective_from` in range. `to` resolution checked after T6.

Accept: each §6.4 rule has a failing case → `InputError`.

### T5 — Mix and attribution [T3]
File: `mix.py`.
- Exponentially weighted token ratio (§5), parent blend (§5), attribution share (§5).
- Window, outage exclusion, `active_days` undecayed.

Accept: §5 example (4 days, 10% / 3% → 5.5%); ratio of sums, not mean of ratios; outage day excluded;
org has no parent.

### T6 — Rates [T1, T3, T5]
File: `rates.py`.
- Rate sheet for the run's models → per-series `rate[date]` array (§2).
- Status `unpriced` when not matched or a needed rate is `null` (§7).

Accept: §2 example ($1.281 / M); null `output` rate with nonzero output share → `unpriced`; null rate
with zero share → priced.

### T7 — Kernel and summaries [T6]
Files: `kernel.py`, `summaries.py`.
- Per-series `tokens × rate`; scope accumulation per sample; memory bounded by one scope (§2).
- `daily_expected` rows (USD + tokens), `period_summary` (sum per sample, then quantile).
- Scope status rules (§7); no scope total for `incomplete`.

Accept: §2 example day → $1.41; scope p90 ≠ Σ model p90; inactive → zeros; incomplete → no scope entry.

### T8 — Scenarios [T5, T7]
File: `scenario.py`.
- `model_transfer`, `agent_transfer`, `growth` with `effective_from` (§6.2).
- Destination mix; `scenario_mix_borrowed`; `scenario_added_series`.
- Apply each change to sub-scopes (k = 1) and parent scopes (k = attribution share) (§6.3).

Accept: §6.3 example (40% × 30% → 12% at org); siblings unchanged; `token_factor` applied; tokens
conserved when `token_factor = 1.0`; baseline scope set = scenario scope set.

### T9 — Artifacts and reader [T3, T7]
File: `artifacts.py`.
- Atomic writes; `manifest.json` last with schema id, bytes, sha256 per file.
- `load_cost_run`: checks schema major, integrity, array shape/dtype/finiteness; identity from index only.
- File names: slug of scope and model, `-2` on collision.

Accept: tampered file rejected; unknown minor accepted; unknown major rejected; slug collision handled.

### T10 — Runner, CLI, wrapper [T4–T9]
Files: `runner.py`, `cli_cost.py`, `utils/cost.sh`.
- `run_cost`, `CostRequest(scenario=...)`.
- Flags and behaviour per §3.2; `--validate-only` writes nothing and lists unpriced models.
- Exit codes 0 / 2 / 1; JSON summary on stdout; logs on stderr.

Accept: exit code per case; existing output refused without `--force`; `--force` deletes only the target.

### T11 — Presentation [T10]
File: `presentation.py`.
- `report.md` in §8.3 order; per-scope daily cost fan and model/agent breakdown, PNG + offline HTML.
- Report notes, including the known-error split note.

Accept: report renders for baseline and scenario runs; HTML has no network references.

### T12 — README, examples, docs [T10, T11]
- `src/forecast/cost/README.md`: purpose, CLI examples (baseline, scenario, validate-only), output
  directory tour, what a USD sample means, reading with `load_cost_run`, statuses, scenarios with
  scope-level example (§6.3), assumptions, known error.
- `config/scenarios/`: one example per change type.
- Architecture doc (§11): `cost` replaces pricing in `budget` and the `scenarios` module; add
  `common.model_pricing`, `curve`.

## Tests (`econ-forecast/tests/cost/`)
| File | Covers |
|---|---|
| `test_schemas.py` | T3 examples validate; versioning rules |
| `test_request.py` | T4 validation cases |
| `test_mix.py` | T5 estimator, blend, attribution |
| `test_rates.py` | T6 blended rate, `unpriced` |
| `test_kernel.py` | T7 kernel, summaries, statuses |
| `test_scenario.py` | T8 changes, scope levels |
| `test_artifacts.py` | T9 write/read, integrity |
| `test_cli.py` | T10 exit codes, `--validate-only`, `--force` |
| `test_end_to_end.py` | Synthetic dataset → forecast → cost baseline + scenario |

Plus `tests/common/model_pricing/test_rate_sheet.py` (T1) and forecasting reader/identity tests (T2).
No network. Test report persisted under `docs/test/reports/` (`cost-junit.xml`, `cost-test-report.md`).

## Out of scope
- Budget module (separate design and plan).
- Seat / Team plan pricing (separate design).
- Fixing forecasting's allocation split (T0 issue).
- Estimating `token_factor` from data; channel override lever; pricing observed history; config file for
  mix constants (§13).

## Verification (end to end)
1. `pytest econ-forecast/tests/cost tests/common/model_pricing econ-forecast/tests/forecasting -q` green.
2. Baseline on `examples/plots/curve-guided/forecast` → exit 0; `load_cost_run` reads it; expected USD per
   scope on stdout.
3. Scenario `team1-opus-to-sonnet` on the same forecast → same scope set; team1 and its projects shift;
   org shifts by attribution share; siblings unchanged.
4. `--validate-only` with an unknown model id → lists it as unpriced, writes nothing.
