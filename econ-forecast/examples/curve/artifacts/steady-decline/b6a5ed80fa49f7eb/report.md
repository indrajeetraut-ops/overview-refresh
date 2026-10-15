# Curve: steady-decline

- Run ID: `5a3ba4d3-f36a-4208-a076-51ed14815689`
- Completed: 2026-09-21T15:52:50.958554+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..10` (abstract, normalized internally)
- Sample count: 60 (day-index 1..60)
- Min level: 1.500000
- Max level: 5.000000

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`5.0`) | 0.000000 | 5.000000 |
| 4 (`-1.0`) | 0.333333 | 4.000000 |
| 7 (`-1.5`) | 0.666667 | 2.500000 |
| 10 (`-1.0`) | 1.000000 | 1.500000 |

## Description

consumption tapering off across the window

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
