# Curve: platform-phases

- Run ID: `6a17aa23-0f33-4478-ad75-f70acd473c5f`
- Completed: 2026-10-10T11:26:42.473783+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..61` (abstract, normalized internally)
- Sample count: 120 (day-index 1..120)
- Min level: 0.970024
- Max level: 1.060000

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.000`) | 0.000000 | 1.000000 |
| 24 (`1.000`) | 0.383333 | 1.000000 |
| 26 (`1.060`) | 0.416667 | 1.060000 |
| 28 (`1.060`) | 0.450000 | 1.060000 |
| 30 (`1.000`) | 0.483333 | 1.000000 |
| 36 (`1.040`) | 0.583333 | 1.040000 |
| 47 (`0.970`) | 0.766667 | 0.970000 |
| 54 (`1.030`) | 0.883333 | 1.030000 |
| 61 (`1.000`) | 1.000000 | 1.000000 |

## Description

Platform team usage level: calm, within +/-6% of 1.0

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
