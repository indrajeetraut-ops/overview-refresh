"""cache_busts_of (efficiency note §2, bucket 2): a request that re-writes most of an already-cached
context while that cache should still be warm -- priced as the write-over-read premium on the
context it re-wrote."""
from datetime import date

from analysis.causal_graph.models import Family, Graph, Node, NodeType
from common.model_pricing import Pricer
from server.worker.graph_fold import cache_busts_of

PRICING = Pricer.load(None, None)  # claude-opus-5: cache_read $0.50/M, cache_write (no TTL detail -> 1h) $10/M
AT = date(2026, 1, 1)


def _graph(*nodes: Node) -> Family:
    graph = Graph(session_id="s1", vendor="claude_code", source="jsonl", built="complete")
    for node in nodes:
        graph.add(node)
    return Family(master=graph, children={})


def _req(node_id: str, ts: str, cache_read: int, cache_write: int, model: str = "claude-opus-5") -> Node:
    return Node(id=node_id, type=NodeType.ASSISTANT, ts=ts,
               attrs={"model": model, "cache_read_tokens": cache_read, "cache_write_tokens": cache_write})


def test_a_warm_cache_rewritten_mid_session_is_a_bust():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    _req("a2", "2026-01-01T00:00:30Z", 0, 110_000))
    result = cache_busts_of(family, "s1", PRICING, AT)
    assert result["count"] == 1
    event = result["events"][0]
    assert (event["ts"], event["gap_s"], event["context_before"], event["rewrite_tokens"]) == \
        ("2026-01-01T00:00:30Z", 30, 100_000, 110_000)
    # only the 100k already cached is recoverable; 100k @ ($10 - $0.50)/M
    assert event["usd_micros"] == 950_000
    assert result["total_usd_micros"] == 950_000


def test_ordinary_growth_is_not_a_bust():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    _req("a2", "2026-01-01T00:00:30Z", 100_000, 5_000))
    result = cache_busts_of(family, "s1", PRICING, AT)
    assert result["count"] == 0
    assert result["total_usd_micros"] == 0  # priced, nothing found -- a real zero


def test_a_rewrite_past_the_ttl_is_a_resume_not_a_bust():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    _req("a2", "2026-01-01T02:00:00Z", 0, 110_000))
    assert cache_busts_of(family, "s1", PRICING, AT)["count"] == 0


def test_a_model_switch_is_an_explained_rewrite():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    _req("a2", "2026-01-01T00:00:30Z", 0, 110_000, model="claude-sonnet-5"))
    assert cache_busts_of(family, "s1", PRICING, AT)["count"] == 0


def test_a_compaction_in_between_is_an_explained_rewrite():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    Node(id="c1", type=NodeType.COMPACTION, ts="2026-01-01T00:00:10Z", attrs={}),
                    _req("a2", "2026-01-01T00:00:30Z", 0, 110_000))
    assert cache_busts_of(family, "s1", PRICING, AT)["count"] == 0


def test_a_small_context_is_below_the_detection_floor():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 9_000, 1_000),
                    _req("a2", "2026-01-01T00:00:30Z", 0, 11_000))
    assert cache_busts_of(family, "s1", PRICING, AT)["count"] == 0


def test_busts_are_unpriced_without_a_pricing_table():
    family = _graph(_req("a1", "2026-01-01T00:00:00Z", 90_000, 10_000),
                    _req("a2", "2026-01-01T00:00:30Z", 0, 110_000))
    result = cache_busts_of(family, "s1")
    assert result["count"] == 1
    assert result["events"][0]["usd_micros"] is None
    assert result["total_usd_micros"] is None
