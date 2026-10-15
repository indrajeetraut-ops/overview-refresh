# Curve Creation — TEST task brief (for the implementing agent)

Task 1's implementation is complete and reviewed (module `src/forecast/curve/`). Now
**write the tests** for it and **persist a test-run report** via the repo's existing
framework. **Do not start coding when you first receive this — see "Handshake" below.**

## Context to read first
- The code under test: `econ-forecast/src/forecast/curve/*.py`
- Design (test scope is §10): `econ-forecast/docs/design/curve-creation-design.md`
- Impl brief + binding resolutions: `econ-forecast/docs/plan/curve-impl-brief.md`
- Existing test style to mirror: `econ-forecast/tests/synthetic/*.py`,
  `econ-forecast/tests/utils/test_plot_forecast_cli.py`, and `tests/acceptance/conftest.py`
- Test-report framework: `econ-forecast/docs/test-results/README.md`,
  `econ-forecast/utils/test_forecast.sh` → `forecast.testing.cli_test_report`

## Where tests go
New directory `econ-forecast/tests/curve/` (discovered via root `pytest.ini`
`testpaths`/`--import-mode=importlib`). Deterministic, offline, use `tmp_path` for outputs.

## Coverage required (map each to the code)
- **model.py** — `evaluate_curve`: endpoints equal first/last resolved anchor levels;
  **no-overshoot** (sampled global min ≥ min anchor, max ≤ max anchor); **nonnegative
  curve** when anchors nonnegative; **stretch-exactness** (values at n=30/60/90 agree at
  shared normalized positions); `n==1` → level at pos 0; `n<1` raises. `parse_anchor_value`
  / `resolve_anchors`: signed=delta / unsigned=absolute; first-anchor-must-be-absolute
  (leading delta rejected); delta accumulation correctness; negative-walk rejected;
  non-string / unquoted-`+` value rejected (the YAML trap); positions strictly increasing;
  first/last at axis endpoints; `axis.end>start`.
- **config.py** — `load_curve_config`: valid loads; bad `interp`; `<2` anchors;
  non-increasing pos; endpoint/bounds violations; missing/invalid `name`; duplicate YAML
  keys; unquoted-`+` → `ConfigError`. Assert error TYPE (`ConfigError`) not just raising.
- **runner.py** — `config_hash` deterministic over resolved config; `curve.jsonl` rows
  equal `evaluate_curve(...)`; all six artifact files present; manifest last with
  `status:"complete"`; identical re-run → no-op (`run_curve` returns None); **incomplete
  dir** (missing/!=complete manifest) → `ArtifactError`; `--preview-days` folded into hash
  (different N ⇒ different dir).
- **cli_curve.py** — exit codes: `0` success/valid, `2` config/input/args, `1`
  artifact/unexpected; `--validate-only` writes nothing and its reported `config_hash`
  equals a real run's for the same effective config; stdout JSON summary shape; no-op path.
- **preview.py** — `render_html`/`render_png` produce files; `preview.html` mode is `0644`.
  Keep it a smoke test — do NOT pull in Playwright/browser (those are separately marked).

## Constraints (CLAUDE.md)
Use pytest. No new dependencies. No Claude references anywhere. Do NOT modify the `curve/`
implementation to make a test pass without flagging it to me first (a test that fails may
be a real bug — surface it, don't paper over it). Do NOT modify unrelated existing tests.

## Persisted report (after tests pass)
Produce ONE recorded run via the canonical framework
(`forecast.testing.cli_test_report`, i.e. what `utils/test_forecast.sh` runs) so a
timestamped run lands under `econ-forecast/docs/test-results/` (worktree copy) with
`report.md`/`junit.xml`/`result.json` and updated `index.json`/`index.md`. Note: this runs
the WHOLE suite (existing + new curve tests); standard mode records the 4 browser tests as
unverified — that's expected.

**Environment wrinkle to solve:** the venv lives at
`/Users/sumandra/projects/talium/forecasting/code-econ/venv` (main checkout), and
`_forecast_env.sh` computes `FORECAST_ROOT` as the worktree root, finds no `venv/` there,
and falls back to bare `python3` (missing deps). Propose in your review how you'll run the
framework so it (a) uses that venv, (b) runs the worktree's code, and (c) writes the
recorded run into the worktree's `docs/test-results/`.

## Handshake (do this FIRST, before any code)
Reply with: (1) your test-file plan for `tests/curve/` (file names + what each covers);
(2) how exactly you'll invoke the report framework in the worktree given the venv wrinkle;
(3) anything ambiguous or any place the spec and code disagree; (4) questions.
**Do not write or modify any code yet.** Wait for my explicit "go".
