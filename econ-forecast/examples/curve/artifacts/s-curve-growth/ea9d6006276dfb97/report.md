# Curve: s-curve-growth

- Run ID: `3ac77e48-1c78-4b85-a140-5c86d6ffb010`
- Completed: 2026-09-21T15:52:51.876524+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..30` (abstract, normalized internally)
- Sample count: 120 (day-index 1..120)
- Min level: 1.000000
- Max level: 7.600000

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.0`) | 0.000000 | 1.000000 |
| 10 (`1.3`) | 0.310345 | 1.300000 |
| 16 (`+3.0`) | 0.517241 | 4.300000 |
| 22 (`+2.5`) | 0.724138 | 6.800000 |
| 26 (`7.4`) | 0.862069 | 7.400000 |
| 30 (`7.6`) | 1.000000 | 7.600000 |

## Description

adoption S-curve: slow start, steep middle, high plateau

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
