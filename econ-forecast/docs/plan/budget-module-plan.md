# Budget module (V1, baseline scenario): implementation plan

## Context
Forecasting saves daily token paths (date × sample) per scope/model. Nothing turns them into
dollars yet. The budget module prices those saved futures and answers: expected cost and p10/p50/p90
per Organization / Team / Project, model, agent, and token type; probability a budget is enough;
budget required for a target confidence; and when a budget runs out. V1 runs the baseline scenario
only; scenarios, mid-period re-forecast, and #67/#68 are deferred.

Authoritative design: `econ-forecast/docs/design/budget-module-design.md` (sections cited as §N).
**Process:** implement only after the user explicitly says yes; then follow the
`talium-impl-workflow` skill (sonnet subagent, handshake, worktree, tests + persisted report,
Major/Minor review).

## Dependency: `common.model_pricing`
Budget needs the price-catalog interface in design §12. `common.model_pricing` is designed in
`docs/modules/model-pricing.md`; its implementation gets its own plan. This plan is ordered so Phases 1–4 do not need it: they use
a `PriceCatalog` protocol (§12 signatures) and an in-test fake catalog. Phase 5 wires the real
catalog and cannot start until `common.model_pricing` is implemented.

## Decisions (locked by the design)
- Price every sample, then summarize; percentiles only from per-sample totals (§4, §6).
- Scopes priced from their own paths; never summed across scopes (§3).
- Deterministic agent × token-type shares: exponentially weighted token-sum ratio
  (`half_life_days: 14`), parent blending with `own_mix_half_weight_days: 7`, window
  `max_age_days` (default = forecast lookback), dates ≤ origin, outages excluded (§5).
- Cache writes are observed token types (`cache_write_5m`, `cache_write_1h`) after #67; no cache-write configuration.
- Budgets per scope (org/team/project), per requested calendar period, no carry-over,
  `spent_to_date` fixed at 0 (§6.1, §8.2).
- Output: `report.md`, `budget.json`, `ratios.json`, charts as PNG + offline HTML with one CSV per
  chart, per-scope cost paths; `scenario: {"id": "baseline"}` reserved (§10).
- Float64 arithmetic; structured JSON logging via `common.msging.log`.

## Phase 0 — Forecasting: record dataset identity (§14)
- `forecasting/runner.py`: write the loaded dataset's file hashes (`PreparedDataset.hashes`) and
  dataset manifest identity into the run's `request.json` (or `manifest.json`), alongside the existing
  `input.dataset_dir`.
- `forecasting/artifacts.py::load_run`: expose the recorded identity; older runs without it load
  unchanged (field absent).
- Tests: `tests/forecasting/test_runner_integration.py` asserts the identity is recorded and matches.
- Does **not** remove forecasting's `allocation` (design §16 **[open]**).

## Phase 1 — Core numerics (pure functions, no I/O)
New `src/forecast/budget/`:
- `ratios.py` — `estimate_shares(usage, scope, model, origin, coverage, settings, parent=None)`
  returning own shares, parent shares, `own_weight`, `active_days`, window, parent-only agents.
  Reads the dataset's `usage` keyed `(org_id, scope_type, scope_id, model, date, agent_type,
  token_type)` (`forecasting/dataset.py`). Organization shares computed unblended and used as parent.
- `rates.py` — `blended_rate(shares, catalog, model, dates)` →
  `$ per token` per date, or an `unpriced` result naming the missing token type.
- `kernel.py` — per-model and per-scope daily cost arrays `[dates, samples]` from path × rate;
  agent and token-type breakdowns from shares.
- `metrics.py` — expected, configured quantiles (linear), `p_sufficient`, `p_overrun`,
  `required_for_confidence`, crossing date per sample, `p_exhausted_by[date]`, `given_crossing`
  (`inverted_cdf` median/p10/p90 + mean offset).
- `errors.py` — `InputError` / `ExecutionError` mirroring forecasting.
- `schemas.py` — `PriceCatalog` protocol (§12) plus result dataclasses.

## Phase 2 — Inputs and orchestration
- `config.py` — method config model (§8.1) with defaults; resolved config dump.
- `request.py` — `BudgetRequest`; load forecast run via `forecasting.load_run`; select scopes;
  requested calendar periods from the run's `request.json` (exclude internal dates); budgets file
  (§8.2) with scope name resolution matching forecasting; dataset reload via
  `forecasting.dataset.load_dataset` and identity check against Phase 0 (hard error on mismatch;
  warning + assumption when the forecast run predates Phase 0).
- `runner.py` — `run_budget(forecast_run_dir, request, output_dir)`: ratios → rates → kernel →
  metrics per scope/period; model and scope statuses (§9); carry forecast assumptions;
  `budget_risk_unsuitable` when `excess_negative_correction` is present.

## Phase 3 — Artifacts and CLI
- `artifacts.py` — atomic writes; `budget.json`, `ratios.json`, `request.json`,
  `resolved_config.yaml`, `paths/index.json` + per-scope `.npy`, `manifest.json` last;
  `load_budget_run` reader with checksum and path-traversal checks mirroring
  `forecasting/artifacts.py`.
- `cli_budget.py` — `--forecast-run --config --budgets --output --force --validate-only`; exit
  codes 0/1/2; JSON summary on stdout, JSON logs on stderr. `--force` never touches the forecast run
  (reuse the protected-path pattern in `forecasting/runner.py::_force_clear_output`).
- `utils/budget.sh` wrapper → `python -m forecast.budget.cli_budget` (pattern of `utils/forecast.sh`).
- `config/budget/budget.yaml` (commented template), `config/budget/budgets.example.yaml`.
- `__init__.py` exports `run_budget`, `load_budget_run`, `BudgetRequest`.

## Phase 4 — Presentation
- `presentation.py` — per scope/period: cumulative fan with budget line, exhaustion curve,
  period-total distribution with budget and confidence budgets, model/agent breakdown.
  Each chart: one CSV in `data/`, then PNG (matplotlib) and self-contained HTML (Plotly,
  plotly.js inlined) rendered from that CSV's numbers.
- `report.md` in the §11 order, including the per-date-quantile, non-additive-scopes, and
  scaled-agent-band notes.
- Module `README.md` (usage, outputs, worked example).

## Phase 5 — Real price catalog (blocked on `common.model_pricing`)
- Replace the fake with `common.model_pricing.load_catalog`; record `catalog_version` and source.
- Unpriced models (`unmatched`, `ambiguous`, missing token-type rate) → `unpriced` → scope
  `incomplete`; report lists the mapping and failures.
- End-to-end run on the 60-day synthetic dataset with a real forecast run.

## Follow-up (not in this plan)
- Update `docs/arch/module-architecture.md` §2 (budget + scenarios one module; `common.model_pricing`;
  reporting boundary) — doc change, can land with Phase 3.

## Reused components
- `forecast.forecasting.artifacts.load_run` / `SavedRun.load_paths` (validated mmap paths).
- `forecast.forecasting.dataset.load_dataset` / `PreparedDataset.usage`, `.coverage`, `.hashes`.
- `forecast.forecasting.request` / config scope-name resolution.
- `forecasting/runner.py::_force_clear_output` pattern; `forecasting/schemas.py`
  (`canonical_bytes`, `identity`, `slug`, `path_name`) for stable IDs and file names.
- `forecast/utils/plot_forecast.py::export_html` / `export_png` approach for offline HTML/PNG.
- `common.msging.log.get_logger` for structured JSON logs.

## Verification
- **Worked example (§7) as a unit test:** totals 105 / 85 / 115; expected 101.67;
  `required_for_confidence[0.8]` = 111; `p_sufficient` = 1/3; `p_exhausted_by` = 0, 1/3, 2/3;
  `given_crossing` median day 2, mean offset 2.5.
- Blended-rate example (§4.1): 20% / 77% / 3% at $3 / $0.30 / $15 per M → $1.281 per M.
- Parent-blending example (§5.2): 4 active days, 10% vs 3% → 5.5%.
- Ratios: heavy day outweighs light day; no date after origin used; outage days excluded;
  Organization unblended; new-agent-from-parent flagged.
- Aggregation: scope percentile ≠ Σ model percentiles on a constructed case; scopes never summed.
- Statuses: inactive → zero cost + `continued_inactivity`; unavailable/unpriced → `incomplete`,
  `cost`/`budget` null, blocking models listed, CLI exit 2.
- Periods: two requested months evaluated independently, internal dates excluded.
- Artifacts: reader rejects tampered checksum / traversal; `--force` leaves the forecast run intact;
  PNG, HTML, and CSV agree for each chart; HTML loads offline.
- Dataset identity mismatch → `InputError`.
- `venv/bin/python -m pytest econ-forecast/tests/budget econ-forecast/tests/forecasting -q`.
