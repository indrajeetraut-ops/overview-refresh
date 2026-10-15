# Doc-rewrite brief — align docs with the ETS forecasting model

**Purpose:** Rewrite the docs that still describe the **retired `weekday_bootstrap`** model so they
match the current committed design. This is the remaining piece of Phase 3 (the example rebuild
already landed, `Refs #64`). Intended as a self-contained handoff — a fresh session can execute it
from the committed repo alone.

**Repo / state:** `/Users/sumandra/projects/talium/forecasting/code-econ`, branch `forecast`.
The forecasting model rewrite (Phases 0–3) is committed: data-driven **ETS** + **dated known-event
overlay** + `--force` on all CLIs + synthetic **weekend reduction defaults to 0**.

**Single source of truth:** [`../design/forecasting-model-design.md`](../design/forecasting-model-design.md)
(read first). Companion plan: [`forecasting-model-plan.md`](./forecasting-model-plan.md).

## Files to rewrite (they still reference weekday_bootstrap / weekend)
- `src/forecast/forecasting/README.md` — rewrite the model section to ETS (fit tiers, joint residual
  bootstrap, nonnegative correction, series/scope statuses); add the known-event overlay and `--force`.
- `docs/operations/README-forecast.md` — operator walkthrough: replace the weekday_bootstrap/weekend
  walkthrough with ETS + known-events + `--force`; drop the weekend section.
- `docs/design/synthetic-data-generation-design.md` — add the one-team-per-project reconciliation note;
  note weekend reduction now defaults to 0. Keep the generator's weekend + multi-team *capability*
  language (examples use one-team-per-project by convention).
- `src/forecast/synthetic/README.md` — one-team-per-project note, weekend-off default, `--force`.
- `src/forecast/utils/README.md` — add `--force` (single-plot and `--config` modes).
- **Do not** create `src/forecast/curve/README.md` — it does not exist (the curve doc is
  `examples/curve/README.md`, already current).
- `docs/notes/*` are historical evidence (numerical preflight, acceptance matrix, handoffs) — leave
  them as snapshots; do not chase their now-stale links.

## Key facts the docs must reflect (do not re-derive incorrectly)
- **Model:** statsmodels additive ETS. `model: {method: ets, seasonal: auto|none, trend: auto|none}`;
  `auto` runs the tiered fallback (14–27 level-only; 28+ weekly-seasonal + damped trend → seasonal-only
  → level-only). `seasonal: none` collapses to level-only even with `trend: auto` (no trend-only tier —
  a known design limitation). **No forecast-side weekend knob** (removed).
- **Uncertainty:** joint residual bootstrap across a scope's active models (shared per-scope date
  picks, ≥14 shared error dates); nonnegative correction with `uncertainty.max_negative_correction_ratio`
  (default 0.05) — exceedance keeps the series `qualified` but flags `excess_negative_correction`.
- **Known events (design §7):** post-simulation overlay, coherent per-path window multiplier
  (anti-washout); two axes — scope precedence **project→team→org** × **global agent** (multiplicative);
  `multiplier` (expected) + `spread` (absolute half-width); no model-level events; parent roll-up is
  deferred (Phase 3 of the plan). A one-off *historical* spike is not forecastable; a declared event is.
- **`--force`:** on all four CLIs (`synt_gen`, `forecast`, `curve`, `plot_forecast`) — recreates an
  existing/non-empty output dir; deletion confined to the resolved target.
- **Structural model (design §1):** Org→Team→Project **tree, one team per project**; agent orthogonal
  (models partition an agent — agent totals = Σ its model forecasts); `total = input + cache_read +
  output` (stored `input` already includes cache writes).
- **Synthetic weekend:** `weekend_reduction_probability` defaults to 0 (no weekly pattern unless opted
  in); still supported when set > 0.
- **Statuses:** series `qualified` / `inactive` / `insufficient_history` / `insufficient_startup` /
  `sampling_failed`; scope `complete` / `conditional` / `inactive` / `incomplete`. (No `fit_failed` /
  `correction_failed` — the reader rejects them.)

## Process / governance
- Follow root `CLAUDE.md`: no "Claude"/"Claude Code" strings in files or commits; `git` with file-path
  args (never `git -C`); structured JSON logging unaffected.
- Reference the **doc-rewrite issue number** in commits (`Refs #NN` — ask the user; `#64` was the
  example-data issue, this is a separate one).
- Verify docs-only changes keep the suite green: `econ-forecast/utils/test_forecast.sh` (the venv
  self-provisions). Docs don't affect tests, so this is a sanity gate, not a big run.
- Persistent memory already carries the workflow preferences (subagent handshake, keep-progressing,
  no-coding-until-authorized, one-team-per-project, issue tracking).

## Operational heads-up
Delegated subagents **stalled repeatedly** (stream watchdog, ~600s no progress) during the prior
session — Phase 3 was ultimately completed by the coordinator directly. For this doc rewrite, prefer
**doing the edits directly (or in small steps)** over one long subagent run; if delegating, checkpoint
progress frequently.
