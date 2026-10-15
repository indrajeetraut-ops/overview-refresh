"""Unit tests for `estimate_reasoning` -- the reported/estimated branch, vendor isolation, the
negative floor, and that the two ingredients always ride along regardless of which branch ran.

Constants used below (ratio 1.52, 27 / -1 / 13) are the claude_code defaults from config.py,
themselves sourced from docs/design/notes/claude_token_usage.md §7, §8, §10.
"""
from common import metrics
from analysis.causal_graph.config import ReasoningConfig, VendorReasoningConfig, default_config
from analysis.causal_graph.reasoning import estimate_reasoning

_MODEL = "claude-opus-5"  # ratio 1.52 under claude_code


def setup_function():
    """Same idiom as `tests/common/msging/test_log_metrics.py`: the registry is one shared
    module-level store, so each test starts from a clean snapshot rather than accumulating
    across the file."""
    metrics.reset_metrics()


def _payload(**over):
    base = {
        "output_tokens": 100,
        "reasoning_tokens": None,
        "estimated_visible_tokens": 0,
        "tool_call_count": 0,
        "content_kind": "text",
        "model": _MODEL,
    }
    base.update(over)
    return base


def _estimate(group, fb=None, *, vendor="claude_code", config=None, model=_MODEL):
    return estimate_reasoning(
        group,
        fb if fb is not None else group[0],
        model=model,
        vendor=vendor,
        config=config or default_config(),
    )


def _reasoning_counts(vendor: str) -> tuple[int, int]:
    """(`estimated`, `floored`) read back from the shared `common.metrics` registry for one
    vendor label -- the same snapshot `causal_graph.counters`/`cli_graph` read (plan Step 3)."""
    snapshot = metrics.metrics()
    key = f'{{"vendor":"{vendor}"}}'
    estimated = snapshot.get("causal_graph.reasoning.estimated", {}).get(key, 0)
    floored = snapshot.get("causal_graph.reasoning.floored", {}).get(key, 0)
    return estimated, floored


# --- reported vs estimated ---------------------------------------------------------------


def test_reported_beats_estimated_and_bypasses_arithmetic():
    """reasoning_tokens present -> that value wins outright, even though the arithmetic branch
    (given this output_tokens/visible/calls) would land somewhere completely different."""
    p = _payload(reasoning_tokens=42, output_tokens=100000, estimated_visible_tokens=1)
    out = _estimate([p])
    assert out["estimated_reasoning_tokens"] == 42
    assert out["reasoning_source"] == "reported"
    assert out["reasoning_ratio"] is None  # no ratio was ever consulted


def test_reported_zero_is_still_reported_not_falsy_estimated():
    """0 is a legitimate reported count, not "nothing reported" -- the branch is on `is not
    None`, not truthiness."""
    p = _payload(reasoning_tokens=0)
    out = _estimate([p])
    assert out["reasoning_source"] == "reported"
    assert out["estimated_reasoning_tokens"] == 0


def test_estimated_when_nothing_reported():
    p = _payload(reasoning_tokens=None, output_tokens=500, estimated_visible_tokens=100,
                 tool_call_count=0, content_kind="text")
    out = _estimate([p])
    assert out["reasoning_source"] == "estimated"
    assert out["reasoning_ratio"] == 1.52
    # no tool calls -> no framing at all
    assert out["estimated_reasoning_tokens"] == round(500 - 1.52 * 100)


def test_missing_output_tokens_yields_null_not_a_crash():
    p = _payload(reasoning_tokens=None)
    del p["output_tokens"]
    out = _estimate([p])
    assert out["estimated_reasoning_tokens"] is None
    assert out["reasoning_source"] is None
    assert out["reasoning_ratio"] is None


# --- ingredients: summed over the group, both branches ----------------------------------


def test_ingredients_summed_over_the_group_reported_path():
    a = _payload(reasoning_tokens=7, estimated_visible_tokens=10, tool_call_count=1)
    b = _payload(reasoning_tokens=7, estimated_visible_tokens=15, tool_call_count=2)
    out = _estimate([a, b], fb=a)
    assert out["estimated_visible_tokens"] == 25
    assert out["tool_call_count"] == 3


def test_ingredients_summed_over_the_group_estimated_path():
    a = _payload(estimated_visible_tokens=10, tool_call_count=1, content_kind="tool_use")
    b = _payload(estimated_visible_tokens=15, tool_call_count=1, content_kind="mixed")
    out = _estimate([a, b], fb=a)
    assert out["estimated_visible_tokens"] == 25
    assert out["tool_call_count"] == 2


# --- tool-call framing --------------------------------------------------------------------


def test_framing_applies_only_when_there_are_calls():
    no_calls = _payload(output_tokens=200, estimated_visible_tokens=50, tool_call_count=0)
    out = _estimate([no_calls])
    assert out["estimated_reasoning_tokens"] == round(200 - 1.52 * 50)  # no -1/+27/+13 at all


def test_framing_27n_minus_1_plus_13_when_text_present():
    p = _payload(output_tokens=1000, estimated_visible_tokens=0, tool_call_count=2,
                 content_kind="mixed")
    out = _estimate([p])
    framing = 27 * 2 - 1 + 13
    assert out["estimated_reasoning_tokens"] == round(1000 - 0 - framing)


def test_framing_excludes_text_block_when_no_text_present():
    p = _payload(output_tokens=1000, estimated_visible_tokens=0, tool_call_count=2,
                 content_kind="tool_use")
    out = _estimate([p])
    framing = 27 * 2 - 1  # no +13: no record in the group carries text/mixed
    assert out["estimated_reasoning_tokens"] == round(1000 - 0 - framing)


# --- negative floor -----------------------------------------------------------------------


def test_negative_estimate_floors_at_zero_and_is_counted():
    p = _payload(output_tokens=10, estimated_visible_tokens=1000, tool_call_count=0)
    out = _estimate([p], vendor="claude_code")
    assert out["estimated_reasoning_tokens"] == 0
    estimated, floored = _reasoning_counts("claude_code")
    assert estimated == 1
    assert floored == 1


def test_non_negative_estimate_is_not_counted_as_floored():
    p = _payload(output_tokens=100000, estimated_visible_tokens=1, tool_call_count=0)
    _estimate([p], vendor="claude_code")
    estimated, floored = _reasoning_counts("claude_code")
    assert estimated == 1
    assert floored == 0


def test_reported_path_does_not_touch_the_floor_counters():
    p = _payload(reasoning_tokens=5)
    _estimate([p], vendor="claude_code")
    estimated, floored = _reasoning_counts("claude_code")
    assert estimated == 0
    assert floored == 0


# --- vendor isolation -----------------------------------------------------------------------


def test_codex_never_picks_up_claude_constants():
    """Same model string that would score 1.52 under claude_code; vendor=codex must use its
    own section (ratio 1.0, zero framing), never fall through to claude_code's."""
    p = _payload(output_tokens=1000, estimated_visible_tokens=100, tool_call_count=3,
                 content_kind="mixed", model=_MODEL)
    out = _estimate([p], vendor="codex")
    assert out["reasoning_ratio"] == 1.0  # not 1.52
    # zero framing: no -1/+27*3/+13 subtracted at all
    assert out["estimated_reasoning_tokens"] == round(1000 - 1.0 * 100)


def test_unknown_vendor_falls_through_to_ratio_one_zero_framing_not_claude_code():
    """A vendor config.py has never heard of must not silently borrow claude_code's section --
    that is the failure that would be invisible in the output (design plan, verification)."""
    p = _payload(output_tokens=1000, estimated_visible_tokens=100, tool_call_count=3,
                 content_kind="mixed", model=_MODEL)
    out = _estimate([p], vendor="some_future_vendor")
    assert out["reasoning_ratio"] == 1.0
    assert out["estimated_reasoning_tokens"] == round(1000 - 1.0 * 100)


def test_default_config_declares_both_known_vendors_with_distinct_constants():
    cfg = default_config()
    assert cfg.vendors["claude_code"].tool_call_tokens == 27
    assert cfg.vendors["claude_code"].ratio_by_model["claude-opus-5"] == 1.52
    assert cfg.vendors["codex"].tool_call_tokens == 0
    assert cfg.vendors["codex"].ratio_default == 1.0
    # codex's own section must not equal claude_code's -- proves they're separately keyed.
    assert cfg.vendors["codex"] != cfg.vendors["claude_code"]


def test_for_vendor_unknown_returns_bare_config_not_claude_code_object():
    cfg = ReasoningConfig()
    unmeasured = cfg.for_vendor("totally_unknown")
    assert unmeasured == VendorReasoningConfig()
    assert unmeasured is not cfg.vendors["claude_code"]


def test_ratio_for_longest_prefix_match():
    vc = VendorReasoningConfig(
        ratio_by_model={"claude-opus": 1.1, "claude-opus-5": 1.52}, ratio_default=1.0
    )
    assert vc.ratio_for("claude-opus-5-20260101") == 1.52  # longer, more specific prefix wins
    assert vc.ratio_for("claude-opus-4-8") == 1.1  # matches the shorter prefix only
    assert vc.ratio_for("gpt-5") == 1.0  # no match at all -> default
    assert vc.ratio_for(None) == 1.0
