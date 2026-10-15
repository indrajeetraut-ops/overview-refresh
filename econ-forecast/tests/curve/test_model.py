"""Pure, deterministic curve math: `evaluate_curve`, `resolve_anchors`, `parse_anchor_value`."""
from __future__ import annotations

import pytest

from forecast.curve.model import evaluate_curve, parse_anchor_value, resolve_anchors

# The design-doc example: non-monotonic (up, down, down, up, down), first anchor
# absolute, includes both delta and absolute anchors.
EXAMPLE_ANCHORS = [(1, "10"), (5, "-0.5"), (6, "-0.8"), (7, "+4"), (14, "12")]
EXAMPLE_AXIS = (1, 14)


# --- parse_anchor_value -----------------------------------------------------

def test_parse_anchor_value_absolute():
    assert parse_anchor_value("10") == (10.0, False)


def test_parse_anchor_value_delta_positive():
    assert parse_anchor_value("+4") == (4.0, True)


def test_parse_anchor_value_delta_negative():
    assert parse_anchor_value("-0.5") == (-0.5, True)


def test_parse_anchor_value_rejects_the_yaml_unquoted_plus_trap():
    # Unquoted `+4` parses as the int 4 in YAML, not the string "+4"; this must be a
    # loud error, not a silently-accepted absolute value.
    with pytest.raises(ValueError):
        parse_anchor_value(4)


@pytest.mark.parametrize("raw", ["", "abc", "1.2.3", "++4", "4-", "1e5", None, 4.0])
def test_parse_anchor_value_rejects_malformed_or_non_string(raw):
    with pytest.raises(ValueError):
        parse_anchor_value(raw)


# --- resolve_anchors ---------------------------------------------------------

def test_resolve_anchors_normalizes_positions_and_accumulates_deltas():
    resolved = resolve_anchors(EXAMPLE_ANCHORS, *EXAMPLE_AXIS)
    positions = [pos for pos, _ in resolved]
    levels = [level for _, level in resolved]
    assert positions == pytest.approx([0.0, 4 / 13, 5 / 13, 6 / 13, 1.0])
    # 10 -0.5 -> 9.5; -0.8 -> 8.7; +4 -> 12.7; absolute 12 -> 12.0
    assert levels == pytest.approx([10.0, 9.5, 8.7, 12.7, 12.0])


def test_resolve_anchors_rejects_leading_delta_as_first_anchor():
    with pytest.raises(ValueError, match=r"first anchor \(position 1\).*must be an absolute"):
        resolve_anchors([(1, "+5"), (10, "5")], 1, 10)


def test_resolve_anchors_rejects_delta_chain_that_walks_negative():
    # The message must name the offending anchor's position and resolved level.
    with pytest.raises(ValueError, match=r"position 10.*negative level"):
        resolve_anchors([(1, "2"), (10, "-5")], 1, 10)


def test_resolve_anchors_allows_exact_zero_floor():
    resolved = resolve_anchors([(1, "0.0"), (10, "5")], 1, 10)
    assert resolved[0][1] == 0.0


def test_resolve_anchors_rejects_non_increasing_positions():
    with pytest.raises(ValueError, match="strictly increasing"):
        resolve_anchors([(1, "1"), (5, "2"), (5, "3"), (10, "4")], 1, 10)
    with pytest.raises(ValueError, match="strictly increasing"):
        resolve_anchors([(1, "1"), (6, "2"), (5, "3"), (10, "4")], 1, 10)


def test_resolve_anchors_rejects_endpoints_not_at_axis_bounds():
    with pytest.raises(ValueError, match="axis endpoints"):
        resolve_anchors([(2, "1"), (10, "2")], 1, 10)
    with pytest.raises(ValueError, match="axis endpoints"):
        resolve_anchors([(1, "1"), (9, "2")], 1, 10)


def test_resolve_anchors_rejects_axis_end_not_greater_than_start():
    with pytest.raises(ValueError, match="axis.end must be greater"):
        resolve_anchors(EXAMPLE_ANCHORS, 10, 10)
    with pytest.raises(ValueError, match="axis.end must be greater"):
        resolve_anchors(EXAMPLE_ANCHORS, 14, 1)


def test_resolve_anchors_rejects_fewer_than_two_anchors():
    with pytest.raises(ValueError, match="at least two anchors"):
        resolve_anchors([(1, "1")], 1, 10)


def test_resolve_anchors_rejects_non_integer_or_boolean_position():
    with pytest.raises(ValueError, match="must be integers"):
        resolve_anchors([(1.5, "1"), (10, "2")], 1, 10)
    with pytest.raises(ValueError, match="must be integers"):
        resolve_anchors([(True, "1"), (10, "2")], 1, 10)


# --- evaluate_curve -----------------------------------------------------------

def test_evaluate_curve_endpoints_equal_first_and_last_resolved_level():
    resolved = resolve_anchors(EXAMPLE_ANCHORS, *EXAMPLE_AXIS)
    levels = evaluate_curve(resolved, 60)
    assert levels[0] == pytest.approx(resolved[0][1])
    assert levels[-1] == pytest.approx(resolved[-1][1])


def test_evaluate_curve_no_overshoot_stays_within_anchor_range():
    resolved = resolve_anchors(EXAMPLE_ANCHORS, *EXAMPLE_AXIS)
    anchor_levels = [level for _, level in resolved]
    levels = evaluate_curve(resolved, 200)
    assert min(levels) >= min(anchor_levels) - 1e-9
    assert max(levels) <= max(anchor_levels) + 1e-9


def test_evaluate_curve_nonnegative_when_anchors_touch_zero_floor():
    resolved = resolve_anchors([(1, "0.0"), (3, "+3"), (5, "-3")], 1, 5)
    levels = evaluate_curve(resolved, 50)
    assert min(levels) >= -1e-9


def test_evaluate_curve_stretch_exactness_across_sample_counts():
    resolved = resolve_anchors(EXAMPLE_ANCHORS, *EXAMPLE_AXIS)
    # (n-1) of 4, 8, 16 share positions at every multiple of 1/4: index i at n=5
    # is index 2i at n=9 and index 4i at n=17, all representing position i/4.
    levels_5 = evaluate_curve(resolved, 5)
    levels_9 = evaluate_curve(resolved, 9)
    levels_17 = evaluate_curve(resolved, 17)
    for i in range(5):
        assert levels_5[i] == pytest.approx(levels_9[2 * i], abs=1e-12)
        assert levels_5[i] == pytest.approx(levels_17[4 * i], abs=1e-12)


def test_evaluate_curve_n_equals_one_returns_level_at_position_zero():
    resolved = [(0.0, 3.0), (0.5, 7.0), (1.0, 1.0)]
    assert evaluate_curve(resolved, 1) == pytest.approx([3.0])


def test_evaluate_curve_rejects_n_less_than_one():
    resolved = resolve_anchors(EXAMPLE_ANCHORS, *EXAMPLE_AXIS)
    with pytest.raises(ValueError, match="positive integer"):
        evaluate_curve(resolved, 0)


def test_evaluate_curve_rejects_fewer_than_two_anchors():
    with pytest.raises(ValueError, match="at least two anchors"):
        evaluate_curve([(0.0, 1.0)], 5)


def test_evaluate_curve_rejects_non_increasing_positions():
    with pytest.raises(ValueError, match="strictly increasing"):
        evaluate_curve([(0.0, 1.0), (0.5, 2.0), (0.5, 3.0)], 5)
