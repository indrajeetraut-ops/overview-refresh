# v2 synthetic data generation — curve-guided consumption

**Status:** research / brainstorm (evolving). **Started:** 2026-09-20.
**Scope:** design notes and decisions for a v2 synthetic generator whose daily
consumption baseline is guided by an operator-authored *shape curve* instead of a fixed
per-profile constant. Not a spec; decisions are marked **[working]** or **[open]**.

Prototype code (throwaway) lives in the isolated `curve-prototype` git worktree/branch,
not in this module. Only validated ideas get lifted back here.

## How consumption is generated today

Daily usage is built **bottom-up per user**. For each active user-day
(`synthetic/series.py`, config `config/synthetic/series.yaml`):

```
daily_input_tokens(profile)      # FIXED per-profile baseline, constant all series
  x volume jitter                # uniform(1 - v, 1 + v), v = daily_variation_pct/100
  x weekend reduction            # factor w/ weekend_reduction_probability on weekends
  (+ spike increments)           # events.spike_rules: probability, multiplier range, targets
  -> allocate across agents/models (usage_share, model shares, mix_variation_pct)
  -> split token categories (cache_hit_ratio +/- cache_hit_variation_pp, output_ratio)
```

Baselines (`series.yaml`): `light 250k`, `mid 1M`, `heavy 5M` per active weekday.
Inactivity: `daily_zero_probability`. The **only time-constant piece** is
`daily_input_tokens`; everything else is already stochastic scatter. So the aggregate is
roughly flat over time — there is no way to express "consumption ramps through the
quarter." v2 makes that one constant **time-varying**, driven by a shape.

(Note: shipped config is `schema_version: 2`; `events` uses `spike_rules` with `targets`
and `zero_periods`, which differs from some older docs.)

## V2 supersedes V1 — one code path, no back-compat

V2 is a **strict superset** of V1, so V1 is dropped entirely (only the old config schema
is obsolete). Everything flows through the curve mechanism; there is no "legacy vs. curve"
branching.

- **Default = flat curve (level 1.0 everywhere).** No binding assigned → implicit identity
  multiplier → constant baseline → **exactly today's V1 output**. V1 is just the degenerate
  flat-curve case of V2. The flat default is **not a materialized artifact** — it is the
  identity fallback already in the binding rules (no team curve → 1.0; flat agent curve →
  config ratio). Optionally recorded as `curve: default/flat` in the run manifest for
  provenance.
- **Assign a curve reference → true V2** (dynamic baseline / dynamic distribution).

"Straight line" default = **horizontal at 1.0** (constant). A *sloped* straight line is
already true V2 (a linear trend), not the default.

## Workflow (two independent stages)

```
[1] curve-create   ->  saved curve artifact(s)   ->  [2] series-create (v2)  ->  usage/coverage
    author a shape       (config + curve values         reference a saved curve as the
    (anchors -> PCHIP)     + preview + manifest)          time-varying baseline
```

Independent and reproducible, mirroring existing `user-create` / `roster-create` /
`series-create`.

### Stage 1 — curve-create (new)

- **Input:** shape config (YAML): `interp` (pchip), anchors `[{date, level}]`, and a
  reference window. `level` is a dimensionless multiplier (1.0 = "profiles at their
  configured baseline").
- **Duration-normalized:** authored dates define only *relative positions* within the
  shape — they are **not** calendar-bound. The curve is a shape over its own duration and
  is stretched/resampled onto any target window at generation time.
- **Smooth trend only:** the curve carries the underlying trend, **no spikes** (see
  decision below). PCHIP (shape-preserving, no overshoot) keeps bends natural.
- **Output artifact (one dir per curve):** resolved `config.yaml`, `curve.jsonl`
  (normalized daily/position `level`), `preview.html`/`.png`, `manifest.json`. A *library*
  of curves accumulates; stage 2 references one by name/path.

### Stage 2 — series-create v2 (curve-guided)

The curve **replaces the fixed baseline**. Consumption is *guided by* the curve, not
fitted to it ("reverse fit"): the curve is the latent expected level; realized daily
points scatter around it via the existing probability machinery.

- **[working] Integration point:** replace the constant at `series.py:172`
  (`daily_input_tokens`) with `daily_input_tokens x curve_level(pos(day))`, where
  `pos(day)` maps the calendar day onto the curve's normalized duration. All downstream
  layers (volume jitter, weekend reduction, inactivity, allocation, cache/output split)
  are untouched.
- **[working] Spikes and calendar features stay in the generator, not the curve.** The
  existing `events.spike_rules` (date-aware, configurable) supply discrete spikes;
  weekend seasonality stays in weekend reduction. The curve is only the smooth trend.
- The aggregate then **emerges** scattered around `Sigma(baseline) x curve`, with no
  renormalization — the "guide, don't fit exactly" behavior falls out for free.

Rationale for user-level time-varying baseline (vs. top-down aggregate-then-allocate):
literally "replaces the fixed baseline," preserves all existing per-user texture, minimal
re-architecture, no renormalization fighting the "don't fit" principle.

### Curve binding / association

A curve binds to one of three dimensions; a curve is a reusable shape, a **binding** ties
it to a target. Nothing more granular (no per-user/project/model).

- **Org** — default when nothing is assigned; drives the whole-org per-user baseline.
- **Team** — drives that team's per-user baseline. **Override** semantics: a team with its
  own curve uses it; else falls back to the org curve; else flat (1.0).
- **Agent** — drives the **allocation split**, not the total. Makes `usage_share`
  time-varying.

**Two orthogonal axes:** org/team curve = *how much* (total per user-day); agent curve =
*who* (which agent gets what share). They apply at different pipeline stages and compose
cleanly.

**Agent mechanism (multiply-on-default + renormalize):** each agent's daily weight =
configured `usage_share` × agent curve level that day; then normalize across agents so the
day's shares sum to 1 (each < 1). Flat curves (level 1.0) reproduce the config default
exactly. Example — config `claude 0.6 / codex 0.4`, claude curve 1.0→2.0, codex flat 1.0:

```
early day: 0.6·1.0, 0.4·1.0 = .60/.40 → claude 60% / codex 40%
late  day: 0.6·2.0, 0.4·1.0 = 1.2/.40 → (÷1.6) claude 75% / codex 25%
```

On a late day with a 1,000,000-token user total: claude 750,000 / codex 250,000, then each
splits across its models as today. The configured ratio is the baseline distribution that
the curves **animate** over the window — the ratio becomes dynamic, it is not replaced.

### Genericity (keep the binding decoupled from the modulated quantity)

The synthetic machinery is generic beyond daily tokens (e.g. could later drive **session**
usage / shared-session data — out of scope now). Keep the abstraction "curve binds to a
dimension and applies as a time-varying multiplier on a *target quantity*"; do not hardcode
"daily tokens" into the curve/binding layer so other resultant-data types stay possible.

## Methods notes

- **Interpolation:** PCHIP (Fritsch–Carlson). Chosen over natural cubic spline because it
  does not overshoot — no invented dips/wiggles between anchors. Validated in prototype.
- **Duration mapping (no separate utility):** stretching is just evaluating the shape at
  a different resolution — PCHIP is continuous. At gen time `pos(day) = (day - start) /
  (days - 1)`; evaluate PCHIP there. The curve **artifact stores anchors + interp as the
  source of truth** (not a frozen daily sample), so resampling to any N days is *exact*
  with no information loss; the saved `curve.jsonl` is preview-only and ignored by
  generation. Only a tiny **shared helper** (`evaluate_curve(anchors, n_days) -> levels`)
  is needed, reused by the generator and the preview renderer — not a CLI or stage.
  Stretching a short shape over a long window spreads features out (rate of change scales
  with window) — intended. Optional: a `--window` preview flag to eyeball a curve mapped
  onto a specific calendar window before generating.
- **[open] Richer noise:** today's jitter is uniform and i.i.d. Candidates for later:
  multiplicative lognormal (variance scales with level) and AR(1) persistence on the
  log-residual so busy stretches cluster. Mirrors the trend x seasonal x irregular
  structure the forecaster assumes. Deferred — first cut keeps existing jitter.

## Open questions

- **[open]** Richer noise model (AR(1)/lognormal) now or later — leaning later.
- **[open — must decide before implementation]** Build strategy: **(a) clean slate**
  (new v2 generator, port well-tested pieces from V1 as needed) vs. **(b) surgical
  modification** of V1 in place. Tradeoffs:
  - *Clean slate:* the curve/binding abstraction sits at the core; avoids inheriting V1's
    bottom-up fixed-baseline assumptions; free to restructure. Cost: re-porting and
    re-verifying allocation, randomness streams, events, artifact_io, contracts.
  - *Surgical:* lowest risk/effort if V1 is well-factored — the baseline hook is a single
    point (`series.py:172`) and bindings layer around allocation. Cost: carries V1's
    structure; risk if baseline vs. stochastic layers are tangled.
  - *Deciding factor:* how cleanly current `series.py` separates the baseline from the
    stochastic/allocation layers. Clean separation → surgical is fine; tangled → clean
    slate. Assess factoring before choosing. Reusable regardless: `allocation.py`,
    `randomness.py`, `events.py`, `artifact_io.py`, `contracts.py`, config validation.

## Resolved

- Curve scope = **Org (default) / Team / Agent**; no finer granularity. Org/Team override
  on the baseline axis; Agent modulates the allocation split (orthogonal).
- Units of `level` = **dimensionless multiplier** (1.0 = no change). Forced by the
  multi-axis multiplier design (agent curves multiply into a share).
- Agent binding = **multiply-on-default**: the configured `usage_share` ratio is the
  baseline distribution; agent curves make that ratio **dynamic** over the window (bend,
  don't replace). Flat curves reproduce the configured ratio exactly.

## Decision log

- 2026-09-20 **[working]** Curve representation = `(date, level)` anchors + PCHIP
  interpolation. Reason: low-friction reproducible authoring; PCHIP avoids overshoot.
- 2026-09-20 **[working]** Curve integrates as a **time-varying per-user baseline**
  (replaces `series.py:172` constant); all existing stochastic/allocation layers stay.
- 2026-09-20 **[working]** curve-create is a **separate stage** producing saved, reusable
  curve artifacts (config + values + preview + manifest).
- 2026-09-20 **[working]** **Spikes stay in the generator** (`events.spike_rules`,
  date-aware/configurable), not driven by the curve. Curve tool keeps spike-drawing
  ability but synth-gen ignores curve-spikes → curve is smooth trend only.
- 2026-09-20 **[working]** Curve is **duration-normalized / stretchable**; authored dates
  are relative positions, mapped onto any target calendar window at generation time.
- 2026-09-20 **[working]** Stretching needs **no separate utility/stage** — inline PCHIP
  re-evaluation via a shared `evaluate_curve(anchors, n_days)` helper; artifact stores
  anchors as source of truth so resampling is exact.
- 2026-09-20 **[working]** Curve binding dimensions = **Org (default) / Team / Agent**.
  Org/Team = baseline (override semantics); Agent = time-varying allocation split via
  multiply-on-`usage_share` + renormalize. `level` is a **dimensionless multiplier**.
- 2026-09-20 **[working]** Keep the curve/binding layer **generic over the modulated
  quantity** (daily tokens now; session data a possible future target) — don't hardcode
  "daily tokens".
- 2026-09-20 **[working]** **V2 supersedes V1, no back-compat.** One curve-driven code
  path; default flat curve (level 1.0) reproduces V1 exactly. Only the old config schema
  is obsolete. Flat default is an implicit identity, not a materialized artifact.
