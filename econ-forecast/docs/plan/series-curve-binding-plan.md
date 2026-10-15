# Task 2 — Curve-guided series generation: implementation plan

## Context
The synthetic series generator (`synthetic/series.py`) builds daily usage bottom-up from a
**fixed** per-profile baseline. Task 2 makes a chosen dimension's level **time-varying** by
binding a Task-1 curve artifact, while preserving all existing behavior. It is a **surgical**
change: two multiplications in the daily loop plus additive config/loading/provenance. A
flat curve (level 1.0) is a no-op, so an unbound config is byte-identical to today.

Authoritative design: `econ-forecast/docs/design/series-curve-binding-design.md`.
Decisions/background: `econ-forecast/docs/research/v2-synth-data-gen.md`.
**Process:** implement only after the user enters implementation mode; then follow the
subagent handshake workflow (sonnet, worktree, tests + persisted report, Major/Minor review).

## Decisions (locked)
- Bind by artifact **`name:id`** under an **explicit** curves root
  (`curve.artifacts: <path>`, required when the block is present; no `default`); `curve:`
  block optional; **schema_version stays 2**.
- Baseline: org/team **override** (team wins for its members, else org, else flat 1.0).
- Agent: **multiply-on-default** `usage_share`, renormalized (flat = configured ratio).
- Generator reads the locked artifact's **anchors** and re-evaluates at `series.days`
  (ignores the artifact's `preview.days`/`curve.jsonl`).
- No `curves:` (or all-flat) → **current method**, byte-identical.

## Changes

### New: `econ-forecast/src/forecast/curve/artifacts.py`
- `load_curve_artifact(dir_path) -> CurveArtifact(name, curve_id, anchors, axis, interp)`:
  verify `manifest.json` present + `status=="complete"`, `config_sha256` matches, dir `<id>`
  == short hash; then `resolve_anchors` from `config.yaml`. Raises curve `ArtifactError`
  otherwise. Parallels `forecasting/artifacts.py::load_run`.

### `synthetic/config.py`
- Add `CurveBindings` model: `artifacts: Path` (required), `org: str | None`,
  `teams: dict[str,str]`, `agents: dict[str,str]` (values are `name:id` strings,
  format-validated). Optional field `curve: CurveBindings | None = None` on `SeriesConfig`;
  resolve `artifacts` like `users_file`/`roster_file` (relative to the series YAML).
- Extend `validate_series_inputs`: `curve.artifacts` exists and is a directory; team keys ∈
  roster names; agent keys ∈ `config.agents`; each `name:id` exists under `artifacts` +
  verifies via `load_curve_artifact`; targeted error messages naming the offending binding.

### `synthetic/runner.py::run_series`
- Precompute a `CurveLevels` holder before `generate_days` (near line 150): for each bound
  curve, `evaluate_curve(load_curve_artifact(...).anchors, config.series.days)` →
  per-offset arrays (`org_levels`, `team_levels`, `agent_levels`; unbound → flat 1.0).
- Snapshot each bound artifact's `config.yaml` + `manifest.json` into
  `inputs/curves/<name>/<id>/` (reuse `_snapshot`, `runner.py:61`).
- Add a `curves` section (bound `name:id`s + `config_sha256`) to the series manifest via
  `_finish`.
- Pass `curve_levels=...` into `generate_days`.

### `synthetic/series.py::generate_days`
- New keyword param `curve_levels` (a `CurveLevels`, defaulting to an all-flat identity so
  callers/tests without curves are unaffected).
- Hook 1 (line ~172): multiply `total_input` by `Fraction(str(curve_levels.baseline
  (team.team_name, offset)))`.
- Hook 2 (line ~178): `usage_share[a]` → `usage_share[a] * curve_levels.agent(a, offset)`
  inside the `agent_weights` dict comprehension.

### Examples (`econ-forecast/examples/synthetic/`)
- One series config with **no** `curves:` (current method) and one **with** `curves:`
  (org + team + agent), referencing example curve artifacts under `artifacts/curves/`.

## Reused components
- `forecast.curve.model` (`resolve_anchors`, `evaluate_curve`), `forecast.curve.runner.
  config_hash`, `forecast.curve.errors`.
- `forecast.artifact_io` (`DatasetWriter`, `sha256`, `read_records`), `synthetic/runner.py`
  scaffold (`_operation`/`_snapshot`/`_finish`), `synthetic/allocation.py`
  (`vary_weights`/`allocate_integer`), `common.msging.log`.

## Verification
- **Flat = V1 regression (critical):** no-`curves` config and an all-flat-curve config each
  produce byte-identical `usage.jsonl`/`coverage.jsonl` to the current generator for the
  same seeds.
- Org curve bends the aggregate over the window; team curve overrides org for its members;
  agent curve shifts and renormalizes the daily split (flat agent = configured ratio).
- Reader rejects missing/incomplete/tampered artifacts; validation rejects unknown
  team/agent targets, bad/missing/ambiguous `name:id`.
- Edges: `series.days == 1`; agent level 0 → agent off; determinism replay.
- `venv/bin/python -m pytest econ-forecast/tests/synthetic econ-forecast/tests/curve -q`,
  then a recorded run via `econ-forecast/utils/test_forecast.sh`.

## Open (finalize before/at implementation)
- None outstanding on config/binding. (Deferred, not blocking: richer noise; session-data
  target.)
