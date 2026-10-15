"""Small, curve-specific report renderer (not a reuse of `synthetic/reporting.py`)."""
from __future__ import annotations

from .config import CurveConfig


def render_report(config: CurveConfig, resolved: list[tuple[float, float]], levels: list[float],
                   *, run_id: str, created_at: str, sample_count: int, day_axis: bool) -> str:
    sample_note = (f"day-index 1..{sample_count}" if day_axis else "normalized position 0.0..1.0")
    lines = [
        f"# Curve: {config.name}", "",
        f"- Run ID: `{run_id}`",
        f"- Completed: {created_at}",
        f"- Interpolation: `{config.interp}`",
        f"- Axis (as authored): `{config.axis.start}..{config.axis.end}` (abstract, normalized internally)",
        f"- Sample count: {sample_count} ({sample_note})",
        f"- Min level: {min(levels):.6f}",
        f"- Max level: {max(levels):.6f}",
        "", "## Anchors (resolved)", "",
        "| Position (axis) | Normalized position | Resolved level |",
        "|---:|---:|---:|",
    ]
    for (raw_pos, raw_value), (norm_pos, level) in zip(config.anchors, resolved):
        lines.append(f"| {raw_pos} (`{raw_value}`) | {norm_pos:.6f} | {level:.6f} |")
    if config.description:
        lines += ["", "## Description", "", config.description]
    lines += [
        "", "## Files", "",
        "- `curve.jsonl`: sampled `{pos, level}` pairs, preview/inspection only "
        "(generation re-evaluates from anchors, not this file).",
        "- `preview.html`: interactive Plotly chart (PCHIP line + anchor markers).",
        "- `preview.png`: static chart for sharing.",
        "- `config.yaml`: resolved input configuration (provenance).",
        "- `manifest.json`: run metadata and file hashes.",
    ]
    return "\n".join(lines) + "\n"
