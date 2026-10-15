# Task 2 — TEST task brief

Task-2 implementation is complete and reviewed (curve-guided series generation). Now
**write the tests** and **persist a full-suite recorded report**. **Do not start coding when
you first receive this — see "Handshake" below.**

## Context to read first
- Code under test: `forecast/curve/artifacts.py` (new reader), `synthetic/config.py`
  (`CurveBindings`, `_validate_curve_bindings`), `synthetic/runner.py`
  (`_resolve_curve_levels`, `_snapshot_curve`), `synthetic/series.py` (`CurveLevels`, the two
  daily-loop hooks).
- Design (test scope §13): `econ-forecast/docs/design/series-curve-binding-design.md`.
- Existing test style to mirror: `econ-forecast/tests/synthetic/*.py`,
  `econ-forecast/tests/curve/*.py`, `tests/acceptance/conftest.py`.
- Report framework: `econ-forecast/docs/test-results/README.md`, `utils/test_forecast.sh`.

## Where tests go
- Reader tests → `econ-forecast/tests/curve/test_artifacts.py`.
- Binding + generation tests → `econ-forecast/tests/synthetic/` (new file, e.g.
  `test_curve_binding.py`). Deterministic, offline, `tmp_path` for outputs.

## Coverage required (map each to the code)
- **THE centerpiece — flat = V1 byte-identical regression.** Generate users+roster once,
  then series runs with the SAME seeds and assert **byte-identical** `usage.jsonl` /
  `coverage.jsonl` / `generation_events.jsonl` across: (a) no `curve:` block, (b) a
  present-but-empty `curve:` block, and (c) an all-flat curve (anchors all `"1.0"`) bound to
  org/team/agent. All three must match exactly (compare sha256). This is the invariant that
  proves the change is a no-op when nothing shapes the data.
- **`curve/artifacts.py`** — `load_curve_artifact`: happy path returns correct
  `name`/`curve_id`/`anchors`/`axis`/`interp` from a real generated artifact; rejects
  (each a distinct case) missing dir, missing/symlink manifest, `status != complete`,
  invalid/missing `config_sha256`, `config.yaml` checksum mismatch (tampered), dir id ≠
  hash[:16], dir name ≠ manifest name, missing `config.yaml`. `resolve_curve_ref`: `name:id`
  resolves; bare unique-complete `name` resolves; bare name with zero complete → error;
  bare name with ≥2 complete → ambiguous; incomplete/orphaned sibling ignored; bad ref
  format rejected. Assert error TYPE (`ArtifactError`), not just "raises".
- **Config binding (`synthetic/config.py`)** — `CurveBindings` ref-format validation
  (`name`, `name:16hex`, rejects bad); `curve.artifacts` path resolves relative to the
  series YAML; `validate_series_inputs` raises `InputDataError` (specifically) for: unknown
  team target, unknown agent target, non-dir `artifacts`, missing/ambiguous/invalid
  `name:id` — each message naming the offending binding.
- **Generation semantics** — org curve bends the aggregate over the window; team curve
  overrides the org curve for its members (its baseline follows the team curve, org does
  NOT apply); agent curve shifts the daily split and renormalizes (flat agent = configured
  ratio, a shaped one moves the share). Coverage/active-day counts unchanged by curves.
- **Provenance** — a bound run's manifest has the `curve` section (name/id/config_sha256/
  snapshot for each bound target) and the `inputs/curves/<name>/<id>/` snapshots exist.
- **Edges** — `series.days == 1`; all bound agents at level 0 on a day → clear
  `InputDataError` (not a bare `vary_weights` ValueError); determinism (same seeds + curves
  → identical output on repeat).
- **`CurveLevels` unit** — identity default returns 1.0; `baseline` override (team>org>1.0);
  `agent` multiply-or-1.0.

## Constraints (CLAUDE.md)
Use pytest. No new dependencies. No Claude references anywhere. Do NOT modify the Task-2
implementation to make a test pass without flagging it to me first — a failing test may be a
real bug; surface it. Do NOT modify unrelated existing tests. Fixtures: generate real curve
artifacts (via the curve tool / `run_curve`) into `tmp_path` for happy paths; construct or
mutate dirs for the negative reader cases.

## Persisted report (after tests pass)
Produce ONE recorded run via the canonical framework (`utils/test_forecast.sh`, standard
mode) so a timestamped run lands under `econ-forecast/docs/test-results/` with updated
`index.json`/`index.md`. It runs the WHOLE suite (existing + new curve + new curve-binding
tests); the 4 browser tests record as unverified as usual. The gitignored `venv` symlink at
the worktree root lets the wrapper find the dependency-complete venv.

## Handshake (do this FIRST, before any code)
Reply with: (1) your test-file plan (files + what each covers, esp. how you construct the
byte-identical regression and the tampered-artifact cases); (2) how you generate fixture
curve artifacts; (3) anything ambiguous or where spec and code disagree; (4) questions.
**Do not write or modify any code yet.** Wait for my explicit "go".

## Resolutions (answers to the review — binding)

1. **Centerpiece config: use the REALISTIC one** (nonzero jitter/weekend/inactivity/mix
   variation + an active spike), fixed seeds. A no-op that only holds under zero-jitter is a
   hollow proof; the flat curve must perturb *nothing* even with all the stochastic
   machinery running. Good call.
2. **Bind ALL teams + ALL agents (+ org)** to the single flat curve in variant (c) — the
   strongest no-op proof, exercises every binding-dict path.
3. **Order:** get the new tests green locally first (fast feedback), THEN produce the one
   recorded full-suite run via `test_forecast.sh`.
4. **`validate_series_inputs` "invalid name:id":** your reading is right — format is caught
   earlier by pydantic (`ValidationError`); at that layer the cases are missing/ambiguous
   (well-formed but unresolved). Test content-invalid (tampered/incomplete) at the *reader*
   layer, missing/ambiguous at `validate_series_inputs`.
5. **Regex duplication:** noted, leave it (matches my own review's Minor list).

**CRITICAL — the flat-bound variant (c):** it is *expected* to be byte-identical, which
relies on PCHIP through two equal anchors evaluating to exactly `1.0` at every sample. If
(c) is NOT byte-identical to (a)/(b) — e.g. a `1.0000000001` sneaks in — **STOP and report
the exact diff. Do NOT weaken the assertion, relax the test, or edit the implementation to
make it pass.** That mismatch would itself be a finding worth surfacing.

Reaffirm: any test that reveals a real defect → STOP and flag; do not paper over it.
**GO — write the tests now.**
