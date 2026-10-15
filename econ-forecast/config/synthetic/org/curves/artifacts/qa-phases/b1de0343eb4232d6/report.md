# Curve: qa-phases

- Run ID: `270c5865-0e1f-460a-b7d1-472b0d4f6446`
- Completed: 2026-10-10T11:32:06.951556+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..61` (abstract, normalized internally)
- Sample count: 120 (day-index 1..120)
- Min level: 0.960007
- Max level: 1.700000

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.700`) | 0.000000 | 1.700000 |
| 5 (`1.700`) | 0.066667 | 1.700000 |
| 7 (`1.000`) | 0.100000 | 1.000000 |
| 18 (`1.000`) | 0.283333 | 1.000000 |
| 20 (`1.150`) | 0.316667 | 1.150000 |
| 22 (`1.150`) | 0.350000 | 1.150000 |
| 24 (`1.000`) | 0.383333 | 1.000000 |
| 39 (`1.000`) | 0.633333 | 1.000000 |
| 41 (`1.400`) | 0.666667 | 1.400000 |
| 44 (`1.400`) | 0.716667 | 1.400000 |
| 46 (`1.000`) | 0.750000 | 1.000000 |
| 47 (`1.040`) | 0.766667 | 1.040000 |
| 53 (`0.960`) | 0.866667 | 0.960000 |
| 61 (`1.000`) | 1.000000 | 1.000000 |

## Description

QA team usage level: test cycle at the start, bump, 1.4x test cycle, quiet end

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
