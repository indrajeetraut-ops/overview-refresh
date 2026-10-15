# Curve: growth-with-launch-spike

- Run ID: `583c82c9-02c4-4cc4-9819-749cdcd3b765`
- Completed: 2026-09-24T00:30:07.352875+00:00
- Interpolation: `pchip`
- Axis (as authored): `1..20` (abstract, normalized internally)
- Sample count: 60 (day-index 1..60)
- Min level: 1.000000
- Max level: 4.975272

## Anchors (resolved)

| Position (axis) | Normalized position | Resolved level |
|---:|---:|---:|
| 1 (`1.0`) | 0.000000 | 1.000000 |
| 8 (`+0.5`) | 0.368421 | 1.500000 |
| 9 (`+3.5`) | 0.421053 | 5.000000 |
| 10 (`-3.3`) | 0.473684 | 1.700000 |
| 20 (`+0.8`) | 1.000000 | 2.500000 |

## Description

gentle ramp with a sharp mid-window launch spike that settles back to the ramp

## Files

- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only (generation re-evaluates from anchors, not this file).
- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).
- `preview.png`: static chart for sharing.
- `config.yaml`: resolved input configuration (provenance).
- `manifest.json`: run metadata and file hashes.
