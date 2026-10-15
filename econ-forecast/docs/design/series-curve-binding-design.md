# Task 2 — Curve-guided series generation: design

**Status:** design draft (for review/edit). **Date:** 2026-09-21.
**Companion:** [v2 synth data gen research + decisions](../research/v2-synth-data-gen.md),
[Task 1 curve creation](./curve-creation-design.md).
**Audience:** implementer. Decisions are **[decided]**, **[proposed]**, or **[open]**.
**Process:** no implementation coding until the user enters implementation mode.

## 1. Purpose & scope

Let the synthetic series generator be guided by operator-authored **shape curves** (Task 1
artifacts) instead of only a fixed per-profile baseline. A curve makes a chosen dimension's
level **time-varying**; the daily consumption still *scatters around* it via the existing
stochastic machinery ("guide, don't fit").

- **In scope:** an optional `curves:` binding in the series config; loading/verifying the
  referenced curve artifacts; two multiplicative hooks in the daily loop; provenance;
  validation; examples (with and without curves); tests.
- **Non-goals:** spikes stay in the generator (`events.spike_rules`), not the curve;
  richer noise (AR(1)/lognormal) deferred; no session-data mode; **no rewrite** — this is
  surgical.

## 2. Guiding property — flat curve = current method **[decided]**

A flat curve (level `1.0`) is a mathematical no-op. Therefore:
- **No `curves:` block → byte-identical to today.** Existing schema-2 configs are unchanged.
- **Partial binding** → only bound dimensions are shaped; the rest stay flat.
V1 is just the all-flat case of V2; there is one code path, no legacy branch.

## 3. The two integration hooks **[decided]** (surgical)

In `synthetic/series.py::generate_days`, per active user-day:

- **Org/Team baseline** — `series.py:172`:
  `total_input = Fraction(daily_input_tokens)` → `× Fraction(str(baseline_level(team, offset)))`.
  Mirrors the existing `volume`/`multiplier` Fraction handling (`series.py:170-174`), so
  determinism/rounding are preserved.
- **Agent distribution** — `series.py:178`:
  `{a: usage_share[a] for a in agents}` → `{a: usage_share[a] * agent_level(a, offset) for a in agents}`,
  then the existing `vary_weights` + `allocate_integer` (both renormalize). A flat agent
  curve reproduces the configured ratio exactly; a shaped one makes the split dynamic.

**Composition:** weekend reduction, volume jitter, and spikes remain multiplicative on top;
spikes layer on the curve-scaled baseline (intended). Org/Team acts on *how much*; Agent on
*who* — orthogonal axes.

## 4. Config — optional `curve:` block **[decided]** (schema_version stays 2)

```yaml
# ... existing series config (series, defaults, profiles, team_profiles, projects,
#     agents, events) unchanged ...
curve:                                   # OPTIONAL; omit for current behavior
  artifacts: ../../artifacts/curves      # REQUIRED explicit root (rel. to this YAML, or absolute)
  org: eng-org:2d792622e5c32675          # name:id (locks an immutable artifact)
  teams:
    core: core-ramp:ab12cd34ef56aa01
  agents:
    claude: claude-adoption:def4567890ab12cd
```

- **Everything explicit; no `default`, no guessing.** `curve.artifacts` is a required path,
  resolved exactly like `users_file`/`roster_file` (relative to the series YAML, or
  absolute). If the block is present, `artifacts` must be present.
- **Reference = `name:id`.** `id` is the curve's `config_hash`; it pins an exact, already
  validated, immutable artifact. Bare `name` allowed only when a single artifact exists
  under that name; otherwise `id` is required (hashes are not chronological).
- **Binding shape** = maps keyed by dimension, so "≤1 curve per org/team/agent" holds by
  construction.
- **Fail loud:** any wrong reference is flagged (see §9) — missing/not-a-dir `artifacts`,
  a `name:id` absent under it, an incomplete/tampered artifact, an unknown team/agent
  target, or an ambiguous bare `name`.

## 5. Binding semantics **[decided]**

- **Baseline (org/team, same axis) — override:** a user's effective baseline level =
  `team_levels[team][offset]` if that team is bound, else `org_levels[offset]`, else `1.0`.
- **Agent (orthogonal axis) — multiply-on-default:** `usage_share[a] × agent_levels[a]
  [offset]`, renormalized by `vary_weights`. Unbound agent → level `1.0`.
- **Dimensionless levels**, `1.0` = no change (enforced ≥ 0 by Task 1; PCHIP never
  overshoots below the anchors).

## 6. Curve resolution & the verified reader **[proposed]**

New module **`forecast/curve/artifacts.py`**:

```
load_curve_artifact(dir_path) -> CurveArtifact   # name, curve_id, anchors, axis, interp
    # verifies: dir exists; manifest.json present with status == "complete";
    # config_sha256 matches; the dir's <id> == short(config_sha256); then returns the
    # resolved anchors (resolve_anchors from config.yaml). Raises a curve error otherwise.
```

Parallels forecasting's `load_run` (verify before consumers touch it). `run_series` resolves
each `name:id` to `<curve.artifacts>/<name>/<id>/`, loads via this reader, and
`evaluate_curve(anchors, series.days)` → a per-offset level array. The artifact's
`preview.days`/`curve.jsonl` are **ignored** — the shape is re-stretched from anchors onto
the actual series length.

## 7. Precompute in `run_series` **[proposed]** (additive block)

Before `generate_days` (near `runner.py:150`), build a small `CurveLevels` holder:
- `org_levels: list[float]` (length `series.days`; all `1.0` when unbound),
- `team_levels: dict[team_name, list[float]]` (only bound teams),
- `agent_levels: dict[agent, list[float]]` (only bound agents),
with accessors `baseline(team, offset)` (team-or-org-or-1.0) and `agent(agent, offset)`
(agent-or-1.0). Passed as a new keyword arg to `generate_days`. Everything else in
`run_series` is unchanged.

## 8. Provenance **[proposed]**

- Snapshot each bound artifact's `config.yaml` + `manifest.json` into the series artifact
  under `inputs/curves/<name>/<id>/` (reuse the `_snapshot` pattern in `runner.py:61`).
- Record the bound `name:id`s (org/teams/agents) and their `config_sha256` in the series
  `manifest.json` under a `curves` key.
- Because `id` = config hash, a run is reproducible and traceable to exact curves.

## 9. Validation **[proposed]** (additive)

- `config.py`: a `CurveBindings` pydantic model (optional `curve` field on `SeriesConfig`);
  `artifacts` required when the block is present; validate `name:id` string format.
- `validate_series_inputs` (has roster + `config.agents`): `curve.artifacts` exists and is
  a directory; team keys ∈ roster Team names; agent keys ∈ `config.agents`; each `name:id`
  resolves under `artifacts` and verifies via `load_curve_artifact`; bare `name` unambiguous.
  Clear, targeted error messages (name the offending binding), consistent with Task 1's
  error style.

## 10. Edge cases **[decided]**

- `series.days == 1` → `evaluate_curve(anchors, 1)` → level at pos 0.
- Agent curve level `0` on a day → that agent's weight is 0 → 0 tokens that day (agent
  "off"); other agents absorb the renormalized split.
- Org↔Team resolved at the baseline lookup (team wins for its members).
- A team in the roster with no team binding uses the org curve (or flat).

## 11. Examples **[decided]** (`examples/synthetic/`)

Add BOTH: a series config with **no** `curves:` (current method / flat) and one **with**
`curves:` bindings (org + a team + an agent), referencing example curve artifacts. Show the
fallback and the opt-in side by side.

## 12. Files touched / added

- Edit: `synthetic/series.py` (2 hooks + `generate_days` signature), `synthetic/runner.py`
  (precompute + snapshots + manifest), `synthetic/config.py` (`Curves` model + validation).
- New: `forecast/curve/artifacts.py` (verified reader), example series configs, tests.
- Reuse: `forecast.curve.model` (`resolve_anchors`, `evaluate_curve`),
  `forecast.curve.runner.config_hash`, `artifact_io` snapshot/manifest, existing
  `allocation` helpers.

## 13. Testing **[proposed]**

- **Flat = V1 regression:** a config with no `curves:` (and one with all-flat curves)
  produces byte-identical `usage.jsonl` to the pre-change generator for the same seeds.
- Org curve scales the aggregate over time; team curve overrides org for its members; agent
  curve shifts the daily split and renormalizes (flat agent = configured ratio).
- Reader: rejects missing/incomplete manifest, id≠hash, tampered config.
- Validation: unknown team/agent target, missing/invalid `name:id`, ambiguous bare name.
- Edges: `series.days == 1`; agent level 0 → agent off; determinism/reproducibility.
- Persist a recorded run via `test_forecast.sh` (per Task-1 convention).

## 14. Deferred / open

- **[deferred]** richer noise (AR(1)/lognormal); session-data modulation target.
