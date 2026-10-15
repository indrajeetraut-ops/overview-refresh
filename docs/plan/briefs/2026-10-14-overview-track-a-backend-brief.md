# Brief — Overview refresh, Track A backend (A1, A2)

Implement §3 A1, A2 and the backend half of A5 of `docs/plan/2026-10-14-overview-ux-refresh.md`
(rev 3). Read §0, §2, §3 first, then `src/webui/AGENTS.md`, `src/server/api/schemas/README.md`,
root `CLAUDE.md`, and `docs/plan/2026-10-14-overview-refresh-progress.md`. Work ONLY in this
worktree (`/Users/anil/workspace/Talium/code-econ/.claude/worktrees/overview-refresh-a`,
branch `feat/overview-refresh-a`). A3/A4 (front end) are a later brief; do not start them.

Reference arithmetic (read-only, ideas not binding): `/Users/anil/workspace/Talium/talium-lovable-1.0/src/lib/budget-cycles.ts`
and its tests `src/test/budget-cycles.test.ts`. Port the period generation faithfully (sprint
numbering anchored on `anchor_number` + `anchor_date`; month/quarter/half/year calendar; custom
one-time/recurring; "last N periods that started, plus the next one") and port its tests.
NEVER modify that repo.

## Steps (strict order; the coordinator releases one at a time)

**A1. Storage and model.**
- Table `budget_cycle`, one row per org: `kind` (`month|quarter|half|year|sprint|custom`),
  `sprint_weeks`, `anchor_date`, `anchor_number`, `custom_end`, `recurrence`
  (`recurring|one-time`), `updated_at`. SQLAlchemy model in `server/storage/schema/` (follow
  `budgets.py`, register it in the schema `__init__`), Alembic revision in
  `server/storage/dbm/alembic/versions/` (follow `8e59d8946ba4_budgets_allowances.py` and
  `test_budgets_migration.py`: add an up/down migration test). Seed it in
  `server/storage/synthetic_load.py` only if budgets are seeded there.
- No row => cycle is `month`, recurring: today's behaviour holds exactly.
- `budgets.period` stores the cycle label (`monthly`, `quarterly`, `half-yearly`, `yearly`,
  `sprint`, `custom`). Rows under another label stay stored but are not read: scopes show "no
  budget" until amounts are entered for the new cadence. Define the label mapping in one place.
- `server/api/manager/budget_cycles.py`, framework-free like `budgets.py` (plain Session +
  QueryCatalog in, dicts out; SQL in `config/queries/manager.yaml`):
  - `periods(cycle, today, history)` -> `[{key, label, start, end_exclusive, is_current,
    days_total, days_elapsed}]` plus `next`, UTC dates.
  - load/save of the org's cycle; validation (sprint_weeks >= 1, custom end >= start, ...) as
    `BadRequestError`.
  - generalise `budgets.period_bounds` to any cycle (keep the `"monthly"` behaviour identical).
- Thread the org's active cycle to every place that now reads `config.budget_period`
  (service.py has ~15 call sites, `single_user/service.py`, `app.py` budget routes). Prefer
  resolving the cycle ONCE per request (a small helper returning label + cycle) over reading the
  DB at each site. `config.budget_period` stays as the default label when no row exists.

**A2. API.**
- `GET /v1/budget-cycle`, `PUT /v1/budget-cycle` (lead/admin, same gate as budgets).
- `GET /v1/budget-cycle/periods?history=6`: periods with exact ISO `from`/`to` (server is the
  only place calendar/sprint arithmetic happens). Readable by any authenticated viewer who may
  see the dashboard (the picker needs it) — confirm against how other read routes are gated and
  tell me what you chose.
- Pydantic models in `schemas/`, then `openapi.json` and `api-types.ts` regenerated with
  `utils/gen_api_types.sh` (never hand-edit the TS).
- Window budget, in `aggregate.window_budget_usd_micros` and `seat_allowance_usd_micros`
  (replace `BUDGET_PERIOD_DAYS = {"monthly": 30}`):
  - when `[from, to)` is a whole period of the cycle, OR a period-to-date of it (starts exactly
    at a period start, ends inside it), the budget is the FULL period budget. Seat allowance =
    team period budget / members, still prorated from a seat's start date.
  - any other window (rolling): prorated by days over the cycle's ACTUAL period length (the
    period that contains the window's end), replacing the fixed 30.
  - `period=` / `PERIOD_DAYS` stay accepted for compatibility.
  - Consequence (accepted by the user): early in a period few teams/seats read as over budget.
- Economics `budget_burn` (service.py ~1240, `period_bounds`) and the forecast endpoints: the
  forecast is by calendar month (§0/A4). Do NOT change the forecast itself. For a non-monthly
  cycle report to me, in the handshake, exactly what budget figure `/v1/economics` and
  `/v1/forecast` use today and what you would do; my default is: prorate the cycle budget to the
  calendar month by days (budget x days_in_month / cycle period length) and keep the forecast
  calendar-month. Do not implement beyond the monthly-identical path until I confirm.
- Tests (A5, backend): period generation for every kind and the edges (sprint numbering, quarter
  and year rollover, one-time custom, today before the anchor, recurring custom, leap years);
  full-period vs prorated window budget; `budget_pct`/`over_budget` for a period-to-date window
  on Overview, Teams and Team; seat allowance; the routes and their gate; migration up/down;
  no-row default equals today's monthly behaviour (existing tests must still pass unchanged).

## Rules
- One cost source; dollars via the precedence chain; budgets in micros through
  `budget_usd_micros`.
- pytest uses the real DB: `export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test`
  (fixture DROPS all tables on that DSN; never any other DB). Python:
  `/Users/anil/workspace/Talium/code-econ/venv/bin/python -m pytest …` from the worktree root.
  ONE pytest process at a time (a second one on the same DB flakes). Run the full
  `tests/server/api` dir and `tests/server/storage` at the end of each step, since budgets touch
  every manager endpoint.
- Front end: no UI work here, but regenerated types must keep `npm run typecheck`, `npm test`,
  `npm run lexicon`, `npm run build` (in `src/webui`, node_modules installed) green; update
  `mock-source.ts` only as far as the new generated types require.
- Never `git -C`. Stage only; the coordinator commits after review. No Claude/Co-Authored-By
  references. TODO in source for anything deferred. Structured JSON logging for new modules.
- Stop and report (do not decide) if: you would remove/move/hide anything not covered by §0; a
  requirement is ambiguous; existing tests need changing to pass.

## Handshake
Before any code, reply with (a) does the brief make sense, (b) ambiguities/concerns with
file:line (call sites that read `config.budget_period`; where the cycle should be resolved per
request; the gate for the periods route; the non-monthly Economics/forecast question above),
(c) your approach for A1 and A2 in a few lines each. Wait for "go".
