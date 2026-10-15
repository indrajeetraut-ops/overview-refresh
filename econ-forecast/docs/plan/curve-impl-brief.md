# Curve Creation — implementation brief (for the implementing agent)

You are implementing **Task 1: Curve Creation**, a standalone utility in the
`econ-forecast` module. This brief points you at the authoritative spec and states the
non-negotiable constraints. **Do not start coding when you first receive this** — see
"Handshake" at the bottom.

## Authoritative spec (read these first)
- Design (authoritative): `econ-forecast/docs/design/curve-creation-design.md`
- Plan: `econ-forecast/docs/plan/curve-creation-plan.md`
- Background/decisions: `econ-forecast/docs/research/v2-synth-data-gen.md`

If anything below conflicts with the design doc, the design doc wins — but flag the
conflict to me rather than silently choosing.

## What to build
A deterministic tool that turns an operator-authored **shape config** into a saved
**curve artifact**. A curve = anchors `[pos, "value"]` on an abstract integer axis,
PCHIP-interpolated into a dimensionless, duration-normalized multiplier over time.

**Independent module `src/forecast/curve/`** (not under `synthetic/`), suggested files:
- `curve/model.py` — anchor resolution (signed string → delta/absolute; first anchor
  absolute; accumulate; PCHIP zero-floor) + `evaluate_curve(anchors, n) -> list[float]`
  (pure, deterministic; evaluates PCHIP at `pos_i = i/(n-1)`).
- `curve/config.py` — Pydantic config load + validation (mirror `synthetic/config.py`).
- `curve/runner.py` — `run_curve(config, output_dir, ...)` adapting the
  `synthetic/runner.py` scaffold (`_operation`, `_check_output`, `_finish`, `_completed`,
  `DatasetWriter`, manifest written LAST).
- `curve/preview.py` — `preview.html` (Plotly, offline, reuse `utils/plot_forecast.py`
  conventions) + `preview.png` (matplotlib, reuse `forecasting/presentation.py:_render_png`).
- `curve/cli_curve.py` — CLI (`--config`, `--output`, `--validate-only`, `--open`,
  `--preview-days N`).
- `utils/curve.sh` — wrapper calling `python -m forecast.curve.cli_curve ...` (mirror
  `utils/synt_gen.sh`; set PYTHONPATH).
- Example config `econ-forecast/config/synthetic/curves/example.yaml`.

## Output artifact layout (see design §6)
`artifacts/curves/<name>/<config-hash>/` containing `config.yaml`, `curve.jsonl`,
`preview.html`, `preview.png`, `report.md`, `manifest.json` (manifest last). Config-hash
keys the dir (idempotent). Re-run of identical config → friendly no-op (print path, exit 0).

## Hard constraints (repo governance — CLAUDE.md)
- Python 3.11+. Use the repo venv at `/Users/sumandra/projects/talium/forecasting/code-econ/venv`.
- **Structured JSON logging** via `common.msging.log` (`configure_logging`, `get_logger`);
  one JSON summary to stdout, logs/errors to stderr.
- Exit codes: `0` success/valid/help, `2` args/config/input error, `1` artifact/unexpected.
- CLI entry file MUST be named `cli_curve.py` (never bare `cli.py`).
- **No new dependencies** — scipy, plotly, matplotlib, pydantic, pyyaml are all present.
  If you think you need one, stop and ask.
- **Never reference Claude / Claude Code** anywhere in code, comments, or commit messages.
- Add a `# TODO:` in-source for anything intentionally deferred; remove it when done.
- Deterministic: no RNG. Same config → same curve values.
- Do NOT modify existing forecasting/synthetic code except tiny, unavoidable, additive
  hooks — and flag any such change to me first.

## Environment
- Work ONLY in this worktree: `/Users/sumandra/projects/talium/forecasting/code-econ/.claude/worktrees/curve-impl`.
  Do not touch the main checkout.
- Run python as: `PYTHONPATH="<wt>/econ-forecast/src:<wt>/src" <repo>/venv/bin/python ...`
  (the shell wrapper should set this itself).

## Tests
Tests are a **separate step after implementation** — but write code to be testable
(pure `evaluate_curve`, injectable output dir, validation separable from IO). Do not skip
error paths. I will direct the test task separately.

## Handshake (do this FIRST, before any code)
Read the three spec docs and the reuse targets (`synthetic/runner.py`, `artifact_io.py`,
`synthetic/config.py`, `utils/plot_forecast.py`, `forecasting/presentation.py`). Then
reply with:
1. Anything ambiguous, underspecified, or internally inconsistent in the spec.
2. Any conflict between the design and the actual existing code/patterns.
3. Your concrete file-by-file implementation outline.
4. Any questions for me.
**Do not write or modify any code yet.** Wait for my explicit "go".

## Resolutions (answers to the review pass — these are binding)

1. **Existing dir = friendly no-op**, not refuse. The config-hash keys the dir, so an
   existing dir means the identical config already ran → print the path, exit 0, write
   nothing. Implement as a pre-check in `curve/runner.py` *before* `DatasetWriter`
   (design §6/§8 fixed to match).
2. **Errors:** define curve-local `curve/errors.py` (`CurveError` base, `ConfigError`,
   `InputDataError`, `ArtifactError`). `forecast.artifact_io` raises
   `forecast.synthetic.errors.{ArtifactError,InputDataError}` — **catch those at the
   `curve/runner.py` boundary and re-raise as curve's own**, so `cli_curve.py`'s exit-code
   dispatch only handles curve error types. Do NOT modify `artifact_io.py` or
   `synthetic/errors.py`.
3. **Adapt, don't import, the runner scaffold.** Copy-and-trim `_operation` /
   `_check_output` / `_finish` / `_completed` semantics into `curve/runner.py` with a new
   curve-local `CurveResult` model and curve-specific manifest fields (no `org_id`,
   `start_date`, etc.). Reuse `DatasetWriter`/`sha256`/`read_records` from `artifact_io`.
4. **Sample resolution:** `curve.jsonl`, `preview.html`, and `preview.png` all sample at
   the **same** N — `--preview-days`/`preview.days` if set, else default ~200.
5. **`report.md`:** a small, curve-specific ad-hoc renderer is fine (anchor table, interp,
   resolved min/max level, axis, run info). Do NOT reuse `synthetic/reporting.py`.
6. **Logging event name:** `DatasetWriter` emits `"synthetic.artifact_written"` — leave
   `artifact_io.py` unchanged; emit your own curve-scoped log events where `curve/` controls
   logging. Acceptable minor wart; do not touch shared infra.
7. **Determinism/no-op idempotency:** config-hash = sha256 over the *resolved* config bytes
   (as `_finish` does with `config.model_dump(mode="json")` + `yaml.safe_dump(sort_keys=True)`).
   `plan.md` is stale on file layout/open items — follow `design.md` (now updated); ignore
   plan.md where they differ.

**GO — implement now** per the above.
