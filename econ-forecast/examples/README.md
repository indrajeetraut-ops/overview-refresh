# Examples

Two **workspaces**, each built end to end by the workspace pipeline
([`src/forecast/pipeline/README.md`](../src/forecast/pipeline/README.md)): one forecast, everything
derived from it, and the charts drawn into each run. Operator walkthrough:
[`docs/operations/README-forecast.md`](../docs/operations/README-forecast.md).

| Path | What it is |
|---|---|
| [`curve-guided/`](curve-guided/) | Curve-shaped data (org ramp-and-plateau, team2 steady-decline, claude s-curve); ETS learns each trend and projects it into September 2026. Cost baseline + three scenarios. |
| [`known-event-spike/`](known-event-spike/) | Flat baseline data with a **declared** dated spike (`known_events.org`, ~+40% over Sep 14–18): the planned spike shows in the forecast mean **and** band, in every scope. Forecast only. |
| [`synthetic/`](synthetic/) | Synthetic dataset configs (the datasets above come from `curve-guided.yaml` and `baseline.yaml`). |
| [`curve/`](curve/) | The locked curve artifacts the synthetic configs bind to. |
| `dataset/` | The generated datasets, `synth-curve-guided/` and `synth-baseline/` — **not committed** (see Reproduce). |
| `assets/plotly.min.js` | The one shared Plotly copy every chart loads (`plotly_js` in each `plot.yaml`). |

Every scope is forecast (organization, team1–3, five projects; each series is scope × model, both
agents). Charts open offline straight from the checkout: each HTML loads `assets/plotly.min.js` by a
relative path.

## Workspace layout

```text
curve-guided/
├── pipeline.yaml                  # stages [forecast, cost, plot]; forecast: month, offset 1, periods 1
├── plot.yaml                      # forecast: every scope; cost: org (total, breakdown, per-model), teams, projects
├── pipeline_result.json           # what the last pipeline run did
├── forecast/
│   ├── forecast.yaml              # input.dataset_dir: ../../dataset/synth-curve-guided
│   └── run/                       # forecast run + charts/  (<scope>__models__tokens.{html,png})
└── cost/
    ├── scenarios/                 # team3-cheaper-models, org-cheaper-models, org-growth
    ├── baseline/                  # cost run + charts/ + report.md (links the charts)
    └── <scenario>/                # one cost run per scenario file
```

`known-event-spike/` is the same without `cost/` (stages `[forecast, plot]`). Links between runs
are relative (forecast → `../../../dataset/…`, cost → `../../forecast/run`), so the whole
`examples/` directory can be moved as one piece.

Start with a cost run's `report.md` (e.g. [`curve-guided/cost/team3-cheaper-models/report.md`](curve-guided/cost/team3-cheaper-models/report.md));
its Charts section links that run's charts. Each `charts/index.json` lists the chart files; `charts/result.json`
is the full plotting record.

## Reproduce (run from `econ-forecast/`)

```bash
# 1. Users and roster the synthetic configs read (../../artifacts/{users,roster}; not committed)
./utils/synt_gen.sh --op user-create   --config config/synthetic/users.yaml  --output artifacts/users  --force
./utils/synt_gen.sh --op roster-create --config config/synthetic/roster.yaml --output artifacts/roster --force

# 2. Datasets (deterministic: fixed seeds)
./utils/synt_gen.sh --op series-create --config examples/synthetic/curve-guided.yaml \
  --output examples/dataset/synth-curve-guided --force
./utils/synt_gen.sh --op series-create --config examples/synthetic/baseline.yaml \
  --output examples/dataset/synth-baseline --force

# 3. Workspaces: forecast -> cost (baseline + every cost/scenarios/*.yaml) -> charts
./utils/pipeline.sh --config examples/curve-guided/pipeline.yaml
./utils/pipeline.sh --config examples/known-event-spike/pipeline.yaml
```

Each pipeline run re-creates its runs and charts (`--force`) and rewrites `pipeline_result.json`;
on failure that file names the failing command, its error and the last 50 stderr lines.

## Cost results — `curve-guided`, September 2026

Prices (USD per million tokens, `price_version` 2026-09-25-d3e5c412). Each of the five token
types is priced at its own rate; `gpt-5.6-*` publish a 5-minute cache-write price only, so the
1-hour write is billed at it (`cache_write_1h_billed_as_5m`):

| Model | Input | Cache write 5m | Cache write 1h | Cache read | Output |
|---|---:|---:|---:|---:|---:|
| claude-opus-5 | 5.00 | 6.25 | 10.00 | 0.50 | 25.00 |
| claude-sonnet-5 | 2.00 | 2.50 | 4.00 | 0.20 | 10.00 |
| gpt-5.6-sol | 4.00 | 5.00 | 5.00 | 0.40 | 20.00 |
| gpt-5.6-terra | 2.00 | 2.50 | 2.50 | 0.20 | 12.00 |

| Scenario file | What changes |
|---|---|
| — (`baseline`) | Forecast tokens priced as they are. |
| `team3-cheaper-models.yaml` | team3 moves 50% of Opus → Sonnet and 50% of GPT-5.6 Sol → Terra. |
| `org-cheaper-models.yaml` | The same two moves for the whole Organization. |
| `org-growth.yaml` | Usage +25% across the Organization from 2026-09-15. |

No new models; token consumption is assumed unchanged by a model switch (`token_factor: 1.0`).

Expected (p10–p90), USD:

| Scope | baseline | team3-cheaper-models | org-cheaper-models | org-growth |
|---|---:|---:|---:|---:|
| Organization | 7,084 (5,197–9,037) | 6,442 (4,730–8,237) | 6,161 (4,519–7,878) | 8,016 (5,807–10,333) |
| team1 | 766 (446–1,086) | 766 (446–1,086) | 670 (388–955) | 857 (479–1,238) |
| team2 | 724 (406–1,045) | 724 (406–1,045) | 626 (354–905) | 816 (450–1,191) |
| team3 | 5,749 (4,255–7,150) | 5,002 (3,695–6,239) | 5,002 (3,695–6,239) | 6,521 (4,759–8,198) |
| payment_service_improvement | 759 (672–857) | 759 (672–857) | 667 (590–754) | 863 (765–973) |
| auth_service_rewrite | 439 (377–504) | 439 (377–504) | 379 (325–436) | 497 (427–571) |
| data_pipeline_upgrade | 442 (175–734) | 442 (175–734) | 380 (150–632) | 500 (186–840) |
| search_service_improvement | 443 (255–665) | 443 (255–665) | 383 (228–570) | 502 (287–762) |
| developer_tooling | 5,810 (4,327–7,250) | 5,054 (3,753–6,319) | 5,054 (3,753–6,319) | 6,594 (4,839–8,322) |

What to read from it:

- **A change at every scope level.** `team3-cheaper-models` lowers team3 (−13%) and its project
  `developer_tooling`; the Organization drops by team3's share of its Opus/Sol usage (−$642); team1,
  team2 and their projects are identical to baseline.
- **Declared at the Organization,** the same moves reach every team and project (`org-cheaper-models`,
  Organization −13%).
- **Growth from mid-month** (`org-growth`): +25% on 16 of 30 days ≈ +13% for the month.
- Scopes are overlapping views with independent samples: team and project totals do not add up to
  the Organization, and a project can exceed its team's p10–p90 range.

## `known-event-spike`

A one-off historical spike washes out of a forecast, so a *planned* spike is declared on the
forecast config instead: the overlay lifts the forecast over its window (mean + band), the applied
event is recorded in `forecast/run/diagnostics.json` and tagged `known_event_applied`, and the
charts show it in every scope (teams and projects inherit the org event).
