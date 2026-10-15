# Task 2 — Curve-guided series generation: implementation brief

You are implementing **Task 2**: binding Task-1 curve artifacts into the synthetic **series
generator**, surgically. **Do not start coding when you first receive this — see
"Handshake" at the bottom.**

## Authoritative spec (read first)
- Design (authoritative): `econ-forecast/docs/design/series-curve-binding-design.md`
- Plan: `econ-forecast/docs/plan/series-curve-binding-plan.md`
- Background/decisions: `econ-forecast/docs/research/v2-synth-data-gen.md`
- Task-1 curve module you build on: `econ-forecast/src/forecast/curve/` (esp. `model.py`
  `resolve_anchors`/`evaluate_curve`, `config.py` `load_curve_config`, `runner.py`
  `config_hash`, `errors.py`).
If anything conflicts with the design doc, the design wins — but flag it, don't silently choose.

## What to build (all additive/surgical; see plan for detail)
1. **`forecast/curve/artifacts.py`** — `load_curve_artifact(dir_path) -> CurveArtifact`
   (`name`, `curve_id`, `anchors`, `axis`, `interp`). Verify: dir exists; `manifest.json`
   present with `status=="complete"`; `config_sha256` matches; dir `<id>` == short hash;
   then `resolve_anchors` from `config.yaml`. Raise a curve `ArtifactError` otherwise.
   Parallels `forecasting/artifacts.py::load_run`.
2. **`synthetic/config.py`** — `CurveBindings` model: `artifacts: Path` (required),
   `org: str | None`, `teams: dict[str,str]`, `agents: dict[str,str]` (values `name:id`,
   format-validated). Optional `curve: CurveBindings | None = None` on `SeriesConfig`;
   resolve `artifacts` like `users_file`/`roster_file` (relative to the series YAML) in
   `_load`. schema_version STAYS 2.
3. **`synthetic/config.py::validate_series_inputs`** — `curve.artifacts` exists & is a dir;
   team keys ∈ roster Team names; agent keys ∈ `config.agents`; each `name:id` exists under
   `artifacts` and verifies via `load_curve_artifact`; bare `name` allowed only if unique.
   Fail loud with targeted messages naming the offending binding (Task-1 error style).
4. **`synthetic/runner.py::run_series`** — precompute a `CurveLevels` holder before
   `generate_days`: for each bound curve, `evaluate_curve(load_curve_artifact(...).anchors,
   config.series.days)` → per-offset float arrays (`org_levels`, `team_levels`,
   `agent_levels`; unbound → flat `1.0`). Snapshot each bound artifact's `config.yaml` +
   `manifest.json` into `inputs/curves/<name>/<id>/` (reuse `_snapshot`). Add a `curves`
   section (bound `name:id`s + `config_sha256`) to the manifest via `_finish`. Pass
   `curve_levels=...` into `generate_days`.
5. **`synthetic/series.py::generate_days`** — new keyword `curve_levels` (default: an
   all-flat identity so existing callers are unaffected). Two hooks:
   - baseline (`series.py:172`): `total_input = Fraction(daily_input_tokens)` ×
     `Fraction(str(curve_levels.baseline(team.team_name, offset)))` — mirror the existing
     `volume`/`multiplier` Fraction handling.
   - agent split (`series.py:178`): in the `agent_weights` comprehension,
     `usage_share[a]` → `usage_share[a] * curve_levels.agent(a, offset)`.
   Semantics: baseline = team level if team bound else org else 1.0; agent = level or 1.0.
6. **Examples** (`econ-forecast/examples/synthetic/`) — add a series config WITHOUT `curve:`
   (current method) and one WITH `curve:` bindings, referencing existing example curve
   artifacts in `econ-forecast/examples/curve/artifacts/` (real `name:id`, e.g.
   `ramp-and-plateau:334e67bdc9b546bb`). Point `curve.artifacts` at that dir (relative path).

## THE critical invariant
**A flat / absent curve must be a mathematical no-op → byte-identical output.** With no
`curve:` block (or an all-flat curve), `usage.jsonl`/`coverage.jsonl` must match the current
generator exactly for the same seeds. `Fraction(x) * Fraction("1.0") == Fraction(x)` and
`usage_share * 1.0 == usage_share`, so this holds — but preserve it carefully and prove it
with a sanity run (generate the same series with and without an empty `curve:` and diff).

## Hard constraints (CLAUDE.md)
- Python 3.11+, repo venv. Structured JSON logging via `common.msging.log`. Exit codes
  0/2/1. **No new dependencies.** **Never reference Claude** anywhere. Deterministic (curve
  levels come from anchors; no RNG added). Add `# TODO:` for anything deferred.
- Do NOT restructure the synthetic pipeline or the curve module; only the additive changes
  above. Flag any unavoidable change to existing behavior BEFORE making it.

## Scope for THIS task
Implementation + a self-sanity run only. **Do NOT write the test suite yet** — tests +
recorded report are a separate task I will direct next.

## Environment
- Work ONLY in this worktree:
  `/Users/sumandra/projects/talium/forecasting/code-econ/.claude/worktrees/series-curves`.
- Run python via the main-checkout venv with PYTHONPATH set:
  `PYTHONPATH="<wt>/econ-forecast/src:<wt>/src" /Users/sumandra/projects/talium/forecasting/code-econ/venv/bin/python ...`
  (Optionally create a gitignored `venv` symlink at the worktree root → that venv to use the
  `utils/*.sh` wrappers, as was approved for Task 1.)

## Handshake (do this FIRST, before any code)
Read the spec docs and the code you'll touch (`synthetic/series.py`, `synthetic/runner.py`,
`synthetic/config.py`, `synthetic/allocation.py`, `forecast/curve/*`, `artifact_io.py`).
Then reply with: (1) anything ambiguous/underspecified or where spec and code disagree
(cite file:line); (2) your concrete file-by-file implementation outline (incl. the
`CurveLevels` shape and how you guarantee the byte-identical no-op); (3) how you'll resolve
`curve.artifacts` and reference the example curve artifacts; (4) questions.
**Do not write or modify any code yet.** Wait for my explicit "go".

## Resolutions (answers to the review — binding)

1. **`_snapshot_curve` sibling helper: approved.** `_snapshot` uses `read_records` (JSONL),
   which can't validate curve's single-doc `config.yaml`. Write a sibling that does the two
   `writer.snapshot(...)` copies and verifies via `sha256(copy) == manifest["config_sha256"]`.
   Do not change the existing `_snapshot`.
2. **`_finish` passthrough: approved**, and name the manifest key **`curve`** (match the
   config block, singular) — add `"curve"` to the passthrough tuple. It is a no-op for
   `run_users`/`run_roster` (they don't set it).
3. **Error boundary: approved.** `validate_series_inputs` and `run_series` catch
   `forecast.curve.errors.*` from `load_curve_artifact`/`load_curve_config` and re-raise as
   `synthetic.errors.InputDataError` (mirrors `curve/runner.py`'s reverse wrap), so the
   synthetic CLI's exit-code dispatch stays consistent (→ exit 2 for bad bindings).
4. **Double-load: fine** (tiny files); don't thread a cache.
5. **Naming: config block is `curve:` (singular); manifest section key is also `curve`.**
6. **Bare-`name` resolution counts only COMPLETE artifacts.** Ignore incomplete/orphaned
   sibling dirs (no complete manifest). Exactly one complete → use it; zero → "not found";
   ≥2 complete → "ambiguous, specify :id". (Consistent with Task-1's incomplete-dir rule.)
7. **`CurveLevels` shape approved** as you sketched (zero-arg identity default, `1.0`
   accessors). Extend the existing `for factor in (volume, multiplier)` loop to include the
   baseline level (exact `Fraction` no-op at 1.0); agent hook `usage_share[a] *
   agent(a,offset)` (exact float no-op at 1.0). Do the with/without-`curve:` byte-identical
   diff before calling it done.
8. **Edge to handle cleanly:** if *every* bound agent resolves to level `0` on the same day,
   `vary_weights` will raise ("weights must have a positive sum"). That config is
   nonsensical, but surface it as a **clear** curve/validation-flavored message (or guard
   it), not a cryptic internal error.

**Scope reminder:** implementation + the byte-identical sanity run only — NO test suite yet.
Flag any unavoidable change to existing behavior before making it. **GO — implement now.**
