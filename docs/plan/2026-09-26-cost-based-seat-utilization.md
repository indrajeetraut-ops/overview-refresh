# Cost-based seat utilization and window-aware thresholds: implementation plan

**Status:** IMPLEMENTED 2026-09-26 on `feat/cost-based-seat-utilization` (`32f3865` API, `7df8b57` web UI,
`9ed702b` generator). Verified against the real synthetic dataset: every migration applied up to
`c4e8a2f1d7b3`, seat usage matches SQL to 4 dp, and the team shape matches §5.4. Not yet on prod (§6).
Deviations from the text below: the mock source does not window-scale (its fixtures don't vary
by window, §3.7), and `tests/server/api/test_synthetic_smoke.py` also needed the loader import fix
(it reaches `put_allowance` transitively).
**Branch:** new `feat/cost-based-seat-utilization`, cut from `demo-release-v2` after committing its two
pending files (`utils/synth_dump.sh`, `utils/stopgap_seed.sh`).
**Scope:** `code-econ` (API, storage, web UI, synthetic loader) and `econ-forecast/` (synthetic
generator, same repo).

---

## 0. Why

Three metrics on the Team, Usage and My-usage screens misread the data. The causes are all
design decisions, not data errors. The evidence below was measured on prod (`cc-econ-demo`,
2026-09-25/26):

1. **Seat utilization is measured in tokens, but caps are set in dollars.** Team "over"
   already means spend > dollar budget (dashboard iteration plan 2026-09-08). The per-seat
   token allowance survived from the 2026-09-03 UX plan ("512k tokens/person/month") with no
   recorded rationale.
   - A token is not a fixed amount of money: prod real data costs $0.50 per million tokens,
     the synthetic org $1.69.
   - `tokens_total` counts cache reads (`graph_fold.py:153`), so good caching habits inflate a
     seat's token count while lowering its cost.
   - Neither prod database has a single `allowances` row, so seat usage is `—` everywhere.
2. **"Room to save" is a flat $50 of recoverable spend per window.**
   - It ignores window length. Recoverable *share* is flat across windows (synthetic
     development median 2.8% / 2.8% / 3.0% at 15 / 30 / 60 days) while the dollar amount grows
     with the window.
   - It ignores spend scale: per-person spend varies up to 10x inside one team ($33–$300 over
     15 days in development).
   - Result: synthetic max per person is $44.72, so the count is always 0. Real data runs at
     ~13% recoverable.
3. **"Low seat use" is a flat 40% of a periodless allowance compared with window usage.** The same
   person reads twice as high on the 30-day view as on the 15-day view.

Found while mapping the code. Same root cause, fixed here too:

4. **`budget_pct` / `over_budget` compare window spend with an unscaled monthly budget**
   (`service.py::_team_summary`, `org_overview` headline). The synthetic 15-day overview reads
   "31% of budget" when the like-for-like figure is about 61%.
5. **My-usage treats the whole team's monthly budget as "your limit"** (`single_user/service.py::_my_budget`)
   and compares it with the person's *window* spend under the words "this month".
6. **Idle seats are invisible.** `seats_under_threshold` and `team_detail` iterate over people
   who *had sessions in the window*. A member with zero usage — the clearest low-use case — is
   never counted or listed.

---

## 1. Decisions (locked 2026-09-26)

| # | Decision | Chosen |
|---|---|---|
| D1 | Unit of seat utilization | **Cost (USD)**, not tokens |
| D2 | Source of a seat's allowance | **Team monthly budget ÷ team members.** No per-seat override (a later addition if asked) |
| D3 | Token allowance concept | **Removed**: table dropped, API field removed, generator stops emitting it. Tokens stay on screens as information only |
| D4 | Person with no team | **No seat allowance.** `usage_pct = None`, shown "—"; never a guessed org-wide split |
| D5 | Per-team threshold overrides | **None.** Team budgets express team-specific expectations; one set of bands org-wide |
| D6 | Room-to-save rule | recoverable ÷ spend ≥ **10%** **and** recoverable ≥ **$10 per 30 days**, scaled to the window |
| D7 | Low-use grace for new seats | **14 days** |
| D8 | Utilization bands | unchanged: `under` < **0.40**, `over` ≥ **0.95** |
| D9 | Budget-to-window scaling | a monthly budget is **30 days** of allowance: `budget × window_days ÷ 30` |

D9 matches the rolling `15d/30d/60d` selector (`aggregate.PERIOD_DAYS`). E1 `economics` keeps its
own calendar-month `period_bounds` logic untouched, because it forecasts to period end, which is
a different question.

---

## 2. Definitions (the single source of truth for every formula below)

All money is integer USD micros internally (the API's existing `Money` convention). Budgets are
stored as `Numeric(12,2)` USD and converted with `int(Decimal(amount) * 1_000_000)`.

```
window_days(frm, to)       = (to - frm).total_seconds() / 86400            # float, > 0
window_budget(B, frm, to)  = B × window_days / 30                            # B = monthly budget (micros)

# seats
members(team)              = count(team_members where team_id = team)       # direct members only
seat_monthly(team)         = team_budget(team) / members(team)              # None if no budget or 0 members
seat_start(user)           = LEAST(users.created_at, min(root sessions.started_at))
seat_days(user, frm, to)   = window_days(max(frm, seat_start), to), floored at 0
seat_allowance(user)       = seat_monthly × seat_days / 30                   # None if seat_monthly None, or seat_days == 0
usage_pct(user)            = spend(user, window) / seat_allowance            # None when allowance None
in_grace(user)             = (to - seat_start) < new_seat_grace_days days
low_use(user)              = usage_pct is not None and usage_pct < under and not in_grace
usage_status(pct)          = None | "over" (pct >= over) | "under" (pct < under) | "healthy"   # unchanged

# teams (no proration: a team budget is a team figure)
team_utilization(team)     = spend(team, window) / window_budget(team_budget)   # None if no budget
budget_pct(scope)          = spend(scope, window) / window_budget(scope_budget)   # SAME formula; see 3.4
over_budget(scope)         = budget_pct is not None and budget_pct > 1.0

# room to save (per person)
share                      = recoverable / spend                             # None if spend == 0
floor                      = min_usd_per_30d × window_days / 30
room_to_save               = share is not None and share >= min_share and recoverable >= floor
```

**Edge cases** (each needs a test, §4):

| Case | Result |
|---|---|
| Team has no budget row | seat allowance `None` → `usage_pct None`, `low_use False`; `team_utilization`/`budget_pct None`; `seats_under_threshold None` |
| Team has budget but 0 members | `seat_monthly None` (never divide by zero) |
| Person on no team (D4) | `usage_pct None`; on My-usage `seat: null` |
| Person in several teams | seat computed against the team being viewed (`team_detail`), or the primary team (`user_primary_teams`) for org-level and My-usage views |
| Parent team with only sub-teams (synthetic `eng`: 0 direct members) | 0 members → no seat; its budget still drives its own `budget_pct` |
| `seat_start` after `to` | `seat_days = 0` → allowance `None` (not a divide-by-zero, not 0%) |
| Member with no sessions ever | `seat_start = users.created_at`; spend 0 → `usage_pct 0.0` → low use unless in grace |
| Explicit `from`/`to` (not a named period) | same formulas; `window_days` from the actual span |
| Spend 0 | `share None` → never room-to-save |
| Session straddling the window edge | counted wholly, the existing `scope_sessions_in_range` overlap rule; unchanged |

`users.created_at` alone is unusable on synthetic data (every row = load time), hence `LEAST`
with the first session. `team_members` has no join date; if one is added later, it replaces
`users.created_at` in `seat_start`.

---

## 3. Changes, layer by layer

### 3.1 Config

**`config/api.yaml`**: replace the `utilization:` block (currently lines 31–38) with:

```yaml
  # Seat utilization (cost-based, 2026-09-26 plan). Query-time, never stored -- no refold.
  # A seat's allowance is its team's monthly budget / members, scaled to the viewed window
  # (budget x window_days / 30) and prorated for seats that started inside it.
  utilization:
    over: 0.95                # usage >= this share of the window's allowance -> "over"
    under: 0.40               # usage <  this share -> "under" / low use
    new_seat_grace_days: 14   # no low-use flag within this many days of a seat's first activity
    room_to_save:
      min_share: 0.10         # recoverable / spend
      min_usd_per_30d: 10     # recoverable floor, scaled to the window
```

**`src/server/api/config.py`**:
- `UtilizationConfig`: remove `room_to_save_usd_micros`. Add `new_seat_grace_days: int = 14` and
  `room_to_save: RoomToSaveConfig = Field(default_factory=RoomToSaveConfig)`.
- New `class RoomToSaveConfig(BaseModel)` with `min_share: float = 0.10` and
  `min_usd_per_30d: float = 10.0`. Rewrite the docstring to the D6 rule.
- `ApiConfig.budget_period` comment: "the only budget cadence" (drop "/allowance").
- Pydantic here ignores unknown keys (no `extra="forbid"`), so a stale `room_to_save_usd_micros`
  in a deployed `api.yaml` would be **silently ignored**. The test in §4 loads the real
  `config/api.yaml` and asserts the new keys, so the file cannot drift.

### 3.2 Storage

**`src/server/storage/schema/budgets.py`**: delete `class Allowance`. Update the module
docstring and `Budget`'s docstring where they mention allowances.

**`src/server/storage/schema/__init__.py`**: remove `Allowance` from the import (line 3) and from
`__all__` (line 30).

**New migration** `src/server/storage/dbm/alembic/versions/<rev>_drop_allowances.py`. Generate it
with `python -m server.storage.cli_db --config config/storage.yaml revision -m "drop allowances" --no-autogenerate`, then hand-edit:
- `down_revision = "b7c31f9a4e02"` (the current single head, `b7c31f9a4e02_git_artifact_events.py`)
- `upgrade()`: `op.drop_table("allowances")`
- `downgrade()`: recreate exactly as `8e59d8946ba4_budgets_allowances.py` created it (columns
  `scope_type VARCHAR NOT NULL`, `scope_id VARCHAR NOT NULL`, `limit_tokens BIGINT NOT NULL`,
  `updated_at TIMESTAMPTZ NOT NULL`, PK `(scope_type, scope_id)`). Data is not restored.
  Neither prod database has rows today.

**`config/queries/manager.yaml`**:
- Delete `get_allowance`, `upsert_allowance` and `allowances_for_scopes` (lines 19–37).
- Add, next to `user_primary_teams`:

```yaml
# Seat start per user (cost-based seats plan §2): the earliest the system knew the person --
# LEAST ignores NULLs, so a never-active member falls back to users.created_at and a
# synthetic user (created_at = load time) falls back to their first session.
seat_start_for_users: |
  SELECT u.user_id, LEAST(u.created_at, min(s.started_at)) AS seat_start
  FROM users u
  LEFT JOIN sessions s ON s.user_id = u.user_id AND s.parent_session_id IS NULL
  WHERE u.user_id = ANY(:user_ids)
  GROUP BY u.user_id, u.created_at;
```

**`config/queries/synthetic.yaml`**: remove `allowances` from the `TRUNCATE` list (line 11) and
the `UNION ALL SELECT 'allowances', ...` row (line 55).

**`tests/fixtures/storage_ddl.sql`**: regenerate with
`python -m server.storage.cli_schema_dump --ddl > tests/fixtures/storage_ddl.sql`, per
`tests/server/storage/test_schema_dump.py:21`. The `CREATE TABLE allowances` block disappears.

### 3.3 Aggregation helpers: `src/server/api/manager/aggregate.py`

New functions (pure, framework-free, same module conventions):

```python
#: D9: a monthly budget is 30 days of allowance. Keyed by `config.budget_period`.
BUDGET_PERIOD_DAYS = {"monthly": 30}

def window_days(frm: datetime, to: datetime) -> float
    # (to - frm).total_seconds() / 86400; BadRequestError if <= 0

def window_budget_usd_micros(monthly_usd_micros: int | None, frm, to, period: str) -> int | None
    # None when monthly is None/0; else round(monthly * window_days / BUDGET_PERIOD_DAYS[period])

def budget_usd_micros(amount) -> int | None
    # Decimal/str/None -> int micros; the one conversion used everywhere below

def seat_allowance_usd_micros(seat_monthly_usd_micros: int | None, seat_start: datetime | None,
                              frm, to, period: str) -> int | None
    # prorated per §2; None if seat_monthly None, or seat_days == 0

def in_grace(seat_start: datetime | None, to: datetime, grace_days: int) -> bool
    # seat_start None -> False (no evidence of a new seat)

def fetch_seat_starts(s, catalog, user_ids) -> dict[str, datetime]
    # manager.seat_start_for_users; empty user_ids -> {}
```

Changed functions:
- **`budget_pct(spend_usd_micros: int, window_budget_usd_micros: int | None) -> float | None`**:
  takes the *window-scaled* budget in micros. Callers change (§3.4). The body stays a guarded division.
- **`person_status(recoverable_usd_micros: int, spend_usd_micros: int, frm, to,
  config: UtilizationConfig) -> str`** implements the D6 rule and returns `"room-to-save"` or
  `"on-track"`. The docstring states the rule and why it's a share (window- and scale-invariant).
- **`seats_under_threshold(...)`** is **replaced** by
  `seats_under_threshold(member_ids: list[str], spend_by_user: dict[str, int],
  seat_allowance_by_user: dict[str, int | None], seat_start_by_user: dict[str, datetime],
  to, config) -> int | None`. It iterates **all members** (fixes §0.6) and applies the full
  `low_use` rule, including grace. It returns `None` when every member's allowance is `None`.
  Delete `TODO(A2-per-seat-allowance)` (D2 settles it; the docstring points here).
- Add **`spend_by_user(rows, config) -> dict[str, int]`**, using `precedence.resolve_aggregate` per
  user so a person's spend matches `_cost` exactly (same precedence as every other `spend` field).
- `usage_status` is unchanged in logic. Its docstring changes "tokens" to "cost".
- `tokens_by_user` stays (tokens remain informational).

### 3.4 Manager service: `src/server/api/manager/service.py`

Shared seat context, built once per request and passed down (no N+1):

```python
def _seat_context(s, catalog, config, team_ids: list[str], frm, to) -> dict:
    """{team_id: {"monthly": micros|None, "window": micros|None, "members": int,
                  "seat_monthly": micros|None}} plus a user_id -> seat_start map."""
```

It uses `budgets_service.budgets_for_scopes` (already one round-trip), `_teams_meta` for member
counts, and `aggregate.fetch_seat_starts`.

- **`_team_summary(team_id, meta, rows, pr_rows, budget_window_usd_micros, config)`**: the
  parameter becomes the **window-scaled** budget in micros. Set
  `budget_pct = aggregate.budget_pct(cost_value, budget_window)` and
  `over_budget = budget_pct is not None and budget_pct > 1.0`. This fixes §0.4 in every caller
  at once: `org_overview`, `teams_table`, `team_detail`.
- **`org_overview`**: the headline `budget_pct` uses `window_budget_usd_micros(org_budget, frm,
  to, config.budget_period)`. Per-team budgets are scaled the same way before `_team_summary`.
  `teams_over_budget` follows automatically. `vs_last_pct` is unchanged.
- **`teams_table`**: drop the `allowances` fetch and the `utilization_pct` item key. Remove
  `"utilization_pct"` from `_TEAMS_SORT_FIELDS` (line 261). It duplicates the now-scaled
  `budget_pct`, and no screen sorts by it (`grep` of `src/webui/src/screens` confirms).
- **`_person_ranked(user_id, meta, rows, pr_rows, seat_allowance_usd_micros, in_grace, frm, to, config)`**:
  - `usage_pct = cost_value / seat_allowance` when the allowance is set
  - `low_use = usage_pct is not None and usage_pct < config.utilization.under and not in_grace`
  - `status = aggregate.person_status(recoverable, cost_value, frm, to, config.utilization)`
  - new keys: `recoverable_pct` (from `aggregate.recoverable(...)["recoverable_pct"]`, already
    computed) and `seat_allowance: Money`. The UI shows both, so a flag is explainable.
- **`team_detail`**:
  - `user_ids = sorted(set(member_ids) | by_user.keys() | by_user_prs.keys())`. Idle members
    appear with $0 (fixes §0.6); non-members with activity (a moved person) stay listed as today.
  - The seat allowance applies only to `member_ids`; others get `None`.
  - Delete the `allowances_for_scopes` call (line 409) and the token `allowance_per_seat`.
  - `highlights.low_use_count` / `room_to_save_count` count over the new `people`.
- **`org_usage`, `_team_usage`, `_person_usage`**: switch to cost.
  - `_team_usage(team_id, meta, rows, members, team_window_usd_micros, seat_ctx, frm, to, config)`
    returns `spend` (Money), `tokens_used` (info), `allowance` (Money: window budget),
    `utilization_pct = spend / window budget`, `status`, and `seats_under_threshold` via the new
    aggregate function over **members** (`directory.members_of(team, include_subteams=False)`).
  - `_person_usage` mirrors it per person: `spend`, `tokens_used`, `allowance` (Money: prorated
    seat allowance), `utilization_pct`, `status`, `in_grace`.
  - Spotlight: `people` built from team **members** (not only active users), sorted by
    `utilization_pct` desc, `None` last.
  - `teams` is built from teams that have members **or** activity. Today it is built from
    `by_team.keys()`, activity only. Change it to `team_ids_for_org`-style enumeration via
    `_user_team_map` plus the team roster, so a fully idle team still appears as "under".
  - Docstring: "Cost/utilization framing" replaces "Token/utilization framing".
- `_worth_a_look` `TODO(A1-findings)`: unchanged; it's out of scope.

**`src/server/api/manager/budgets.py`**:
- Module docstring: "F1: budgets".
- `get_budget`: drop the `get_allowance` read and the `limit_tokens` key.
- `put_budget(s, catalog, scope_type, scope_id, period, amount_usd)`: drop the `limit_tokens`
  parameter.
- Delete `put_allowance` and `allowances_for_scopes`.

**`src/server/api/manager/cli_budgets.py`**:
- Remove allowance handling. If the source YAML contains a non-empty `allowances:` key, exit 2
  with: `token allowances were removed (cost-based seats, 2026-09-26); a seat's allowance is now
  its team budget / members -- delete the 'allowances:' section`.
- Update the module docstring's source-shape example.
- `utils/budgets_import.sh` comment line 2: "budgets" only.

**`src/server/api/app.py`**: `put_budget` route (lines 218–226) calls
`budgets_service.put_budget(..., Decimal(body.amount_usd))` with no `limit_tokens`.

### 3.5 Single-user (My usage)

**`src/server/api/single_user/service.py`**: replace `_my_budget` with

```python
def _my_seat(s, catalog, config, user_id, frm, to) -> dict | None:
    """The viewer's own seat: their primary team's monthly budget / members, scaled to the
    window and prorated from their seat start (cost-based seats plan §2). None with no team
    or no team budget (D4) -- never a fabricated limit."""
    # -> {"team_id", "seat_monthly": Money, "allowance": Money (window), "usage_pct", "status", "in_grace"}
```

It uses `directory.team_of` (primary first, as today), `manager_aggregate` helpers and
`budgets_service.budgets_for_scopes`. `overview()` sets `"seat": _my_seat(...)` in place of `"budget"`.

**`src/server/api/schemas/single_user.py`**: delete `UserBudget`; add
`class UserSeat(BaseModel)` (`extra="forbid"`) with the fields above; `Overview.budget` becomes
`Overview.seat: UserSeat | None = None`. Update the `Overview` docstring.

### 3.6 API schemas and generated artifacts

**`src/server/api/schemas/manager.py`**:
- `BudgetResponse` / `BudgetWriteRequest`: remove `limit_tokens`. `extra="forbid"` stays, so a
  stale client sending it gets a 422 (intended).
- `TeamUsage`: replace `allowance_tokens: int | None` with `allowance: Money` (window budget); add
  `spend: Money`; keep `tokens_used: int`; `utilization_pct` / `status` / `seats_under_threshold`
  keep their names with new semantics.
- `PersonUsage`: same swap plus `in_grace: bool`.
- `TeamTableRow`: remove `utilization_pct`.
- `PersonRanked`: add `recoverable_pct: float | None = None`, `seat_allowance: Money`,
  `in_grace: bool`.
- Rewrite docstrings mentioning token allowance (`PersonUsage`, `SpotlightTeamUsage`).

Regenerate, in order, and commit both:
1. `utils/export_openapi.sh` → `src/server/api/openapi.json`
2. `utils/gen_api_types.sh` → `src/webui/src/data/api-types.ts`

Both have `--check` modes, which CI and §5 run.

### 3.7 Web UI: `src/webui/src`

Copy rules: lexicon-safe (`npm run lexicon`). Never "waste", never "limit exceeded"
(iteration plan D). Money through `fmtMoneyMicros`.

- **`screens/TeamScreen.tsx`**
  - Hero "Low seat use" sub-text: `of {members} seats`.
  - People table:
    - "seat usage" column = `usage_pct` of the dollar seat allowance. Tooltip/title:
      `${fmtMoneyMicros(spend)} of ${fmtMoneyMicros(seat_allowance)} this period`.
    - "Recoverable" column shows `fmtMoneyMicros` plus `fmtPct(recoverable_pct)` beneath.
    - The Status chip adds `"New seat"` (muted) when `in_grace` and not room-to-save.
  - Chart section "Seat utilisation per person": sub-text `Spend vs seat allowance`; the
    `ReferenceLine` label becomes `seat allowance`.
  - The `usage_pct ?? 0` default in `usageChartData` (line 46) is **wrong** for `None`: filter
    those people out of the chart instead of plotting 0%.
- **`screens/UsageScreen.tsx`**
  - Section title: "How teams use their budget". Sub-text: "Spend vs this period's share of the
    monthly budget".
  - Bars use `spend.usd_micros`; `ReferenceLine y` = `allowance.usd_micros`.
  - Team table columns: Spend | Allowance ($) | Utilisation. Tokens move to a secondary muted line.
  - Spotlight rows: the ProgressBar is `value=spend max=allowance`.
  - Headline copy (lines 44–48) stops saying "token allowance": `... using the most of their
    budget; N teams are well under theirs.`
- **`screens/MyUsage.tsx`**
  - Read `overview.seat` instead of `budget`: `limitUsd = seat.allowance`, `pct = seat.usage_pct`.
  - Heading: "You've spent X **this period**" (not "this month", since the window is the selected
    timeframe).
  - Stat strip: "seat allowance (this period)" and "seat per month"; drop the "token limit" stat.
- **`screens/ConfigureScreen.tsx`**
  - Delete the "Token allowance (optional)" input, its state and its validation (lines ~58–90,
    130–142). `putBudget` sends `{ amount_usd }` only.
  - Explainer text (line 98): "Set the monthly spend budget for the org, a team, or a project. A
    team's budget divided by its members is each seat's allowance."
- **`shell/timeframe.tsx`** line 53: "budget thresholds" (drop "/allowance").
- **`data/source.ts`** line 115 comment: drop `limit_tokens`. The `putBudget` body type comes from
  the regenerated `api-types.ts`.
- **`data/mock-source.ts`**: re-derive the fixtures from the same `fx` data so mock and API
  modes render identically (the U5 rule):
  - `seat allowance = team.budget / team.members`, window-scaled with the same D9 formula
  - person spend from `fx` people's cost
  - remove every `tokenLimit` / `allowance_tokens` / `limit_tokens` (lines 137–155, 189, 216,
    233–234, 584, 623–645)
- **`data/mock.ts`**: keep `tokensUsed` (informational). Remove `tokenLimit` where no longer read
  (typecheck will point at each). Lines 500 and 547 are canned chat answers and can keep their
  prose.

### 3.8 Synthetic generator and loader

**`econ-forecast/src/forecast/synthetic/org/config.py`**: delete `AllowancesConfig` (line 316) and
`OrgConfig.allowances` (line 329). `Strict` models reject unknown keys, so an old config with
`allowances:` now fails validation with a clear pydantic error (intended).

**`econ-forecast/src/forecast/synthetic/org/models.py`**: delete `AllowanceRecord` (line 274).

**`econ-forecast/src/forecast/synthetic/org/expand.py`**: delete `_build_allowances` (lines
262–272), its import (line 40) and the `allowances.jsonl` write (line 182). The manifest then no
longer lists the file.

**`src/server/storage/synthetic_load.py`**:
- Drop `put_allowance` from the import (line 29) and `"allowances.jsonl"` from `file_names`
  (line 225).
- Replace lines 353–357: if `allowances.jsonl` exists and is non-empty, log
  `synthetic_load.allowances_ignored` with the row count and continue. The dataset already on
  prod ships this file with 0 rows and must still load.
- `synthetic_load.budgets` log: drop the `allowances=` field.

**Docs** (`econ-forecast/docs/design/synthetic-data-generation.md`,
`synthetic-org-expansion-design.md`): replace the allowances sections with one paragraph saying
seat allowances derive from team budgets (link this plan). `docs/plan/2026-09-18-synthetic-org-implementation.md`
line 177: annotate `allowances` as removed by this plan.

---

## 4. Tests

Run the smallest set per step (CLAUDE.md "Verify"), then the full suite before merge.

**`tests/server/api/test_manager_aggregate.py`**
- `test_budget_pct_*` (lines 406–414): rewrite to micros and window budgets. Add
  `test_window_budget_scales_by_window_days` (15d = half, 60d = double of 30d) and
  `test_window_budget_none_without_budget`.
- `test_person_status_room_to_save_threshold` (line 586): replace with
  - `test_room_to_save_needs_share_and_floor` (share 12% and above the floor → flagged; share 12%
    with $3 recoverable over 30d → not flagged; share 8% with $200 → not flagged)
  - `test_room_to_save_floor_scales_with_window` ($6 recoverable, 15% share: flagged at 15d, where
    the floor is $5; not at 30d)
  - `test_room_to_save_none_with_zero_spend`
- `test_seats_under_threshold_*` (lines 455–462): replace with
  `test_seats_under_counts_idle_members` (a member with no rows counts),
  `test_seats_under_respects_grace`, `test_seats_under_none_without_any_allowance`.
- New: `test_seat_allowance_prorates_from_seat_start`, `test_seat_allowance_none_when_seat_starts_after_window`,
  `test_in_grace_boundary` (exactly 14 days → not in grace), `test_window_days_rejects_empty_window`.

**`tests/server/api/test_manager_service_team_detail.py`** (fixtures at lines 64–95):
- Fixtures set a **team budget**, not an allowance. Rewrite `test_room_to_save_count_matches_...`
  and `test_low_use_flag_...` to the new rules with explicit `frm`/`to` (30 days).
- New: `test_idle_member_is_listed_and_low_use`, `test_new_member_in_grace_not_low_use`,
  `test_person_without_team_budget_has_no_usage_pct`.

**`tests/server/api/test_manager_service_org_usage.py`**
- `test_low_seat_flag_and_utilization_status` (line 50): cost-based.
- `test_spotlight_team_is_the_over_allowance_one...` (line 102): "over" by spend vs window budget;
  spotlight people include an idle member.
- New: `test_idle_team_appears_as_under`.

**`tests/server/api/test_manager_service_tables.py`** (line 94): assert `utilization_pct` is gone
and `budget_pct` is window-scaled (15d window, $3,000 budget, $1,500 spend → 1.0).

**`tests/server/api/test_manager_service_org_overview.py`** (lines 58, 110): over-budget uses the
scaled budget. Add `test_headline_budget_pct_is_window_scaled`.

**`tests/server/api/test_manager_budgets.py`**:
- Delete `test_allowance_independent_of_budget` (line 108).
- `test_budget_round_trips` asserts no `limit_tokens` key.
- New: `test_cli_rejects_allowances_section` (calls `cli_budgets.main(["--source", tmp])`, expects exit 2).

**`tests/server/api/test_service_overview.py`**: `test_overview_budget_reads_the_users_own_team`
(line 331) becomes `test_overview_seat_is_team_budget_per_member_window_scaled`. Add
`test_overview_seat_none_without_team`.

**`tests/server/api/test_budgets_migration.py`**: extend to run the new revision up and down,
asserting that `allowances` is absent after upgrade and present after downgrade.

**`tests/server/storage/test_schema_metadata.py`**: drop the `allowances` reference.
**`tests/server/storage/test_schema_dump.py`**: passes after the golden regeneration (§3.2).

**New `tests/server/api/test_api_config_utilization.py`**: loads `config/api.yaml` via
`load_config` and asserts `utilization.room_to_save.min_share == 0.10`, `min_usd_per_30d == 10`,
`new_seat_grace_days == 14`, and that `room_to_save_usd_micros` is **not** a field of
`UtilizationConfig` (guards the silent-ignore trap, §3.1).

**Synthetic**:
- `tests/server/storage/test_synthetic_load_unit.py`: a dataset with a non-empty `allowances.jsonl`
  loads and logs `allowances_ignored`.
- `econ-forecast/tests`: an org config containing `allowances:` fails validation; expand output
  no longer contains `allowances.jsonl`.

**Web UI** (`src/webui`): `npm run typecheck`, `npm test`, `npm run lexicon`, `npm run build`.
Add a vitest for the TeamScreen chart filter: people with `usage_pct: null` are excluded rather
than plotted at 0.

---

## 5. Verification

1. Unit and service tests: `utils/test_all.sh -m "not slow"` (the full non-slow suite; the
   known pre-existing failure `tests/server/test_otel_e2e.py::test_both_signals_join_into_one_cost_block`
   is unrelated).
2. Generated artifacts are in sync: `utils/export_openapi.sh --check` and `utils/gen_api_types.sh --check`.
3. Migration against a real database: on the local stack, run `python -m server.storage.cli_db
   --config config/storage.yaml migrate` against `econ`. Then `\dt allowances` shows nothing, and
   `status` shows the new head.
4. Live check on synthetic data (local stack; load `econ_synth` from
   `econ-forecast/artifacts/org-demo/org` as in `docs/deployment.md` §0.2), viewer `github:vp-eng`,
   explicit window `from=2026-08-23T00:00:00Z&to=2026-09-22T00:00:00Z`:
   - `/v1/teams/{development,platform,qa}`: `usage_pct` is non-null for every member and
     `low_use_count` > 0 for at least platform and qa. The measured shape on prod data for a
     comparable window: development about 115% average seat usage (4 low, 16 over), platform 50%
     (4 low, 2 over), qa 36% (3 low, 0 over).
   - Cross-check one person by SQL: their spend ÷ (team budget ÷ members × window_days ÷ 30)
     equals the API's `usage_pct` to 4 dp.
   - `room_to_save_count == 0` on every team is **correct** for this dataset (max share 5.3% < 10%).
     Record it in the PR description so nobody "fixes" it.
   - `/v1/org/overview?period=15d`: headline `budget_pct` is about 2× what it was before the change.
5. Browser pass (web UI in `api` mode against the local stack):
   - Team screen: seat column, chart and chips.
   - Usage screen: dollar bars and spotlight.
   - My usage: the seat block and "this period" wording.
   - Configure: no token field.

## 6. Rollout to prod (`cc-econ-demo`)

1. Merge, then on the host: `git pull`, then `utils/aws_up.sh` (rebuilds the images).
2. **Run the migration explicitly.** `init` only creates *fresh* schemas and reports "exists" on
   an existing one (`dbm/operations.py::init`), so it will **not** drop `allowances` on `econ`:
   ```bash
   docker compose --env-file .env.aws -f docker/docker-compose.aws.yml run --rm --no-deps api \
     python -m server.storage.cli_db --config config/storage.yaml migrate
   ```
   `econ_synth` is migrated by the synthetic loader itself (`synthetic_load.py:241`) on its next
   reload. Until then its unused `allowances` table is harmless, because no code reads it.
3. Real data needs **team budgets** for seats to appear (today `econ` has none). Set them via
   Configure or `utils/budgets_import.sh`. This depends on the separate identity fix (people must
   be on teams first).

## 7. Out of scope (noted, not done)

- A per-seat dollar override (D2 leaves room: a `seat_budgets(user_id, amount_usd, period)`
  table would replace `seat_monthly` for that user).
- A team-membership join date (would replace `users.created_at` in `seat_start`).
- `_worth_a_look` findings for seat under-utilization (`TODO(A1-findings)`).
- Regenerating the synthetic org with poorer cache habits so room-to-save fires
  (`econ-forecast/config/synthetic/org/org.yaml:103`). That's a data choice, not a code change.
