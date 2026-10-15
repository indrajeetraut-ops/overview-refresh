# Curve: ramp-and-plateau

- Run ID: `2e898274-d695-4534-bb4c-b2f57c33996d`
- Completed: 2026-09-21T15:52:50.147790+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..12` (abstract, normalized internally)
- Sample count: 90 (day-index 1..90)
- Min level: 1.000000
- Max level: 3.599987

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.0`) | 0.000000 | 1.000000 |
| 4 (`+1.5`) | 0.272727 | 2.500000 |
| 8 (`+1.0`) | 0.636364 | 3.500000 |
| 10 (`3.6`) | 0.818182 | 3.600000 |
| 12 (`3.5`) | 1.000000 | 3.500000 |

## Description

slow ramp through the quarter, then a plateau

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
