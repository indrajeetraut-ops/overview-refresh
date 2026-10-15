# API contract — `server/api/schemas/`

**These Pydantic models are the source of truth** for the dashboard API (MVP plan
`docs/plan/2026-09-09-mvp-backend-implementation.md` §1 + F0). FastAPI emits an OpenAPI schema
from them; the front-end's TypeScript types are **generated** from that schema and drift fails
the build — it is not a convention two files must remember.

## The pipeline

```
server/api/schemas/*.py  ──FastAPI──▶  openapi.json  ──openapi-typescript──▶  src/webui/src/data/api-types.ts
        (edit here)                  (server.api.cli_export_openapi)         (generated; never hand-edit)
```

Regenerate + drift-check: `utils/gen_api_types.sh` (wired into CI). A planted field rename in a
model, without regenerating, is a red build.

## The envelope wraps every manager view model

Every manager endpoint returns `Envelope[SomeViewModel]` (`envelope.py`):

- **mode block** on every response — `SessionMode` (one `session_info` row) or `AggregateMode`
  (a timeframe spanning >1 datasource mode).
- **every dollar is `Money`** — `usd_micros` + `cost_basis` + `cost_complete`. One cost source
  (ground rule 3); the API never re-derives dollars from tokens.
- **absent ≠ empty** — an uncomputable block is `data: null` + `unavailable: {block, requires}`,
  or `Unavailable` on the sub-block. Never `0`, never `[]`.

## Prototype-isms to strip (do NOT put these in a model)

The `mock.ts` shapes in the figma prototype are a useful *inventory* of what each screen needs,
**not** the contract. As shapes cross into a model, strip:

1. **No presentation in data.** No `color`/`fill` (`"#1A7A4A"`, `"var(--primary)"`,
   `"var(--amber)"`). The API returns the *category/label*; `src/webui` maps it to a hue
   (the provenance/quality palette is pinned client-side, BLUE §7). Applies to `workQuality[]`,
   `channels[]`, `agentSpend`, `sessionTypes[]`, everywhere.
2. **No pre-formatted display strings.** Return structured/numeric values:
   `buildTime:"4 days"` → `build_days: 4`; `activeLabel:"3h 20m"` → `active_minutes: 200`;
   `avgTimeToPR:"2.4d"` → a number + unit; `lastActive:"2h ago"` → an ISO timestamp. The
   client formats time and relative dates.
3. **No navigation hints in data.** `worthALook[].screen`/`screenParam` are routing. The API
   returns the finding + a typed entity ref (`{kind, id}`); the client decides the route.
4. **Generated fakes are scaffolding, not contract.** `genEnvelope()`, `genBurn()`, `econFor()`
   and the hardcoded multipliers describe a *picture*. The real shapes they imply (a `Dist`, an
   envelope series, a burn series) are computed — see Track X5.

## Lexicon gate

Surfaced strings (worth-a-look findings, habit labels, quality labels, channel labels, flag
text) pass the tone lexicon: **recoverable / could save / room to save**, never "waste".
Quality is exactly **Clean / Heavy lift / Quick & dirty / Costly rework**. The lexicon CI check
covers API-produced strings, not just front-end literals.

## Porting status

| Endpoint | Model | State |
| :-- | :-- | :-- |
| `GET /v1/pricing/models` | `PricingModelsResponse` | ✅ F0 reference |
| `GET /v1/users/{id}/overview` | `Overview` | ✅ U5-myusage |
| `GET /v1/users/{id}/rhythm` | — | dict |
| `GET /v1/users/{id}/sessions` | — | dict |
| `GET /v1/sessions/{id}` | — | dict |
| `GET /v1/users/{id}/insights` | — | dict |
| `GET /v1/users/{id}/repos` | — | dict |
| `GET /v1/forecast?scope_type=&scope_id=` | `Envelope[BudgetForecast]` | ✅ T6 (forecast dashboard plan §3.6/§3.8) |
| `GET /v1/forecast/plan` | `Envelope[BudgetPlan]` | ✅ T6 |
| `GET/PUT /v1/budget-cycle` | `BudgetCycleResponse` / `BudgetCyclePutResponse` | ✅ Overview refresh A2 |
| `GET /v1/budget-cycle/periods?history=` | `BudgetPeriodsResponse` | ✅ Overview refresh A2 |
| manager endpoints (A/D/E) | `Envelope[…]` | built per track |

### The forecast models (T6)

Money in `BudgetForecast` / `BudgetPlan` is a plain integer `*_usd_micros` (like economics'
`budget_burn`), not `Money`: the figures are model outputs read from a precomputed run, and the
actuals are `cost_effective_usd_micros` summed over the scope's sessions, bucketed by local
date in the run's `timezone` (the `/v1/economics` `budget_burn` path, one cost source). Dates
are `date`, probabilities are floats, `runway.status` is one of `lasts | at_risk | runs_out |
exhausted`; the client words them. `forecast: null` (no run, unreadable run, or a scope that is
not in it) and `data: null` + `unavailable` (the planner) follow envelope rule 3.

### The org overview models (Overview refresh, Track B)

`GET /v1/org/overview` (`Envelope[OrgOverview]`) keeps one cost source: `headline.spend`, each
`agent_spend[].spend` and `daily_spend` are resolved through the precedence cost chain
(`dashboard.precedence.cost`) per session and then summed, so `sum(agent_spend) ==
sum(daily_spend) == headline.spend.usd_micros` exactly (pinned by tests). Each agent's `Money`
carries that agent's own `cost_basis`; an agent whose rows resolve to nothing is listed with
`Money(None)`, not 0.

- **`daily_spend`** `[{day: date, spend_usd_micros: int}]` replaces `monthly_spend` (and the SQL
  `scope_monthly_spend`). It lists the window's **elapsed UTC days only**, from `from`'s day to
  the day of `min(to, now)` inclusive of today (`to` is exclusive), missing days `0`. A session
  counts on the UTC day of `max(started_at, from)`; a day outside the elapsed range (a session
  dated after `now`) is clamped into the nearest edge day so the sum still reconciles. Days are
  UTC, not the forecast's local-timezone days, so a day can differ slightly from Economics'
  `budget_burn`. A rolling window starts mid-day, so a 15-day window gives 16 buckets (N+1 UTC
  days), the first one partial.
- **`agent_task_mix`** `[{agent, type, count}]`: root sessions per (agent, dominant session
  type), from the same rows and rules as `OrgUsage.session_types` (roots only, no `session_type`
  omitted), so summing `count` per `type` equals it. Counted by session, never dollars. `type`
  is the raw label; the client names and colours it. Ordered agent asc, count desc, type asc.
- **Removed:** `initiatives_trend` (and `issues_started_weekly` / `issues_shipped_weekly`),
  `monthly_spend`.

### Budget cycle models (Overview refresh, Track A)

`GET/PUT /v1/budget-cycle` read and replace the org's budget cycle (`kind` month | quarter | half |
year | sprint | custom, plus sprint length, anchor, custom end and recurrence); no saved row means a
recurring calendar month. `GET /v1/budget-cycle/periods?history=` lists the periods the picker
offers, with exact `from` / `to` (UTC): the server is the only place calendar or sprint arithmetic
happens. For the current period `to` is `min(now, end_exclusive)`.

- Budget amounts are stored under the cycle's label (`monthly`, `quarterly`, `half-yearly`,
  `yearly`, `sprint`, `custom`). Changing the kind changes the label: existing amounts are kept but
  not read, and scopes show no budget until amounts are entered for the new cadence. A same-kind
  edit (sprint length, custom dates) keeps the label, so amounts now mean "per period" of the
  changed cycle. `PUT` returns a `note` saying which.
- The window budget used by `budget_pct`, `over_budget` and seat allowances: a window that starts
  exactly at a period start (00:00 UTC) and ends inside or at the end of that period gets the
  **full period budget**; any other window is prorated by its days over the actual length of the
  period that contains its last instant. A seat's allowance is its team's period budget / members,
  prorated from the seat's start (to the period end in a full-period window).
- Forecast: still by calendar month. For a non-month cycle the forecast and plan budget is the
  cycle budget prorated to the calendar month by days; `budget_burn` stays on the calendar month.
  TODO(A4): run `econ-forecast` per org cycle.
- A saved row that no longer validates is read as the default month and logged
  (`budget_cycle.stored_row_invalid`); the next `PUT` replaces it.
