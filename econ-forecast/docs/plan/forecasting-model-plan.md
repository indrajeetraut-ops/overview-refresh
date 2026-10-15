# Forecasting model — implementation plan

## Context
Implements [forecasting-model-design](../design/forecasting-model-design.md): data-driven **ETS**, a
**dated known-event overlay**, and the structural model (Org→Team→Project tree, one team per project; agent
orthogonal). The design is authoritative for behavior; this plan is the *how / order / which files*.

**Process:** implement only after the user enters implementation mode; then follow the `talium-impl-workflow`
skill (sonnet subagent, pre-coding handshake, worktree, tests + persisted report, coordinator Major/Minor
review).

**Approach — implement a bit, validate, repeat.** Each implementation phase ships with **basic acceptance
tests only** — the minimum to prove the code is correct — so we can experiment before committing to a large
build. The **exhaustive scenario suite** and the **one-time example rebuild** are kept off the critical path
as their own phases (below), scheduled when warranted.

## Guiding constraints
- **All artifacts are regenerable and disposable.** Synthetic datasets, forecast runs, curve artifacts, and
  plot outputs may be deleted and recreated; nothing to preserve.
- **Examples are recreated with correct configs** — the synthetic configs must be fixed to
  **one team per project** (today they reuse a project name across teams, violating the design).
- **CLIs gain `--force`** so recreation doesn't fight the "refuse existing output dir" guard.

## Phase 0 — `--force` on all CLIs (cross-cutting, model-agnostic)
Every CLI refuses an existing/non-empty output dir. Add an opt-in `--force`.
- **Files:** `synthetic/cli_synt_gen.py`, `forecasting/cli_forecast.py`, `curve/cli_curve.py`,
  `utils/cli_plot_forecast.py` (+ their runners' output-dir guards; `utils/run.py`).
- **Behavior:** without `--force`, unchanged (refuse). With `--force`, clear the target output directory
  (only the intended output path; never a parent or outside it) before writing, then proceed. Config-mode
  (`plot_forecast --config`) honors it too.
- **Wrappers** pass args through already; no change beyond docs.
- **Docs:** document `--force` in each CLI `--help` and the READMEs (see README list).

## Phase 1 — known-event overlay (design §7)
Model-agnostic; can land on the interim model before ETS.
- **`forecasting/config.py`:** add optional `known_events` (org / teams / projects / agents), each with
  `start_date`, `end_date`, `multiplier`, `spread`. Validation per §7 (dates in horizon, `multiplier ≥ 0`,
  `0 ≤ spread ≤ multiplier`, names resolve, ≤ 1 per level).
- **New `forecasting/known_events.py`:** resolve each series' effective per-day multiplier (scope precedence
  tree × global agent), draw the coherent per-path factor over each window, apply to the `(days × samples)`
  paths. No parent roll-up (that is Phase 3).
- **`forecasting/runner.py`:** call the overlay after path simulation, before allocation; record applied
  events in `diagnostics.json`.
- **Examples:** add a known-events forecast example (a plots example that shows a visible planned spike).
- **READMEs:** forecasting README + operator guide + config template comments.

## Phase 2 — ETS revival (design §3, §5, §6)
- **Restore** `forecasting/ets.py` and `forecasting/paths.py` from git `ed59aff^`; re-add the `statsmodels`
  pin to `econ-forecast/requirements.txt` (and root if used by the venv).
- **`forecasting/config.py`:** `model: { method: ets, seasonal: auto|none, trend: auto|none }`; **remove**
  the `weekend:` block and `Weekend`/`WeekendOverride`.
- **`forecasting/runner.py`:** replace the `weekday_bootstrap` path with ETS fit → joint residual bootstrap
  (§5) → nonnegative correction (§6); wire `seasonal`/`trend` modes and the tiered fallback (§3);
  restore numerical controls (ref the retained preflight note).
- **Remove** `forecasting/weekday_bootstrap.py` (retired).
- **Config template** `config/forecasting/forecast.yaml`: `method: ets`, drop `weekend`, keep
  `max_negative_correction_ratio`.
- **Examples/artifacts:** regenerate all forecast runs and plot examples under ETS.
- **READMEs:** forecasting README + operator guide + config template comments.

## Phase 3 — examples & artifacts recreation (one-time; after Phases 1–2 basic acceptance passes)
Done **once**, only after implementation is in and basic acceptance tests are green — so we're not
regenerating heavy artifacts while the code is still moving.
- **Fix synthetic configs to one team per project** — revise `project_mix` in `examples/synthetic/*.yaml`
  (`baseline.yaml`, `dated-org-release*.yaml`, `dated-team-model-release.yaml`, `dated-two-week-release.yaml`,
  `curve-guided.yaml`) so each project name belongs to exactly one team; no project appears under two teams.
  Reconcile the roster/users if needed.
- **Delete and regenerate** every committed example artifact: synthetic datasets (gitignored), the
  `examples/plots/*/forecast` runs and `plots/` outputs, and curve artifacts if any anchor/schema changed.
  Use `--force` to recreate into fixed paths.
- **Recreate the plots examples** end to end (synth → ETS forecast → plots), including a known-event example.
- **Weekend is now OFF by default** (`weekend_reduction_probability` defaults to `0`; landed early). The
  example set must therefore deliberately show both: one config that opts into a **weekend drop**
  (`weekend_reduction_probability > 0`) and one with a **dated spike** (a known event and/or a curve that
  carries a spike), so both weekly-seasonality and planned-spike behavior are demonstrated.

## Deferred — parent attribution
Not planned here (its own plan when scheduled). Roll a sub-scope event up the tree (project → its team →
org; team → org) via share-based propagation or bottom-up coherent forecasting; the `known_events` config
surface does not change.

## README / doc updates (consolidated)
- `src/forecast/forecasting/README.md` — ETS, known events, statuses, artifact layout, `--force`.
- `src/forecast/synthetic/README.md` — one-team-per-project; `--force`.
- `src/forecast/utils/README.md` — `--force`.
- `src/forecast/curve/README.md` — `--force`.
- `docs/operations/README-forecast.md` — full workflow with ETS + known events + `--force`; drop weekend.
- `examples/synthetic/README.md`, `examples/plots/README.md`, `examples/curve/README.md` — regenerated
  numbers, fixed configs, known-event example.
- `config/forecasting/forecast.yaml` — comments for `method: ets`, `known_events`, no weekend.
- `docs/design/synthetic-data-generation-design.md` — reflect one-team-per-project (structural reconciliation).

## Testing — two tiers

### Tier 1 — basic acceptance (ships with each implementation phase; gates progress)
The **minimum to prove the code is correct**, written alongside Phase 0/1/2. A recorded green run via
`utils/test_forecast.sh` gates moving to the next phase. Enough to validate an implement→validate loop, not
exhaustive:
- **Phase 0 `--force`:** refuse-by-default preserved; `--force` clears only the intended output dir and never
  escapes it.
- **Phase 1 known events:** overlay math (per-path coherent, no washout); scope precedence tree; agent global
  composition (multiplicative); `spread` band-fan; validation; provenance recorded.
- **Phase 2 ETS:** a fit runs and selects the right tier/fallback; `simulate(anchor="end")` shape + replay;
  nonnegative correction; allocation reconciliation; correct statuses; one end-to-end smoke run.
- **Cross-cutting invariants:** one-team-per-project; agent = Σ model forecasts (exact); total-token identity.

### Tier 2 — exhaustive scenario testing (separate; scheduled when necessary)
The full design-driven acceptance surface — **kept off the critical path** and scheduled on its own. Revive/
adapt the retained [acceptance matrix](../notes/forecasting-acceptance-matrix.md) (HIST/ETS/PATH/ALLOC/
RESULT/STORE-*, PLOT-01..10, plus known-event scenarios), coverage/outage matrices, numerical rejection
paths, analytic constant-series branch, edge cases, and cross-platform tolerances. Its own
`forecasting-model-test-brief.md` and persisted runs. **Not required for early experimentation.**

## Sequencing
Each of Phases 0–2 is an **implement → validate (Tier-1 basic acceptance) → review** loop; don't start the
next until the current phase's basic acceptance is green.
1. **Phase 0** (`--force`) + basic acceptance — unblocks recreation; small, model-agnostic.
2. **Phase 1** (known-event overlay) + basic acceptance — lands the planned-spike capability.
3. **Phase 2** (ETS revival) + basic acceptance — the model change.
4. **Phase 3** (examples & artifacts recreation, one-time) + README updates — only after 1–3 are green.
5. **Tier-2 exhaustive scenario testing** — scheduled separately, when warranted.
- Parent attribution: later, on its own plan.

## Risks / watch-items
- **statsmodels fragility** returns with ETS (convergence/finite handling); restore the preflight discipline,
  keep the tiered fallback.
- **`--force` is destructive** — constrain deletion strictly to the resolved output directory; test it.
- **One-team-per-project** may require reworking example rosters/project sets; keep the change minimal and
  internally consistent across synth/forecast/plot examples.
- **Confirm before Phase 2:** no real-data ETS fit failure beyond the (now-solved) spike-visibility motive.

## Out of scope
Phase 3 parent attribution; the full reporting module; budget/scenario math; any change to the daily-usage
input contract or the saved-artifact reader beyond additive fields.
