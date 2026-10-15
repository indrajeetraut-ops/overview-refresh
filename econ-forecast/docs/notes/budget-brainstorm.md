# Budget — brainstorm scratchpad

Feeds `docs/design/budget-module-design.md` (rewrite; the current file predates the cost split).
Started 2026-09-27. Skill: `talium-design-doc`. Upstream settled: `docs/design/cost-module-design.md`.

## Decisions

| # | Decision | Notes |
|---|---|---|
| B1 | Budget reads cost runs only (`load_cost_run`); never the forecast, dataset, or prices. | From cost D5. |
| B2 | Baseline and scenario cost runs are evaluated identically. | User, 2026-09-27. |
| B3 | Phase 1: evaluate a budget. Phase 2: reverse question — find scenarios reaching a target confidence within planner-set choices. | User, 2026-09-27. |
| B4 | Outputs: confidence P(period cost ≤ budget), risk = 1 − confidence, and the exhaustion curve P(budget used up by date) as the primary timing output; median crossing date always shown with the count of futures that ran out. Charts show confidence, risk, and date together. | User, 2026-09-27. |
| B5 | Budgets apply to scopes only: Organization, Team, Project. Never agent or model. Each scope evaluated from its own cost paths; scope budgets are independent (no reconciliation across levels). | User, 2026-09-27. |
| B6 | A budget is an input: scope + USD amount + period (start, end). The period must lie fully inside the cost run's dates; otherwise input error. | User, 2026-09-27. |
| B7 | Budgets file `budgets/1.0` as drafted; file only, no amount/period CLI flags; copied into the budget run. | User, 2026-09-27. |
| B8 | One budget run: budgets file × several cost runs (`--cost-run` repeated) of the same forecast; exactly one baseline; scenarios reported as change vs baseline. Incomplete scope → no numbers + reason; `risk_ready: false` → expected cost only, `budget_risk_unsuitable`; inactive → $0, "no recent usage". | User, 2026-09-27. |
| B9 | Outputs: `report.md`, machine-readable results, and curves (charts). | User, 2026-09-27. |

## Open questions

| # | Question | Status |
|---|---|---|
| Q1 | ~~Budget period~~ | Resolved → B6. |
| Q2 | ~~Several cost runs per budget run~~ | Resolved → B8. |
| Q3 | Confidence = P(period cost ≤ budget) over samples. "High confidence" = a planner-set target (e.g. 0.9). | Proposed. |
| Q4 | Evaluation is a pure in-memory function `evaluate(cost_run, budget) → result`, so phase 2 can score many candidates without writing artifacts. Phase 1 must ship it this way. | Proposed. |
| Q5 | Phase 2 search = enumerate a small grid of planner-declared choices (levers × allowed fractions), cost + evaluate each in memory, rank by confidence then by size of change. No optimizer. | Proposed. |
| Q6 | Phase 2 lives in budget as a second CLI (`cli_plan`), designed after phase 1. | Proposed. |
| Q7 | ~~Budget scopes~~ | Resolved → B5. |
| Q8 | ~~Budgets file~~ | Resolved → B7. |
| Q9 | Open-weight models in phase 2 need prices in `model_pricing` (hosted channel) and realistic `token_factor`s. | External item; phase 2. |

## Phase 2 input (user, 2026-09-27)

Example: "$40K for September — find a few scenarios that give high confidence." Planners constrain the
search to a set of choices:

1. Model mix within Claude (planner picks which models).
2. Model mix within Codex.
3. Prescribed open-weight models.
4. Agent A vs agent B.

## Draft — CLI (phase 1)

```bash
./econ-forecast/utils/budget.sh --budgets budgets.yaml \
  --cost-run artifacts/sep-cost-baseline \
  --cost-run artifacts/sep-cost-team1-opus-to-sonnet \
  --output artifacts/sep-budget
```

## Next topics

1. Core logic: per (budget, cost run) period totals per sample → confidence / risk → exhaustion curve →
   crossing dates (median, range, count). Required budget per confidence level?
2. Output schemas (machine-readable results) and charts.
3. Phase 2 design.

## Deferred

- Team / seat subscription budgets: not started upstream (cost is per-token only).

## External-module items

1. `forecast.cost`: `load_cost_run` / `CostRun` (reader). Phase 2 also `run_cost` in memory (no artifacts) — may need a non-writing entry point.
2. `common.model_pricing`: open-weight model prices (phase 2).

## Draft — budgets file (`budgets/1.0`)

```yaml
schema: budgets/1.0
budgets:
  - scope:
      type: team
      name: team1
    amount_usd: 40000
    start: 2026-10-01
    end: 2026-10-31
  - scope:
      type: organization
    amount_usd: 150000
    start: 2026-10-01
    end: 2026-10-31
```

Rules: scope must exist in the cost runs; `start ≤ end`, both inside the cost run dates; one entry per (scope, start, end).
