# Implementation brief — `forecast.cost`

## Where
- Worktree: `/Users/sumandra/projects/talium/forecasting/code-econ-cost` (branch `feat/cost`).
  Work **only** here. Do not touch the main checkout (`…/code-econ`).
- Python: `/Users/sumandra/projects/talium/forecasting/code-econ/venv/bin/python` (the worktree has no
  venv). Run from the worktree root; `pytest.ini` puts `src` and `econ-forecast/src` on the path.

## What
- **Authoritative design:** `econ-forecast/docs/design/cost-module-design.md` (cited §N). The design wins
  over this brief and the plan if they disagree — raise it.
- **Plan:** `econ-forecast/docs/plan/cost-module-plan.md`. Implement **T1–T12**. **Not T0** (issue
  creation is the coordinator's).
- Style reference for the whole module: `econ-forecast/src/forecast/forecasting/` (errors, artifacts
  reader, runner, CLI, presentation, logging). Mirror its idioms.
- Files in scope:
  - `econ-forecast/src/forecast/cost/**`, `econ-forecast/config/scenarios/**`, `econ-forecast/utils/cost.sh`,
    `econ-forecast/tests/cost/**`
  - T1: `src/common/model_pricing/**` (new `rate_sheet`), `tests/common/model_pricing/test_rate_sheet.py`,
    `docs/modules/model-pricing.md`, `src/common/model_pricing/README.md`
  - T2: `econ-forecast/src/forecast/forecasting/{runner,artifacts}.py` and their tests,
    `econ-forecast/docs/design/forecasting-model-design.md` §10
  - T12: `econ-forecast/docs/arch/module-architecture.md`
- Out of scope / do not modify: `src/common/pricing.py`, forecasting's `allocation.py` and
  `allocations.json` (known error, stays), budget docs, anything else.

## Phases — stop after each and report
1. **Phase A:** T1, T2.
2. **Phase B:** T3–T9 (library, no CLI).
3. **Phase C:** T10–T12, full test run, test report.

After each phase: stop, report files changed, tests run and results, deviations and open questions; wait
for the go-ahead.

## Decisions not spelled out elsewhere
- A forecast run without a recorded dataset sha256 (runs made before T2) → `InputError` with a message to
  re-run the forecast. No fallback.
- Dataset path comes from the forecast run's record; a missing path → `InputError`.
- Scenario transforms need no other scope's paths: each scope's series are transformed with `k` and the
  precomputed attribution shares (§5, §6.3), so one-scope-at-a-time processing (§2.2) still holds.
- `rate_sheet` builds on existing `load_catalog()` → `Catalog.for_org()` → `OrgPrices.resolve()` /
  `rates()`; do not reimplement matching or date rules.
- Rates: `Decimal` strings in the sheet, converted to float64 once in `rates.py`.
- Example data for end-to-end: `econ-forecast/examples/plots/curve-guided/{dataset,forecast.yaml}`.
  Its saved `forecast/` records a stale absolute dataset path and no dataset hash — re-run the forecast into
  a temp dir after T2; never modify `examples/`. Automated end-to-end tests build their own small synthetic
  dataset in `tmp_path` (see existing forecasting integration tests).

## Repo rules (root `CLAUDE.md`)
- Python 3.11. Deps already present (numpy, pydantic, PyYAML, structlog, matplotlib, plotly); add none
  without asking.
- Structured JSON logging with structlog, configured in the CLI, as in `forecasting/cli_forecast.py`.
- CLI entry `cli_cost.py`; wrapper `econ-forecast/utils/cost.sh` like `utils/forecast.sh`.
- `TODO(<ref>)` for anything deliberately deferred.
- No references to Claude/AI tooling in code, comments, or docs.
- **Do not commit.** The coordinator reviews and commits.
- Git without `-C`.

## Tests and report
- Tests per the plan's test table; every design worked example is a test ($1.281 / $1.41, 5.5% blend,
  40% × 30% → 12%).
- Run and persist (Phase C):
  ```bash
  venv=/Users/sumandra/projects/talium/forecasting/code-econ/venv/bin/python
  $venv -m pytest econ-forecast/tests/cost -q --junitxml=docs/test/reports/cost-junit.xml
  $venv utils/gen_test_report.py --title "Cost (forecast.cost)" \
      --junit docs/test/reports/cost-junit.xml \
      --out docs/test/reports/cost-test-report.md --tests econ-forecast/tests/cost/
  ```
- Also run `tests/common/model_pricing` and `econ-forecast/tests/forecasting` — nothing existing may break.

## Done when
- T1–T12 acceptance criteria met; all tests green; report files written; plan verification steps 1–4 run
  (report their output).
- Final message: files created/changed, test counts, deviations from design/plan and why, open questions.

## First step: handshake (no code)
Read the design, the plan, this brief, and the code you will reuse (`forecasting/artifacts.py`,
`forecasting/dataset.py`, `forecasting/history.py`, `forecasting/runner.py`, `common/model_pricing/catalog.py`).
Reply with:
1. Whether the instructions make sense.
2. Concerns, ambiguities, or contradictions (design vs code vs plan), each with a proposed resolution.
3. Your intended internal design in a few bullets: how `SavedRun` gains the new facts, the `RateSheet`
   type and segment building, how covered days / outages are obtained for the mix, the in-memory shape of
   a scope's token paths during a scenario, and how `load_cost_run` validates files.

Then **stop and wait** for the go-ahead.
