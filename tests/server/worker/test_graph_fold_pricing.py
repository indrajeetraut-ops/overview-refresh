"""channel_cost_of/carry_of pricing (G2, mvp-gap-closure-plan 2026-09-12).

Both were originally shipped with `usd_micros` hardcoded `None` ("no pricing here" -- the
literal comment). This locks in the fix: priced per model at the session's own date when a
`Pricer` is given, `None` (never a guessed 0, never a partial sum) when it isn't, or
when a contributing model has no rate for that date/kind.
"""
from datetime import date

from analysis.causal_graph.models import Family, Graph, Node, NodeType
from common.model_pricing import Pricer
from server.worker.graph_fold import carry_of, channel_cost_of

PRICING = Pricer.load(None, None)  # the real catalog -- claude-opus-5: $5/$25/$0.50/$6.25/$10 per M
AT = date(2026, 1, 1)  # after every block's effective_from in the real table


def test_channel_cost_is_unpriced_without_a_pricing_table():
    """The pre-fix baseline: no pricing table (or no `at`) given -- `None`, not 0, and tokens
    are still counted regardless."""
    models = {"claude-opus-5": {"input": 1_000_000, "output": 200_000, "cache_read": 0, "cache_write": 100_000}}
    result = channel_cost_of(models)
    assert result["input"] == {"tokens": 1_000_000, "usd_micros": None}
    assert result["output"] == {"tokens": 200_000, "usd_micros": None}
    assert result["recache"] == {"tokens": 100_000, "usd_micros": None}


def test_channel_cost_prices_each_channel_per_model():
    models = {"claude-opus-5": {"input": 1_000_000, "output": 200_000, "cache_read": 0, "cache_write": 100_000}}
    result = channel_cost_of(models, PRICING, AT)
    assert result["input"] == {"tokens": 1_000_000, "usd_micros": 5_000_000}   # 1M @ $5/M
    assert result["output"] == {"tokens": 200_000, "usd_micros": 5_000_000}   # 0.2M @ $25/M
    # combined cache_write with no per-write TTL detail -- the conservative 1h rate ($10/M),
    # the same fallback split_cache_write already uses for an OTel-only request.
    assert result["recache"] == {"tokens": 100_000, "usd_micros": 1_000_000}  # 0.1M @ $10/M


def test_channel_cost_sums_dollars_across_multiple_models_not_tokens_then_one_rate():
    """A session that mixes models must price each model's own tokens at that model's own
    rate -- summing tokens first and pricing once at a single rate would be wrong the moment
    two models disagree on price."""
    models = {
        "claude-opus-5": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0},   # $5
        "claude-haiku-4-5-20251001": {"input": 1_000_000, "output": 0, "cache_read": 0, "cache_write": 0},
    }
    result = channel_cost_of(models, PRICING, AT)
    opus_price = channel_cost_of({"claude-opus-5": models["claude-opus-5"]}, PRICING, AT)["input"]["usd_micros"]
    haiku_price = channel_cost_of(
        {"claude-haiku-4-5-20251001": models["claude-haiku-4-5-20251001"]}, PRICING, AT,
    )["input"]["usd_micros"]
    assert opus_price != haiku_price  # different models, different rates -- would collide if summed-then-priced
    assert result["input"]["usd_micros"] == opus_price + haiku_price


def test_channel_cost_stays_none_for_an_unrecognized_model_even_with_a_pricing_table():
    """Never a guessed 0 -- a model with no rate at all leaves that channel None, tokens still
    counted."""
    models = {"some-future-model-nobody-priced-yet": {"input": 500, "output": 0, "cache_read": 0, "cache_write": 0}}
    result = channel_cost_of(models, PRICING, AT)
    assert result["input"] == {"tokens": 500, "usd_micros": None}


def _assistant_node(node_id: str, model: str, cache_read: int, cache_write: int) -> Node:
    return Node(id=node_id, type=NodeType.ASSISTANT,
               attrs={"model": model, "cache_read_tokens": cache_read, "cache_write_tokens": cache_write})


def test_carry_prices_cache_read_per_model():
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    graph.add(_assistant_node("a1", "claude-opus-5", cache_read=2_000_000, cache_write=0))
    graph.add(_assistant_node("a2", "claude-opus-5", cache_read=1_000_000, cache_write=500_000))
    family = Family(master=graph, children={})

    unpriced = carry_of(family, "s1")
    assert unpriced["cache_read_tokens"] == 3_000_000
    assert unpriced["carry_usd_micros"] is None

    priced = carry_of(family, "s1", PRICING, AT)
    assert priced["cache_read_tokens"] == 3_000_000
    assert priced["carry_usd_micros"] == 1_500_000  # 3M cache_read tokens @ $0.50/M
    # request-shape fields are unaffected by pricing
    assert priced["requests"] == 2
    assert priced["peak_context"] == 2_000_000
