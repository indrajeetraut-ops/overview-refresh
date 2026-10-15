"""resume_cost_of pricing (segment-level-pricing plan Phase 3b).

`reload_tokens` are real cache-write tokens re-paid on a real resuming assistant node -- a
faithful (not estimated) figure once priced, same posture as `channel_cost_of`/`carry_of`
(`test_graph_fold_pricing.py`). Before this, `usd_micros`/`total_usd_micros`/
`projected.usd_micros` were hardcoded `None` regardless of whether a pricing table was given.
"""
from datetime import date

from analysis.causal_graph.models import Family, Graph, Node, NodeType
from common.model_pricing import Pricer
from server.worker.graph_fold import resume_cost_of

PRICING = Pricer.load(None, None)  # the real catalog -- claude-opus-5 cache_write (no TTL detail): $10/M
AT = date(2026, 1, 1)  # after every block's effective_from in the real table


def _resumed_session_graph() -> Graph:
    """Two turns, ~2h apart (past the assumed 1h TTL): turn 1 writes 500k cache tokens, then a
    long gap, then turn 2 re-caches 2M tokens (>= half of the 500k live before the gap) -- a
    real reload by `resume_cost_of`'s own rule."""
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    graph.add(Node(id="turn:1", type=NodeType.TURN, ts="2026-01-01T00:00:00Z",
                   attrs={"turn_num": 1}, children=["a1"]))
    graph.add(Node(id="a1", type=NodeType.ASSISTANT, ts="2026-01-01T00:00:05Z", parent_id="turn:1",
                   attrs={"model": "claude-opus-5", "cache_read_tokens": 0, "cache_write_tokens": 500_000}))
    graph.add(Node(id="turn:2", type=NodeType.TURN, ts="2026-01-01T02:00:00Z",
                   attrs={"turn_num": 2}, children=["a2"]))
    graph.add(Node(id="a2", type=NodeType.ASSISTANT, ts="2026-01-01T02:00:10Z", parent_id="turn:2",
                   attrs={"model": "claude-opus-5", "cache_read_tokens": 0, "cache_write_tokens": 2_000_000}))
    return graph


def test_resume_cost_is_unpriced_without_a_pricing_table():
    family = Family(master=_resumed_session_graph(), children={})
    result = resume_cost_of(family, "s1")
    assert result["count"] == 1
    assert result["events"][0]["reload_tokens"] == 2_000_000
    assert result["events"][0]["usd_micros"] is None
    assert result["total_usd_micros"] is None
    assert result["projected"]["usd_micros"] is None


def test_resume_cost_prices_reload_tokens_at_the_reloading_nodes_model():
    family = Family(master=_resumed_session_graph(), children={})
    result = resume_cost_of(family, "s1", PRICING, AT)
    assert result["count"] == 1
    event = result["events"][0]
    assert event["reload_tokens"] == 2_000_000
    assert event["usd_micros"] == 20_000_000  # 2M cache_write tokens @ $10/M (no TTL detail -> 1h rate)
    assert result["total_usd_micros"] == 20_000_000
    # projected re-prices the LAST assistant node's full context as a hypothetical reload
    assert result["projected"]["context_tokens"] == 2_000_000
    assert result["projected"]["usd_micros"] == 20_000_000


def test_resume_cost_total_is_none_when_any_reload_event_is_unpriced():
    """All-or-nothing across events (ground rule 3): one reload on an unrecognized model must
    not let the total look like a real, complete figure."""
    graph = _resumed_session_graph()
    graph.nodes["a2"].attrs["model"] = "some-future-model-nobody-priced-yet"
    family = Family(master=graph, children={})
    result = resume_cost_of(family, "s1", PRICING, AT)
    assert result["count"] == 1
    assert result["events"][0]["usd_micros"] is None
    assert result["total_usd_micros"] is None


def test_resume_cost_is_empty_but_priced_zero_when_there_is_nothing_to_reload():
    """A session with a pricing table but zero reload events gets a real `$0`, not `None` --
    same "priced-and-nothing-to-price" posture as `channel_cost_of`'s per-channel zero."""
    graph = Graph(session_id="s2", vendor="claude_code", source="jsonl", built="complete")
    graph.add(Node(id="turn:1", type=NodeType.TURN, ts="2026-01-01T00:00:00Z",
                   attrs={"turn_num": 1}, children=["a1"]))
    graph.add(Node(id="a1", type=NodeType.ASSISTANT, ts="2026-01-01T00:00:05Z", parent_id="turn:1",
                   attrs={"model": "claude-opus-5", "cache_read_tokens": 0, "cache_write_tokens": 0}))
    family = Family(master=graph, children={})
    result = resume_cost_of(family, "s2", PRICING, AT)
    assert result["count"] == 0
    assert result["total_usd_micros"] == 0  # priced, nothing to reload -- a real zero, not None
    assert result["projected"] is None  # no cache writes at all -> nothing to project either


# ------------------------------------------------------- recoverable share (efficiency note §2, bucket 1)

def test_resume_cost_recoverable_share_is_the_rewritten_context_at_write_minus_read():
    """Only the 500k that was already cached before the gap is recoverable (the other 1.5M is new
    context, written either way), and only the write-over-read premium on it: the do-it-well
    counterfactual still reads that context. 500k @ ($10 - $0.50)/M = $4.75."""
    family = Family(master=_resumed_session_graph(), children={})
    result = resume_cost_of(family, "s1", PRICING, AT)
    event = result["events"][0]
    assert event["ts"] == "2026-01-01T02:00:10Z"
    assert event["recoverable_usd_micros"] == 4_750_000
    assert result["total_recoverable_usd_micros"] == 4_750_000


def test_resume_cost_reload_is_the_resuming_turns_first_request_only():
    """A later write in the resuming turn is that turn's own growth, not part of the reload."""
    graph = _resumed_session_graph()
    graph.nodes["turn:2"].children.append("a3")
    graph.add(Node(id="a3", type=NodeType.ASSISTANT, ts="2026-01-01T02:00:20Z", parent_id="turn:2",
                   attrs={"model": "claude-opus-5", "cache_read_tokens": 2_000_000, "cache_write_tokens": 300_000}))
    result = resume_cost_of(Family(master=graph, children={}), "s1", PRICING, AT)
    assert result["count"] == 1
    assert result["events"][0]["reload_tokens"] == 2_000_000
    assert result["total_usd_micros"] == 20_000_000


def test_resume_cost_recoverable_share_is_unpriced_without_a_pricing_table():
    result = resume_cost_of(Family(master=_resumed_session_graph(), children={}), "s1")
    assert result["events"][0]["recoverable_usd_micros"] is None
    assert result["total_recoverable_usd_micros"] is None
