# Task 1 — Curve Creation: design

**Status:** design draft (for review/edit). **Date:** 2026-09-20.
**Companion:** [v2 synth data gen research](../research/v2-synth-data-gen.md).
**Audience:** implementer. Decisions are marked **[decided]**, **[proposed]**, or **[open]**.

## 1. Purpose & scope

Curve-create authors a **reusable, duration-normalized shape** (a dimensionless
multiplier over normalized time) and saves it as an artifact that stage 2 (v2 series-gen)
references. It is a standalone, deterministic generator stage — no consumption data, no
calendar semantics, no spikes.

**In scope:** shape config → PCHIP curve → saved artifact (config + sampled values +
preview + manifest); validation; preview render.
**Non-goals:** no spikes (smooth trend only), no calendar/date semantics baked into the
shape, no token/consumption generation, no binding logic (that is stage 2).

## 2. Curve model **[decided unless noted]**

- **Anchors:** ordered list of `(position, level)`.
  - `level`: dimensionless multiplier. `1.0` = "no change / baseline." **Encoding
    [decided]:** each anchor is a positional pair `[pos, value]`; `value` is a **quoted
    string** where a leading `+`/`-` = **delta** from the previous anchor and no sign =
    **absolute** (see §4). First anchor absolute; deltas accumulate; resolved to absolute
    before PCHIP. Value must match `^[+-]?\d+(\.\d+)?$` and be a string (rejecting a bare,
    unquoted number makes the YAML "+"-drop trap a loud error).
  - **Lower bound [decided]: allow `0.0`.** Safe because PCHIP never overshoots (see §3
    zero-floor guarantee). Validation requires *resolved absolute* levels ≥ 0 and rejects
    delta chains that walk negative.
  - `position`: authored on a **configured integer axis** `axis: {start, end}` (e.g.
    `1..10`, `1..30` monthly-ish, `1..12`) — integers are easier to place than fractions.
    The axis is **abstract**, not calendar days: normalized internally
    `pos -> (pos - start)/(end - start)` and stretched onto the real window at gen time.
    So `end: 30` is authoring granularity, not a 30-day binding.
- **Interpolation:** PCHIP (shape-preserving, no overshoot). `interp` field kept for
  future methods but PCHIP is the only supported value now.
- **Constraints:** ≥ 2 anchors; `axis.start < axis.end`; positions strictly increasing and
  within `[start, end]`; first/last anchor at the axis endpoints (normalize to 0.0/1.0);
  resolved absolute levels within bounds.

## 3. Core helper: `evaluate_curve` **[decided]**

The single piece of math shared by curve-create (preview) and stage 2 (generation):

```
evaluate_curve(anchors: list[(pos, level)], n: int) -> list[float]   # length n
    # builds PchipInterpolator over normalized positions, evaluates at
    # n points pos_i = i/(n-1) for i in 0..n-1  (n == 1 -> [level at pos 0])
```

- Pure, deterministic, no RNG. Same anchors + n → same output.
- **Location [decided]:** new **independent module `src/forecast/curve/`** (e.g.
  `curve/model.py`). Stage 2 imports it; no curve artifact needed at gen time (anchors are
  the source of truth, resampled to the target day count exactly).
- **Zero-floor guarantee [PCHIP-specific]:** PCHIP is monotone per segment and never
  overshoots — on any interval it stays *within* the two surrounding anchor levels.
  Therefore if all (resolved) anchors are ≥ 0, the entire curve is ≥ 0; a `0` anchor is a
  floor the curve touches but does not cross. (A natural cubic spline would undershoot
  below a `0` anchor.) So validating anchors ≥ 0 is sufficient for PCHIP; a cheap
  `min(evaluate_curve(...)) ≥ 0` assertion at gen time is belt-and-suspenders and cannot
  trip for PCHIP. If a non-PCHIP `interp` is ever added, validate the *sampled* curve.

## 4. Config schema (input YAML) **[proposed]**

```yaml
schema_version: 1
name: q3-ramp-and-plateau          # identifier used by stage 2 references
interp: pchip
axis: {start: 1, end: 14}          # abstract integer position axis (NOT calendar days)
anchors:                            # [pos, value]; value is a STRING: signed = delta, unsigned = absolute
  - [1,  "10"]                      # absolute base (first anchor must be absolute)
  - [5,  "-0.5"]                    # delta down  -> 9.5
  - [6,  "-0.8"]                    # delta down  -> 8.7
  - [7,  "+4"]                      # delta up    -> 12.7
  - [14, "12"]                      # absolute    -> 12.0
preview:                            # optional, cosmetic only
  days: 60                          # sample the shape over N days for the preview x-axis
  baseline_tokens: 1000000         # for y-axis units in preview
description: "ramp up then gentle decline"
```

**Position authoring [decided]:** configured integer axis `axis: {start, end}`, abstract
(not calendar days), normalized internally. Chosen over normalized fractions (hard to
picture) and real dates (misleading — the shape is calendar-free).

**Level authoring [decided]:** each anchor is a positional pair `[pos, value]` (YAML
*sequence*, not `{}`). `value` is a **quoted string**: a leading `+`/`-` makes it a
**delta** from the previous anchor, no sign makes it **absolute**. First anchor must be
absolute; deltas accumulate; resolved to absolute before PCHIP.
Rationale: `{a, b}` isn't a valid YAML pair, and YAML silently drops a leading `+`
(`+4` == `4`) — typing `value` as a string and validating it makes the unquoted-`+` trap a
**loud error** instead of a silent absolute.

## 5. CLI integration **[decided: standalone]**

Standalone tool (user requirement), **not** an op of `cli_synt_gen.py`, in the independent
`forecast/curve/` module:

- `forecast/curve/cli_curve.py` with `--config`, `--output`, `--validate-only`, `--open`
  (auto-open the preview — operators iterate heavily), optional `--preview-days N` (sample
  the shape over N days for the preview x-axis; overrides config `preview.days`). Default
  preview is the abstract position axis. No calendar dates enter the curve.
- Dispatch to `run_curve(config, output_dir, ...)` in `forecast/curve/runner.py`, mirroring
  `synthetic/runner.py`'s `run_series` scaffolding (adapt/reuse `_operation`,
  `_check_output`, `_finish`, `_completed`).
- Shell wrapper: `utils/curve.sh` calling `python -m forecast.curve.cli_curve ...`
  (mirrors `synt_gen.sh`; CLAUDE.md `cli_{function}.py` naming; sets PYTHONPATH).

## 6. Output artifacts & folder layout **[decided]**

**Layout** — under an artifacts root, keyed by `<name>/<config-hash>`; config and outputs
live together (all evidence in one place), manifest written **last**:

```
artifacts/curves/
  <name>/
    <config-hash>/          # deterministic id = short sha256 of the config -> "Config X -> Output X"
      config.yaml           # resolved input snapshot (provenance)
      curve.jsonl           # sampled {pos, level} — preview/inspection only; gen re-evaluates from anchors
      preview.html          # interactive Plotly (reuse plot_forecast.py) — the --open artifact
      preview.png           # static matplotlib (reuse presentation.py:_render_png); no kaleido
      report.md
      manifest.json         # written last; records uuid run_id + timestamp + config_sha256
```

- **Config identity, not per-execution:** the subdir is the config hash, so identical
  config → same dir (idempotent), any change → new dir. The uuid `run_id`/timestamp live
  *inside* `manifest.json`, not in the path.
- **One config = one named curve** (single). A "collection" is a folder/glob of configs the
  CLI batches (`--config curves/*.yaml`); not many curves in one file.
- **Reference/read addressing:** `<name>` = all ids under that name; `<name>:<id>` resolves
  to `<name>/<id>`. Used by consumers (preview viewer, index builder, **stage 2**), not
  generation (generation is always input-config → artifact).
- **Root `index.json`:** built by a *separate* indexer that walks
  `artifacts/curves/*/*/manifest.json` + `report.md`; not part of the generate path.
- Existing `<name>/<config-hash>` dir → **friendly no-op** [decided]: the hash keys the
  dir, so an existing dir means the identical config already ran — print the path, exit 0,
  write nothing. (This intentionally differs from other stages' refuse-on-existing rule.)
  Implement as a pre-check in `curve/runner.py` before constructing `DatasetWriter`
  (`DatasetWriter.__enter__` uses `mkdir(exist_ok=False)` and cannot no-op itself).

## 7. Preview rendering **[proposed]**

Both rendered from the same curve/anchor data:
- **`preview.html`** — offline Plotly (`include_plotlyjs="cdn"`), reusing
  `utils/plot_forecast.py` conventions; interactive (zoom/hover), the `--open` target.
- **`preview.png`** — static matplotlib via `forecasting/presentation.py:_render_png`
  (`FigureCanvasAgg.print_png`); for `report.md` / sharing.

X-axis = abstract position axis (or a day index `1..N` when `--preview-days`/`preview.days`
is set); Y-axis = level (× `baseline_tokens` if provided). Both show the interpolated line
plus anchor markers, so the operator eyeballs the shape before stage 2 uses it.

## 8. Validation rules **[proposed]** (`--validate-only` runs these, writes nothing)

- schema_version supported; `interp` == `pchip`.
- ≥ 2 anchors; positions strictly increasing; normalize to [0,1] (first/last endpoints).
- levels within bounds (§2 open lower-bound decision).
- `name` present and well-formed (used as stage-2 reference key).
- `preview.days` / `--preview-days` (if present) a positive integer.
- for non-validate runs: if `<name>/<config-hash>` already exists → friendly no-op (same
  config already generated); otherwise create it fresh.

## 9. Cross-cutting **[decided]**

- **Determinism:** no RNG; reproducible values. Report timestamps / run id differ by run.
- **Logging:** structured JSON via `common.msging.log` (`configure_logging`,
  `get_logger`); one JSON summary to stdout, logs/errors to stderr (module convention).
- **Exit codes:** `0` success/valid/help, `2` args/config/input error, `1` artifact or
  unexpected failure (matches existing ops).
- **Failure cleanup:** on handled failure remove only the dir this invocation created;
  never touch existing dirs/inputs (matches existing stages).

## 10. Testing **[decided]** (`econ-forecast/tests/curve/` — matches the independent module)

- `evaluate_curve`: endpoints hit anchor levels; **no-overshoot** property (values stay
  within neighboring anchor bounds on monotone segments); **stretch exactness** (same
  anchors at n=30/60/90 agree at shared normalized positions); n==1 edge.
- config validation: bad interp, <2 anchors, non-increasing positions, out-of-bounds
  level, missing name, existing output path.
- artifact writing: files present, manifest last, hashes/round-trip, `curve.jsonl` matches
  `evaluate_curve`.
- CLI: `--validate-only` writes nothing; exit codes; stdout JSON summary shape.

## 11. Decisions

**Decided:** position = integer `axis: {start, end}`, abstract/stretchable; level = mixed
absolute/`delta`, base 1.0, allow `0.0` (PCHIP zero-floor); CLI = standalone `cli_curve.py`
+ `curve.sh`; PCHIP-only interp for now.

Also decided: **generate both previews** — `preview.html` (plotly, interactive) +
`preview.png` (matplotlib, static); no kaleido. **Layout** = `artifacts/curves/<name>/
<config-hash>/` with config+outputs together; one config = one curve; `<name>[:id]`
addressing; separate index builder. Stage-2 reference = `<name>[:id]` (resolves the
unique-output question too — the config-hash keys the dir).

Also decided: **level encoding** = positional `[pos, "value"]`, quoted-string value, signed
= delta / unsigned = absolute, first anchor absolute, validated `^[+-]?\d+(\.\d+)?$`.

**Minor defaults (locked unless changed):**
1. `evaluate_curve` in `forecast/curve/model.py` (independent module).
2. Re-run of identical config → friendly no-op (print path, exit 0), not a hard error.
3. Default preview/`curve.jsonl` sample density ~200 points when `preview.days` unset;
   `N` points when `--preview-days N` given.
4. First anchor must be absolute (leading delta rejected).

## 12. Reused components

`common.msging.log` (logging), `forecast.artifact_io` (record/manifest IO, `DatasetWriter`,
`sha256`), `utils/plot_forecast.py` (Plotly patterns), `forecasting/presentation.py:
_render_png` (matplotlib PNG), and `synthetic/runner.py` + `synthetic/config.py` as
structural patterns to adapt. New independent module `src/forecast/curve/`:
`curve/model.py` (`evaluate_curve` + anchor resolution), `curve/config.py`,
`curve/runner.py` (`run_curve`), `curve/preview.py` (html+png), `curve/cli_curve.py`;
plus `utils/curve.sh` and an example `config/synthetic/curves/*.yaml`.
