# Brief — Overview refresh, Track B (Overview API)

You implement Track B of `docs/plan/2026-10-14-overview-ux-refresh.md` (rev 3, §4 and §0).
Read the plan's §0, §2, §4 first, then `src/webui/AGENTS.md`, `src/server/api/schemas/README.md`
and the root `CLAUDE.md`. Work ONLY in this worktree
(`/Users/anil/workspace/Talium/code-econ/.claude/worktrees/overview-refresh-b`, branch
`feat/overview-refresh-b`). Never touch another checkout.

## Steps (strict order; the coordinator releases one at a time)

1. **B4** Drop `initiatives_trend`: the field and schema, `aggregate.initiatives_trend`, and the
   `issues_started_weekly` / `issues_shipped_weekly` calls in `org_overview`. First grep for any
   other caller of those two queries (`config/queries/manager.yaml`); delete a query only if no
   other caller. Drop `InitiativesTrend` from `src/webui/src/components/charts.tsx` and the mock.
   (Front-end follow-through for `Overview.tsx` is minimal: remove the section that rendered it
   ONLY because the plan's §0 says "Shipping vs new work: Removed"; nothing else on the screen.)
2. **B3** `agent_spend` on the precedence chain: group `rows` by agent, resolve each group's
   cost via `precedence.resolve_aggregate` with `config.dashboard.precedence.cost` (the same
   chain `_cost()` uses). `sum(agent_spend) == headline.spend.usd_micros`. Check other callers
   of `aggregate.spend_by_agent` before changing its signature.
3. **B1** `daily_spend: [{day: date, spend_usd_micros: int}]` on `OrgOverview`. Elapsed days only
   (window start .. min(window end, now), inclusive of today, UTC days), missing days = 0. Resolve
   each row through the chain (per row), bucket on the UTC day of `max(started_at, from)`. The
   sum must equal `headline.spend.usd_micros` exactly. Remove `monthly_spend` and the SQL
   `scope_monthly_spend` (grep other callers first).
   Note: sessions whose bucket day falls outside the elapsed range (e.g. started after `now`)
   must still be counted so the sum reconciles: clamp them into the last elapsed day. Say in your
   report if you had to do this.
4. **B2** `agent_task_mix: [{agent, type, count}]`: root sessions (`is_root`) per (agent,
   `session_type`), from the same `rows` as `session_type_counts`; rows with no `session_type`
   are omitted. pytest: summing `count` per type equals `session_type_counts`.
5. **B5** Regenerate `src/server/api/openapi.json` (`src/server/api/cli_export_openapi.py`)
   and `src/webui/src/data/api-types.ts` (`utils/gen_api_types.sh`; NEVER hand-edit it). Update
   `src/webui/src/data/mock-source.ts` (deterministic daily series summing to the mock headline
   spend, plus a plausible agent task mix; remove monthly_spend / initiatives_trend), update
   `schemas/manager.py` docstrings and `schemas/README.md`. Fix the front end so
   `npm run typecheck`, `npm test`, `npm run lexicon`, `npm run build` (in `src/webui`) pass. The
   Overview screen must still render today's layout: wherever it used `monthly_spend`, adapt it
   MINIMALLY to the new `daily_spend` (e.g. aggregate to months) or leave a TODO(Track C) —
   Track C replaces that section later; do NOT redesign anything.

## Rules

- Budget/cost: one cost source. Dollars via the precedence chain only.
- pytest uses the real DB: `export TALIUM_TEST_DSN=postgresql+psycopg://postgres@localhost:5432/econ_test`
  (the fixture DROPS all tables on that DSN: never point it at econ_synth or any other DB).
  Python: `/Users/anil/workspace/Talium/code-econ/venv/bin/python -m pytest …` from the worktree root.
  Relevant tests: `tests/server/api/test_manager_service_org_overview.py`,
  `test_app_org_overview.py`, `test_manager_aggregate.py`, plus any you add. Add unit tests for
  the new aggregate helpers and service tests for the reconciliation invariants.
- Never `git -C`. Commit with conventional messages (`feat(api): …`, `refactor(api): …`),
  ONE commit per step, only when the coordinator says so (or stage and report; the coordinator
  commits after review). No Claude/Co-Authored-By references in commits or code.
- Leave a `TODO` in source for anything deferred; remove it when done.
- Structured JSON logging per CLAUDE.md applies to new modules (none expected here).
- Stop and report (do not decide) if: `resolve_aggregate` cannot be split per row additively;
  you would need to remove/move/hide anything the plan's §0 does not explicitly cover; a
  requirement is ambiguous.
- Do not edit `/Users/anil/workspace/Talium/talium-lovable-1.0` (read-only reference).

## Handshake

Before writing any code: read the above, then reply with (a) whether the brief makes sense,
(b) every ambiguity/concern/wrong assumption you found (cite files), (c) your intended approach
per step in 2-3 lines each. Do not write code until the coordinator replies "go".
