"""Anchor resolution and PCHIP curve evaluation: pure, deterministic, no I/O.

This module holds the one piece of math shared by curve-create (this tool) and, later,
v2 series-generation: `evaluate_curve`. It has no dependency on config, IO, or logging so
it stays trivially reusable and unit-testable.
"""
from __future__ import annotations

import re

from scipy.interpolate import PchipInterpolator

_VALUE_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?$")


def parse_anchor_value(raw: str) -> tuple[float, bool]:
    """Parse one anchor's quoted value string.

    Returns (magnitude, is_delta). A leading `+`/`-` makes it a delta from the previous
    resolved level; no sign makes it absolute. Rejects anything that is not a string, or
    that does not match `^[+-]?\\d+(\\.\\d+)?$` (this is what makes the unquoted-`+` YAML
    trap a loud error instead of a silently-accepted absolute value).
    """
    if not isinstance(raw, str) or not _VALUE_PATTERN.fullmatch(raw):
        raise ValueError(
            "anchor value must be a quoted decimal string matching ^[+-]?\\d+(\\.\\d+)?$ "
            '(e.g. "10", "+4", "-0.5")')
    return float(raw), raw[0] in "+-"


def resolve_anchors(raw_anchors, axis_start: int, axis_end: int) -> list[tuple[float, float]]:
    """Resolve `[pos, "value"]` pairs into normalized `(position, absolute level)` pairs.

    - `position` is on the caller's abstract integer axis `[axis_start, axis_end]`;
      normalized internally to `(pos - axis_start) / (axis_end - axis_start)` -> [0.0, 1.0].
    - `value` is resolved to an absolute level: a leading sign is a delta from the
      previous *resolved* level; no sign is absolute. The first anchor must be absolute.
    - Resolved levels must be >= 0.0 (see `evaluate_curve` for the PCHIP zero-floor
      guarantee this makes safe).

    Raises `ValueError` (translated to a curve `ConfigError`/`InputDataError` by callers)
    on any violated constraint. Pure: no I/O, no RNG.
    """
    if axis_end <= axis_start:
        raise ValueError("axis.end must be greater than axis.start")
    anchors = list(raw_anchors)
    if len(anchors) < 2:
        raise ValueError("at least two anchors are required")
    positions = [pos for pos, _ in anchors]
    if any(isinstance(pos, bool) or not isinstance(pos, int) for pos in positions):
        raise ValueError("anchor positions must be integers")
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError("anchor positions must be strictly increasing")
    if positions[0] != axis_start or positions[-1] != axis_end:
        raise ValueError("the first and last anchor must sit at the axis endpoints")
    if any(pos < axis_start or pos > axis_end for pos in positions):
        raise ValueError("anchor positions must stay within the axis bounds")

    span = axis_end - axis_start
    resolved: list[tuple[float, float]] = []
    level = None
    for index, (pos, raw_value) in enumerate(anchors):
        magnitude, is_delta = parse_anchor_value(raw_value)
        if index == 0 and is_delta:
            raise ValueError(
                f"the first anchor (position {pos}) must be an absolute (unsigned) value, "
                f"not a delta ({raw_value!r})")
        level = magnitude if not is_delta else level + magnitude
        if level < 0.0:
            raise ValueError(
                f"anchor at position {pos} ({raw_value!r}) resolves to a negative level "
                f"({level:g}); levels accumulate from the previous anchor and must stay "
                f">= 0 (a level is a nonnegative multiplier)")
        resolved.append(((pos - axis_start) / span, float(level)))
    return resolved


def evaluate_curve(anchors: list[tuple[float, float]], n: int) -> list[float]:
    """Sample the PCHIP-interpolated curve at `n` evenly spaced normalized positions.

    `anchors` are already-resolved `(normalized_position, absolute_level)` pairs spanning
    [0.0, 1.0] (see `resolve_anchors`); `n` is the number of output samples, evaluated at
    `pos_i = i / (n - 1)` for `i` in `0..n-1` (n == 1 -> the level at position 0.0).

    Pure and deterministic: the same `(anchors, n)` always produces the same output.
    PCHIP (shape-preserving, no overshoot) guarantees the sampled curve never dips below
    the lowest anchor level on any interval, so nonnegative anchors (enforced by
    `resolve_anchors`) imply a nonnegative curve everywhere.
    """
    if n < 1:
        raise ValueError("n must be a positive integer")
    positions = [pos for pos, _ in anchors]
    levels = [level for _, level in anchors]
    if len(positions) < 2:
        raise ValueError("at least two anchors are required to interpolate")
    if positions != sorted(positions) or len(set(positions)) != len(positions):
        raise ValueError("anchor positions must be strictly increasing")
    interpolator = PchipInterpolator(positions, levels)
    if n == 1:
        return [float(interpolator(0.0))]
    return [float(value) for value in interpolator([index / (n - 1) for index in range(n)])]
