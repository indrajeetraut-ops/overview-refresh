# Curve: development-phases

- Run ID: `bd4e237f-ead9-47ff-9af0-f1ef57fa5611`
- Completed: 2026-10-10T11:32:03.576097+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..61` (abstract, normalized internally)
- Sample count: 120 (day-index 1..120)
- Min level: 0.960039
- Max level: 1.800000

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.800`) | 0.000000 | 1.800000 |
| 5 (`1.800`) | 0.066667 | 1.800000 |
| 7 (`1.000`) | 0.100000 | 1.000000 |
| 19 (`1.000`) | 0.300000 | 1.000000 |
| 21 (`1.300`) | 0.333333 | 1.300000 |
| 23 (`1.300`) | 0.366667 | 1.300000 |
| 25 (`1.000`) | 0.400000 | 1.000000 |
| 30 (`1.000`) | 0.483333 | 1.000000 |
| 32 (`1.500`) | 0.516667 | 1.500000 |
| 39 (`1.500`) | 0.633333 | 1.500000 |
| 41 (`1.000`) | 0.666667 | 1.000000 |
| 45 (`0.960`) | 0.733333 | 0.960000 |
| 51 (`1.040`) | 0.833333 | 1.040000 |
| 57 (`1.000`) | 0.933333 | 1.000000 |
| 61 (`1.000`) | 1.000000 | 1.000000 |

## Description

Development team usage level: opening crunch, freeze bump, release crunch, quiet end

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
