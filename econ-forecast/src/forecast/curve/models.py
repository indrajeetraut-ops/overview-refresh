"""Record and result models for the curve tool."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel


class CurvePoint(BaseModel):
    """One sampled `curve.jsonl` row: preview/inspection only, not the source of truth."""
    pos: float
    level: float


class CurveResult(BaseModel):
    output_dir: Path
    run_id: str
    files: list[str]
    summary: dict[str, float | int | str]
    report_path: Path
