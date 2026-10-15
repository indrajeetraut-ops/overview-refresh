# Phase PR — S1 rhythm zone (Z0): Implementation Plan

**Goal:** add a rhythm zone to S1 — four tiles (active days, longest streak, peak hour,
favorite model) plus a 52-week activity heatmap — and trim the zones below it, so the overview
opens with facts about the user's working pattern rather than eight aggregate quantities.

**Why now:** S1 currently leads with eight tiles of the same kind of fact. The zone added here
renders in **every datasource mode including `otel_only`**, which today collapses to Z1+Z2 and
looks abandoned — so this is a mode-ladder improvement, not only a visual one.

**Spec set (read before starting a task — do not improvise against the spec):**
- Visual contract: `docs/design/mockups/dashboard-s1-rhythm-concept.html` — the Z0 markup,
  tile treatment, heatmap geometry and level ramp. Existing screens: `dashboard-ux-mockup.html`.
- Feasibility, with the seams verified and the traps named:
  `docs/design/notes/2026-08-07-s1-rhythm-feasibility.md` (**FEAS**) — **read §2, §4, §7b before
  writing any query.** Three of this plan's tasks exist only because of what is in there.
- Screens, zones, component inventory, five UI states: `docs/design/dashboard-ux-blueprint.md` (**BLUE**)
- Mode capability matrix, `unavailable` contract: `docs/design/dashboard-datasource-modes.md` (**MODES**)
- Tone lexicon: `docs/design/session-storytelling.md` §2 (**STORY**), checklist mirrored at
  `src/server/api/static/js/lexicon.md`
- Parent plan and ground rules: `docs/plan/2026-08-01-user-dashboard-implementation.md` (**PLAN**) §0

**Depends on:** P2 (API) and P3 (SPA), both landed. **Status:** planned 2026-08-07.

**Decisions taken (do not re-litigate):**
1. **Longest streak only** — no "current streak" tile. `Current streak: 0` greeting someone
   returning from a break is the one framing here that edges toward a verdict (STORY §2).
2. **Heatmap encodes tokens per day.** No metric toggle. Sessions still ride in the tooltip —
   it is the same `GROUP BY` and answers "one long day or five short ones?".
3. **Timezone is deferred.** See ground rule 1.

---

## 0. Ground rules specific to this phase

PLAN §0 applies unchanged. Five more:

1. **UTC bucketing is a known, accepted limitation — not an oversight.** There is no user
   timezone anywhere in the system (no column, no config key, no param) and adding one waits
   for a user table. Every day boundary and every hour bucket in this phase is UTC.
   - Per CLAUDE.md, this needs a **`TODO(rhythm-tz)` comment at every bucketing site** —
     the named query, the streak/active-day computation, and the peak-hour bucketer — each
     naming what changes when a user table lands. Remove them in that phase, not before.
   - **Expect this to look like a bug during development.** A developer in IST (+5:30) will
     see late-evening sessions land on the next day's cell and a peak hour shifted by five
     and a half hours. That is this limitation, working as specified. Do not "fix" it locally.
   - No user-facing caption for it this phase.
2. **Z0 renders in every mode.** It is not gated, and it never ships the `unavailable` state.
   Peak hour degrades through a precedence chain instead (R3) — a real number with a named
   source, which is a different thing from an absent capability (MODES §5 does not apply).
3. **The heatmap draws from `--seq250…--seq600` only.** BLUE §7 pins `--s1…--s6` to provenance
   origins globally; a heatmap borrowing them would make two unrelated legends share hues.
   Sequential is correct here anyway — this is magnitude, not identity.
4. **Level steps are quantiles of non-zero days, never linear.** Tokens/day is heavy-tailed
   enough that a linear split paints most of the year level-1 and says nothing.
5. **Tone lexicon applies to every new string**, including the peak-hour basis caption. Review
   is a merge gate, not a style pass.

---

## R0 — Correct the sub-agent session count (do this first, land it separately)

**Touches:** `config/queries/dashboard.yaml`, `src/server/api/service.py`

A pre-existing defect, not caused by this feature: sub-agent sessions get their own `sessions`
row carrying the same `user_id` (`fold_handler.py:57`), and `sessions_in_range` has no root
filter — so today's Z1 "sessions" tile counts a session that spawned 12 sub-agents as 13.

> **Do not "fix" this with `AND s.parent_session_id IS NULL`.** Reviewed and rejected
> 2026-08-07; the reasoning is easy to re-derive wrongly, so it is recorded here in full.
>
> The fold *does* roll sub-agent **tokens** up — `info["inclusive_tokens"] = own + Σ children`
> ([driver.py:177](src/server/worker/fold/driver.py:177), asserted in `test_driver.py:192`) —
> which makes "children are already folded into the parent, so drop their rows" a reasonable
> first instinct. It is wrong for two reasons:
>
> 1. That rollup lands in a **different key than the dashboard reads.** `_recompute` derives
>    `models` and `tokens_total` from own events only (the variable is named `own_tokens`,
>    [driver.py:207](src/server/worker/fold/driver.py:207)), and `rollup_subagents` adds
>    `inclusive_tokens` alongside them without overwriting either. Every rung of the
>    precedence chain reads own-only fields.
> 2. **There is no inclusive cost figure anywhere in the system.** The per-child entries in
>    the `subagents` map carry tokens, duration, purpose, agent_type, origin, depth and model
>    — no cost — and `cost_effective_usd_micros` belongs to the true-up worker, a different
>    writer from `rollup_subagents`, with the boundary deliberately structural
>    (`schema/session_info.py:34-48`). Drop the child rows and their dollars leave the system
>    with no key to recover them from. Same for tools, files, skills, MCP and active time:
>    all own-only per row, none merged upward.
>
> Today's totals are correct *because* children are summed alongside parents. Keep every row;
> change only what is counted. Making the filter approach correct would mean promoting
> inclusive tokens, inventing an inclusive cost across two uncoordinated writers, merging
> inclusive tools/files/skills, and a full refold — a fold phase of its own, and one that
> breaks the mode story (`inclusive_tokens` is a JSONL-fold product; `otel_only` has none).

- [ ] Add `s.parent_session_id` to the `sessions_in_range` SELECT list. It is not currently
      selected, which is why this could not be expressed before.
- [ ] `_z1_headline`: `"sessions"` becomes a count over roots only. `"active_sessions"` too.
- [ ] `_top_repos`: same inflation, same treatment — count root sessions per repo.
- [ ] Leave `tokens`, `cost_usd_micros`, `active_time_s`, `_files_touched`, `_distinct_repos`
      and `subagents_spawned` summing over **all** rows. Each is already correct; a comment
      should say so, because the next reader will assume they were missed.
- [ ] Tests: a fixture with one parent + N children asserts sessions == 1 and tokens == the
      full parent+children sum. Both halves, or the regression comes back.

**Done when:** `tests/server/api/test_service_overview.py` proves a fan-out session counts once
and its children's tokens still land in the total. **Land as its own commit** — it changes a
shipped number and should be reviewable without the new zone attached.

## R1 — The daily activity query

**Touches:** `config/queries/dashboard.yaml`

One named query, `activity_by_day`, over a rolling window for one user.

- [ ] `GROUP BY s.started_at::date`, user-scoped, `WHERE s.started_at >= :from`. Return
      `day`, `tokens`, `sessions`.
- [ ] **`sessions` counts roots only** (`count(*) FILTER (WHERE s.parent_session_id IS NULL)`);
      **`tokens` sums every row.** Same asymmetry as R0, same reason.
- [ ] **Tokens must resolve in every mode** (FEAS §7b). `i.tokens_total` is JSONL-written and
      is `0` on every `otel_only` row — summing it alone renders an empty heatmap for an
      OTel-only deployment. Use the two-rung expression:
      - when `i.otel->'measures'` is present and non-empty, sum the four
        `i.otel->'tokens'->>'tokens_*'` values **cast `::numeric`** (they are decimal strings
        from `_fmt`, not integers — `::bigint` will fail);
      - otherwise `i.tokens_total`.
- [ ] Comment the query with: why the asymmetry exists, that this mirrors the top of
      `precedence.py`'s tokens chain, and that it **omits the `otel_ledger` rung** (only
      reachable by a ledger-only fleet with no metrics pipeline). An undocumented divergence
      from the precedence chain is how the two drift.
- [ ] `TODO(rhythm-tz)`: `::date` is a UTC day boundary; becomes `AT TIME ZONE` on the user's
      zone when a user table exists.
- [ ] Note in the comment: no index on `sessions.started_at` today. The `user_id` index carries
      this at v1 scale; a composite `(user_id, started_at)` is the fleet-scale follow-up.

**Done when:** the query returns ≤366 rows for a year, and a hand-built fixture with one
`otel_only` session and one `jsonl_only` session returns non-zero tokens for **both** days.

## R2 — `GET /v1/users/{id}/rhythm`

**Touches:** `src/server/api/service.py`, `src/server/api/app.py`

Its own endpoint, not a block on `/overview`: streaks and a 52-week window are lifetime-scoped
by definition, and a response carrying two different time scales would make
`aggregate_mode_block` describe the range while the heatmap describes a year (FEAS §6).

- [ ] `?weeks=52` (bounded, default 52). No `from`/`to` — this endpoint is deliberately not
      range-scoped, and accepting them would invite exactly the confusion it exists to avoid.
- [ ] Response: `{...aggregate_mode_block, active_days, days_in_window, longest_streak:
      {days, from, to} | null, days: [{day, tokens, sessions}]}`.
- [ ] Compute `active_days` and `longest_streak` in Python from the query's rows — a day is
      active if it has any session. Consecutive-day walk; no SQL window functions.
- [ ] `longest_streak` is `null`, not `0`, when there are no active days at all — the UI needs
      to distinguish "no data yet" from "a one-day streak" (envelope rule 3).
- [ ] Emit **every** day in the window including zero days, or the front-end has to reconstruct
      the calendar and will get the padding wrong.
- [ ] `TODO(rhythm-tz)` on the streak walk: day boundaries are UTC.
- [ ] Tests: empty user; single active day; a streak broken by exactly one gap day; a streak
      running to the last day of the window (must not be truncated by the window edge).

**Done when:** the endpoint answers for a user with no sessions without raising, and the streak
tests above pass.

## R3 — Peak hour + favorite model on `/overview`

**Touches:** `src/server/api/service.py`

Both are **range-scoped** and both come from rows `overview()` has already fetched — no new
query, no new round trip. Scoping peak hour to the selected range is also the better product
answer: a current habit beats a lifetime average that can never move.

- [ ] New `z0` block on the overview response: `{peak_hour: sourced(...), favorite_model: ...}`.
- [ ] **Peak hour is a precedence chain, not an `unavailable` capability** — model it on
      `ACTIVE_TIME_CHAIN` in `precedence.py`, which already solves this exact shape:
      - `turn_started` — bucket every `turns[].started` (root sessions, `jsonl_only`/`both`).
      - `session_started` — one point per session from `started_at` (`otel_only` fallback).

      Return it through `sourced()` so the UI can caption which rung won. Per-request OTel
      timestamps are **not** available: the ledger drops `event_ts` at fold time
      (FEAS §3) — do not go looking for them.
- [ ] Return the full 24-bucket histogram alongside the peak, not just the peak hour. The tile
      renders a micro-histogram, and re-deriving it client-side is impossible from one number.
- [ ] `favorite_model`: top model by tokens from `_model_split()`, which `_z2_consumption`
      already computes. **Call it once and pass it to both** — recomputing is the kind of
      duplication that drifts.
- [ ] Exclude zero-token models before ranking, the same filter Z2's bar already applies for
      Claude Code's `<synthetic>` marker requests.
- [ ] `TODO(rhythm-tz)` on the hour bucketer.
- [ ] Tests: an `otel_only`-only range resolves peak hour via `session_started` and reports
      that source; a `jsonl` range resolves via `turn_started`; a tie in the histogram picks
      deterministically (document which way).

**Done when:** both fields resolve in all three modes and the reported source is asserted per
mode.

## R4 — The `Heatmap` component

**New:** `src/server/api/static/js/components/heatmap.mjs`, styles in `components.css`

- [ ] 7 rows × ~53 columns, `grid-auto-flow: column`, 12px cells, 3px gaps. Lead-in blanks so
      rows are real weekdays. Geometry is in the concept mockup — port it, do not redesign it.
- [ ] Five levels: empty (`--grid`) + `--seq250/350/450/600`. Quantile-stepped over non-zero
      days (ground rule 4).
- [ ] Tooltip per cell: date, tokens, sessions. Zero days read "nothing recorded" — not "0
      tokens", which implies a measurement that did not happen.
- [ ] Outline the cells inside the currently-selected timeframe, so the range pill has a
      visible referent. Update on timeframe change **without refetching** (R6).
- [ ] Horizontal scroll in its own container below ~760px; never shrink cells below legibility
      (BLUE §7 responsive rule).
- [ ] `role="img"` + an `aria-label` summarising the window, and a legend. The lightest step is
      below 3:1 against the surface by design (low value = faint); the tooltip and legend are
      the required relief.
- [ ] Ships the five states via `state.mjs` — but **not** `unavailable` (ground rule 2).

**Done when:** it renders a full year in both themes, the tooltip tracks the cursor, and the
selected-range outline moves when the timeframe changes.

## R5 — Z0 tiles and the S1 re-layout

**Touches:** `js/screens/overview.mjs`, `js/components/stat-tile.mjs`, `css/components.css`

- [ ] **Restyle `StatTile`: label above the value, sentence case.** The current
      value-then-uppercase-label order is most of what makes the strip read as a metrics grid.
      Three CSS lines; `.tile .l` moves above `.tile .v` in the DOM.
- [ ] Z0 tile row: active days · longest streak · peak hour (with the 24-bar micro-histogram) ·
      favorite model. Quieter ground (`--band`, no border) than the Z1 row, so the two read as
      two kinds of fact rather than eight of the same.
- [ ] **Trim Z1 from eight tiles to four** — tokens, est. cost, sessions, active time. Turns,
      files, repos and sub-agents collapse into one caption line beneath.
- [ ] Peak-hour tile carries a basis caption when the resolved source is `session_started`.
      Name the source, not the user (MODES §5 rule 1) — this string is a lexicon gate item.
- [ ] Zone order: Z0 → Z1 → Z2 → Z3 → Z4 → Z5 → Z6. In `otel_only` the screen still ends after
      Z2, with **Z0 above it** — that is the whole point.

**Done when:** S1 matches the concept mockup in both themes, and the `otel_only` mock renders
Z0 + Z1 + Z2 + the source note and nothing else.

## R6 — Fetch scoping in the store

**Touches:** `js/store.mjs`, `js/screens/overview.mjs`

`app.mjs:88` re-dispatches the router on every state change, so a 7d→30d toggle re-runs
`renderOverview` and refetches everything. A heatmap mounted naively would refetch 52 weeks on
every toggle.

- [ ] Cache the `/rhythm` response keyed by `userId` (+ `weeks`), not by timeframe. Only the
      selected-range outline updates on a timeframe change — it is a re-render, not a refetch.
- [ ] Invalidate on `userId`/vendor change.

**Done when:** switching 7d→30d→90d issues exactly one `/rhythm` request in the network log.

## R7 — Z3 tabs

**Touches:** `js/components/tabs.mjs` (new), `js/screens/overview.mjs`

- [ ] Extract a small generic tab shell. `.s4tabs`/`.tabpane` in `components.css` is already
      generic and reusable as-is; `MetricsTabs` is **not** (its panes are S4-specific), so this
      is an extraction, not a reuse.
- [ ] Z3's four cards become one card with Tools / Skills / MCP servers / Repos tabs.
- [ ] Keyboard-reachable, `aria-selected`, arrow-key movement between tabs.

**Done when:** Z3 occupies one card, S4's tabs still work, and the tab strip is operable from
the keyboard.

## R8 — Docs

**Touches:** `docs/design/dashboard-ux-blueprint.md`, `docs/design/dashboard-datasource-modes.md`

- [ ] BLUE §2: add Z0 to the S1 zone diagram and the ordering rationale; update Z1's tile list.
- [ ] BLUE §8: add `Heatmap` and `RhythmTiles` to the component inventory, Modes column **all**.
- [ ] MODES capability matrix: same, and note Z0 as the one zone `otel_only` renders in full.
- [ ] Record the UTC limitation in both, pointing at FEAS §5 and the `TODO(rhythm-tz)` markers.

**Done when:** an implementation session reading only BLUE + MODES would build what shipped.

---

## Sequencing

```
R0 (standalone, land first)
     │
     ├──► R1 ──► R2 ──┐
     │                ├──► R4 ──► R5 ──► R6
     └──► R3 ─────────┘
                              R7 (independent)
                              R8 (with the last code commit)
```

R0 first and alone. R1→R2 and R3 are independent of each other and can run in parallel; both
must land before R5 has data to render. R7 touches no new data and can go any time.

## Risks

| Risk | Mitigation |
| :---- | :---- | 
| UTC bucketing looks like a bug to a non-UTC developer | Ground rule 1; `TODO(rhythm-tz)` at every site |
| The R1 token expression drifts from `precedence.py` | Comment naming the omitted rung; the R1 two-mode fixture fails loudly if either rung breaks |
| `tokens_total` stays JSONL-only forever | FEAS §7b option (c) — promote `tokens_effective` alongside `cost_effective_usd_micros`; not this phase |
| Heatmap's most recent cell is unstable pre-fold | `started_at` is `now()` between ingest and first fold (FEAS §2); self-correcting — do not animate the grid |
| R0 changes a shipped number | Separate commit, both-halves test |
