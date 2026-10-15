# Task 1 — Curve Creation utility (implementation plan)

## Context
v2 synthetic data generation replaces the fixed per-profile baseline
(`synthetic/series.py:172`) with an operator-authored **shape curve** — a dimensionless,
duration-normalized multiplier over time. Curve Creation is the standalone stage that
authors and saves those shapes; stage 2 (v2 series-gen, separate task) consumes them.
Design discussion + decisions live in
`econ-forecast/docs/research/v2-synth-data-gen.md`; the fuller design draft is
`econ-forecast/docs/design/curve-creation-design.md`. This plan is the implementable
breakdown for Task 1 only.

Requirements (from user): **standalone utility**; **integrated Plotly viewing** (operators
will iterate heavily); **config-driven**; a defined **output shape**; a **CLI + shell
wrapper** where input config + output dir are the only inputs, and **every run produces a
unique output** so repeated tweak-and-rerun cycles never collide.

## Design decisions

- **Standalone tool**, not an op of `cli_synt_gen.py`: new `cli_curve.py` +
  `utils/curve.sh` (CLAUDE.md `cli_{function}.py` naming). Own config, own runner path.
- **Curve model:** anchors are positional pairs `[pos, "value"]` on an abstract integer
  `axis: {start, end}` (normalized to `[0,1]` internally; duration-independent). `value` is
  a **quoted string**: leading `+`/`-` = delta from previous anchor, unsigned = absolute
  (first anchor absolute); validated `^[+-]?\d+(\.\d+)?$`, resolved to absolute before
  PCHIP. `level` is a dimensionless multiplier (`1.0` = no change), `0.0` allowed (PCHIP
  never overshoots below the anchors). PCHIP via `scipy.interpolate.PchipInterpolator`.
- **Core helper `evaluate_curve(anchors, n) -> list[float]`** in new `synthetic/curve.py`:
  pure/deterministic, evaluates PCHIP at `pos_i = i/(n-1)`. Shared by this tool's preview
  and (later) stage-2 generation; resamples exactly to any day count.
- **Integrated viewing:** every run writes `preview.html` (offline Plotly, `include_
  plotlyjs="cdn"`, reusing `utils/plot_forecast.py` figure conventions) showing the PCHIP
  line + anchor markers. `--open` auto-opens it (fast edit→run→view loop). Optional
  `--preview-days N` samples the shape over N days for the preview x-axis (no calendar
  dates enter the curve — it stays duration-agnostic).
- **Layout — config identity, not per-execution:** `--output` is the artifacts root; each
  run writes `<name>/<config-hash>/` (config-hash = short sha256 of the config). Identical
  config → same dir (idempotent, "Config X → Output X"); any change → new dir. The uuid
  `run_id` + timestamp are recorded *inside* `manifest.json`, not in the path. Config and
  outputs live together in that dir. One config = one named curve (a collection = a
  folder/glob of configs). `<name>[:id]` is the read/reference selector (consumers +
  stage 2), not a generation input. A root `index.json` is built by a separate indexer.

## Output shape (`artifacts/curves/<name>/<config-hash>/`, manifest written LAST)

| File | Contents |
|---|---|
| `config.yaml` | resolved input config (provenance) |
| `curve.jsonl` | sampled `{pos, level}` (fixed resolution) — inspection/preview only; stage 2 re-evaluates from anchors |
| `preview.html` | Plotly (interactive): PCHIP line + anchor markers — the `--open` artifact |
| `preview.png` | matplotlib (static, `presentation.py:_render_png`) — for `report.md`/sharing; no kaleido |
| `report.md` | anchor summary, interp, min/max level, run info (reuse `reporting.render_report` or a small curve report) |
| `manifest.json` | schema/versions, `run_id`, anchor count, interp, `config_sha256`, file hashes, provenance |

## Files to create
- `econ-forecast/src/forecast/synthetic/curve.py` — curve model + `evaluate_curve` + sampler.
- `econ-forecast/src/forecast/synthetic/cli_curve.py` — standalone CLI (`--config`,
  `--output`, `--validate-only`, `--open`, `--preview-days`).
- `run_curve(config, output_dir, ...)` — new runner path (in `synthetic/runner.py` or a new
  `curve_runner.py`) mirroring `run_series`: `_operation`/`_check_output`/`DatasetWriter`/
  `_finish`/`_completed`.
- Curve config loader/validation — extend `synthetic/config.py` (Pydantic, like
  `load_series_config`).
- `econ-forecast/utils/curve.sh` — wrapper: `python -m forecast.synthetic.cli_curve ...`
  (mirror `utils/synt_gen.sh`; sets PYTHONPATH).
- `econ-forecast/config/synthetic/curve.yaml` — maintained example config.
- Tests in `econ-forecast/tests/synthetic/` (see Verification).

## Reused components
- `forecast.artifact_io.DatasetWriter` / `sha256` / `read_records` — artifact IO + manifest.
- `synthetic/runner.py` scaffold: `_operation`, `_check_output`, `_finish`, `_completed`.
- `common.msging.log` (`configure_logging`, `get_logger`) — structured JSON logging.
- `utils/plot_forecast.py` — Plotly figure conventions for `preview.html`.
- `synthetic/config.py`, `synthetic/errors.py`, `synthetic/reporting.py` — patterns.

## Cross-cutting (match existing stages)
- Deterministic (no RNG). Exit codes: `0` success/valid/help, `2` args/config/input, `1`
  artifact/unexpected. One JSON summary to stdout; JSON logs/errors to stderr. Handled
  failure removes only the dir it created. `schema_version` on config + manifest.

## Decisions to confirm (recommendations in place; redirect at approval)
1. Position authoring form — normalized `pos: 0.0–1.0` (recommended) vs `day`+`duration_days` vs `date`.
2. `level` lower bound — allow `0.0` (recommended) vs require `> 0`.
3. Unique-output mechanism — content-hash subdir (recommended) vs operator-supplied fresh `--output`.
4. Stage-2 reference scheme — by `name` (recommended) vs output path vs registry.

## Verification
- Unit (`tests/synthetic/test_curve.py`): `evaluate_curve` endpoints hit anchor levels;
  no-overshoot on monotone segments; stretch-exactness (n=30/60/90 agree at shared
  positions); `n==1` edge.
- Config validation: bad `interp`, <2 anchors, non-increasing `pos`, out-of-bounds level,
  missing `name`, existing output.
- CLI (`test_curve_cli.py`): `--validate-only` writes nothing; exit codes; stdout summary
  shape; manifest written last; `curve.jsonl` matches `evaluate_curve`.
- Manual: `./econ-forecast/utils/curve.sh --config econ-forecast/config/synthetic/curve.yaml
  --output econ-forecast/artifacts/curves --open` → eyeball `preview.html`; tweak anchors,
  rerun, confirm a new unique subdir and updated shape.
- Run `venv/bin/python -m pytest econ-forecast/tests/synthetic -q`.
