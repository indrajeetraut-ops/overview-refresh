# Plot / pipeline — implementation brief (Revision 2, issue #78)

You implement **Revision 2** of `econ-forecast/docs/plan/plot-forecast-enhancements-plan.md`
(tasks T1–T9, at the end of that file). The authoritative design is
`econ-forecast/docs/design/plot-forecast-enhancements-design.md` §5–§11 and §14. Decisions and their
reasons: `econ-forecast/docs/notes/plot-brainstorm.md` (D1–D30; struck items are withdrawn).
Read all three fully before anything else, plus the root `CLAUDE.md`.

## Where
- Worktree: `/Users/sumandra/project/talium/forecast/code-econ/.worktrees/plot-pipeline`
  (branch `feat/plot-pipeline`). Work **only** here. `venv` is a symlink to the shared venv.
- Python: `source econ-forecast/utils/_forecast_env.sh` then `"$FORECAST_PYTHON"`; run tests from
  `econ-forecast/`.

## Rules
- **Surgical** (D20): change only what the design §14.5 and plan tasks list. No schema-version
  bumps, no backward-compat shims, no refactors of untouched code, no renames of existing modules.
- The pipeline is a **dumb orchestrator** (D24/D25): it calls the `utils/*.sh` CLIs via subprocess,
  knows the layout, holds no module settings, has only `--config`.
- Match surrounding code style: structlog JSON logging to stderr, one JSON summary line on stdout,
  pydantic `Strict` models, `cli_<function>.py` entry points, wrappers like `utils/cost.sh`.
- No new dependencies.
- Leave `TODO(#78: …)` only for something deliberately deferred.
- Never reference Claude / AI tools in code, comments, or commit messages.
- `git`: never `git -C`; commit on `feat/plot-pipeline` per task (or small group), message
  `<type>(<scope>): … ` + `Refs #78`, **no** Co-Authored-By line.
- Do not delete or move anything under `econ-forecast/examples/` (the old examples). T9 builds
  `econ-forecast/examples-test/` only; the swap is done later by the coordinator after user approval.

## Tests
- Baseline before your change: 610 passed; the 4 tests in `tests/utils/test_plot_forecast_browser.py`
  fail (need a Playwright Chromium; environment, not code). Use `--ignore=` for that file. Keep that
  file's tests updated to the new design even though they cannot run here.
- Update the existing tests that assert old behavior (charts in forecast/cost runs, `--output`, old
  `plot.yaml` keys, `project-all-projects`, self-contained HTML) — to the new design, not by deleting
  coverage. Add the new tests in the plan's Tests table.
- Plan acceptance bullets are required tests.
- At the end: full `pytest tests -q` (browser tests ignored) green, then persist the report:
  `"$FORECAST_PYTHON" -m pytest tests --ignore=tests/utils/test_plot_forecast_browser.py
  --junitxml=../docs/test/reports/plot-pipeline-junit.xml` and
  `"$FORECAST_PYTHON" ../utils/gen_test_report.py --title "Plot / pipeline (#78)"
  --junit ../docs/test/reports/plot-pipeline-junit.xml --out ../docs/test/reports/plot-pipeline-test-report.md
  --tests econ-forecast/tests/` (check the script's `--help` for exact args). Never under `docs/superpowers/`.

## T9 specifics
- Build `econ-forecast/examples-test/` exactly per design §14.6 and plan T9; regenerate datasets with
  `utils/synt_gen.sh` (same configs/seeds as the old README steps), run `utils/pipeline.sh` on each
  workspace, run the T9 verification and report the results (including the cost figures compared with
  the table in `examples/cost/README.md`).
- Commit `examples-test/` (HTML + the one `assets/plotly.min.js` included; `dataset/` gitignored).

## Handshake first — do NOT write code yet
Reply with: (1) your understanding in ~10 bullets, (2) every ambiguity, contradiction, or concern you
find between plan, design and the real code (file:line), (3) anything you think is missing. Wait for
the coordinator's answers before you start.
