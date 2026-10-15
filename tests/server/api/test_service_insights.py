from datetime import datetime, timedelta, timezone

from server.api.single_user import service
from server.api.config import ApiConfig, InsightsConfig
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)

#: STORY §2's literal banned-words table -- checked against every generated observation/
#: action_text, the same discipline the design doc demands of the narrator line.
_BANNED_WORDS = ("waste", "wasted", "wasteful", "inefficient", "overkill", "overuse",
                 "you should have", "error-prone", "bad session")


def _seed(s, session_id, datasource, *, cost_effective_usd_micros=0, otel=None, files=None,
          info=None, tools=None, models=None, turns=None):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent="claude_code", status="live", started_at=NOW, last_event_at=NOW,
                     signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models=models or {}, tools=tools or {}, files=files or {}, skills=[], subagents={}, pr_list=[],
        turns=turns or [], turn_count=0, long_turn_count=0, info=info or {}, otel=otel or {},
        cost_effective_usd_micros=cost_effective_usd_micros, datasource=datasource,
    ))


def _insights(s, catalog, **kwargs):
    config = kwargs.pop("config", ApiConfig())
    return service.insights(s, catalog, config, USER, NOW - timedelta(days=1),
                            NOW + timedelta(days=1))


def _assert_lexicon_clean(card):
    text = f"{card['observation']} {card.get('action_text') or ''}".lower()
    for word in _BANNED_WORDS:
        assert word not in text, f"insight #{card['insight_id']}: banned word {word!r} in {text!r}"


# ----------------------------------------------------------------------- #11

_DESIGN_SEGMENTS = [{"index": 0, "label": "DESIGN", "confidence": 0.75, "turns": 5}]
_RESEARCH_SEGMENTS = [{"index": 0, "label": "COMPREHEND", "confidence": 0.7, "turns": 3}]


def test_insight_11_reports_the_dominant_model_for_the_costliest_type(engine, catalog):
    with session_scope() as s:
        _seed(s, "design-1", "jsonl_only", cost_effective_usd_micros=9_000_000,
             models={"claude-opus-5": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}},
             info={"segments": _DESIGN_SEGMENTS})
        _seed(s, "research-1", "jsonl_only", cost_effective_usd_micros=100_000,
             models={"claude-sonnet-5": {"input": 200, "output": 100, "cache_read": 0, "cache_write": 0}},
             info={"segments": _RESEARCH_SEGMENTS})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 11)
    assert card["evidence"]["type"] == "DESIGN"
    assert card["evidence"]["dominant_model"] == "claude-opus-5"
    assert card["evidence"]["share"] == 1.0
    assert card["evidence"]["sessions"] == 1
    assert card["est_usd"] == 9.0
    _assert_lexicon_clean(card)


def test_insight_11_uses_the_segment_with_the_most_turns_as_the_session_type():
    from server.api.manager.aggregate import dominant_segment_label

    segments = [
        {"index": 0, "label": "COMPREHEND", "turns": 2},
        {"index": 1, "label": "IMPLEMENT", "turns": 8},
    ]
    assert dominant_segment_label(segments) == "IMPLEMENT"


def test_insight_11_absent_without_segments(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             models={"claude-opus-5": {"input": 1000, "output": 500, "cache_read": 0, "cache_write": 0}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 11 for c in result["insights"])


# ----------------------------------------------------------------------- #14

def test_insight_14_flags_unverified_file_changes(engine, catalog):
    with session_scope() as s:
        _seed(s, "unverified", "jsonl_only", cost_effective_usd_micros=1_000_000,
             files={"a.py": {"lines_added": 10, "lines_removed": 2}},
             info={"practices": {"verified": False}})
        _seed(s, "verified", "jsonl_only", cost_effective_usd_micros=500_000,
             files={"b.py": {"lines_added": 3, "lines_removed": 1}},
             info={"practices": {"verified": True}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 14)
    assert card["sessions"] == ["unverified"]
    assert card["est_usd"] == 1.0
    assert card["kind"] == "practice"
    assert card["mode"] == "jsonl"
    _assert_lexicon_clean(card)


def test_insight_14_absent_when_nothing_qualifies(engine, catalog):
    with session_scope() as s:
        _seed(s, "verified", "jsonl_only", files={"a.py": {}}, info={"practices": {"verified": True}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 14 for c in result["insights"])


def test_insight_14_uses_citation_only_form_below_min_sessions(engine, catalog):
    with session_scope() as s:
        _seed(s, "unverified", "jsonl_only", cost_effective_usd_micros=1_000_000,
             files={"a.py": {"lines_added": 10, "lines_removed": 2}},
             info={"practices": {"verified": False}})

    with session_scope() as s:
        result = _insights(s, catalog, config=ApiConfig(insights=InsightsConfig(min_sessions=5)))
    card = next(c for c in result["insights"] if c["insight_id"] == 14)
    assert "Anthropic recommends" in card["observation"]
    assert "lines_changed" not in card["evidence"]     # no personal-correlation claim below gate


# ----------------------------------------------------------------------- #18

def test_insight_18_flags_vague_prompt_with_heavy_exploration(engine, catalog):
    with session_scope() as s:
        _seed(s, "vague", "jsonl_only", cost_effective_usd_micros=2_000_000,
             tools={"Read": {"bytes_read_total": 80_000}},
             info={"practices": {"first_prompt_chars": 10, "at_refs": 0}})
        _seed(s, "scoped", "jsonl_only", cost_effective_usd_micros=100_000,
             tools={"Read": {"bytes_read_total": 80_000}},
             info={"practices": {"first_prompt_chars": 200, "at_refs": 2}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 18)
    assert card["sessions"] == ["vague"]
    assert card["est_tokens"] == 80_000 // 4
    _assert_lexicon_clean(card)


def test_insight_18_not_flagged_when_at_refs_present(engine, catalog):
    with session_scope() as s:
        _seed(s, "short-but-scoped", "jsonl_only", tools={"Read": {"bytes_read_total": 80_000}},
             info={"practices": {"first_prompt_chars": 10, "at_refs": 3}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 18 for c in result["insights"])


# ----------------------------------------------------------------------- #22

def test_insight_22_flags_all_auto_no_manual_compactions(engine, catalog):
    with session_scope() as s:
        _seed(s, "auto-only", "jsonl_only", cost_effective_usd_micros=3_000_000,
             info={"practices": {"manual_compactions": 0, "auto_compactions": 4}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 22)
    assert card["evidence"] == {"auto_compactions": 4, "manual_compactions": 0}
    assert "auto-compaction picks what survives, you should" in card["action_text"]
    _assert_lexicon_clean(card)


def test_insight_22_absent_when_any_manual_compaction_exists(engine, catalog):
    with session_scope() as s:
        _seed(s, "mixed", "jsonl_only",
             info={"practices": {"manual_compactions": 1, "auto_compactions": 4}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 22 for c in result["insights"])


def test_insight_22_absent_when_no_compactions_at_all(engine, catalog):
    with session_scope() as s:
        _seed(s, "none", "jsonl_only", info={"practices": {"manual_compactions": 0, "auto_compactions": 0}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 22 for c in result["insights"])


# ----------------------------------------------------------------------- #26

def test_insight_26_background_call_spend(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=1_000_000,
             otel={"by_query_source": {
                 "interactive": {"requests": 5, "cost_usd_micros": 800_000, "cost_est_usd_micros": 0,
                                 "cost_effective_usd_micros": 800_000},
                 "prompt_suggestion": {"requests": 2, "cost_usd_micros": 200_000, "cost_est_usd_micros": 0,
                                       "cost_effective_usd_micros": 200_000},
             }})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 26)
    assert card["est_usd"] == 0.2
    assert card["mode"] == "otel"
    assert card["kind"] == "cost"
    assert card["action_text"] is None            # no settings pointer exists
    assert "interactive" not in card["evidence"]["by_query_source"]
    _assert_lexicon_clean(card)


def test_insight_26_absent_for_jsonl_only(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 26 for c in result["insights"])


# ----------------------------------------------------------------------- #27

def test_insight_27_flags_shared_model_between_interactive_and_background(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", otel={"requests": [
            {"model": "claude-opus-5", "query_source": None,
             "cost_usd_micros": 500_000, "cost_est_usd_micros": None},
            {"model": "claude-opus-5", "query_source": "sdk",
             "cost_usd_micros": 100_000, "cost_est_usd_micros": None},
        ]})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 27)
    assert card["evidence"]["shared_models"] == ["claude-opus-5"]
    assert card["est_usd"] == 0.1                 # only the background request's cost
    _assert_lexicon_clean(card)


def test_insight_27_absent_when_models_differ(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", otel={"requests": [
            {"model": "claude-opus-5", "query_source": None,
             "cost_usd_micros": 500_000, "cost_est_usd_micros": None},
            {"model": "claude-haiku", "query_source": "sdk",
             "cost_usd_micros": 10_000, "cost_est_usd_micros": None},
        ]})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 27 for c in result["insights"])


# ----------------------------------------------------------------------- #4

def test_insight_4_resume_after_break(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             info={"resume_cost": {"events": [{"turn": 2, "gap_s": 900, "ttl_s": 300,
                                               "context_before": 10_000, "reload_tokens": 6_000,
                                               "usd_micros": 34_200}],
                                   "count": 1, "total_usd_micros": 34_200}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 4)
    assert card["mode"] == "jsonl"
    assert card["kind"] == "cost"
    assert card["est_usd"] == 0.03            # _card rounds to 2dp; 34,200 micros = $0.0342
    assert card["evidence"] == {"reload_events": 1, "sessions": 1}
    _assert_lexicon_clean(card)


def test_insight_4_sums_events_and_sessions_across_the_range(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             info={"resume_cost": {"events": [], "count": 2, "total_usd_micros": 1_000_000}})
        _seed(s, "jsonl-2", "jsonl_only",
             info={"resume_cost": {"events": [], "count": 1, "total_usd_micros": 500_000}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 4)
    assert card["evidence"] == {"reload_events": 3, "sessions": 2}
    assert card["est_usd"] == 1.5


def test_insight_4_absent_without_resume_cost_data(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 4 for c in result["insights"])


def test_insight_4_absent_when_entirely_unpriced(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             info={"resume_cost": {"events": [], "count": 1, "total_usd_micros": None}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 4 for c in result["insights"])


# ----------------------------------------------------------------------- #5

def test_insight_5_retry_burn(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=3_000_000,
             info={"errors": {"by_class": {"rate_limit": 2}, "retry_count": 2,
                              "retried_tokens_est": 12_000}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 5)
    assert card["mode"] == "jsonl"
    assert card["kind"] == "cost"
    assert card["est_tokens"] == 12_000
    assert card["est_usd"] == 3.0
    assert "12,000" in card["observation"]
    _assert_lexicon_clean(card)


def test_insight_5_sums_across_sessions(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=1_000_000,
             info={"errors": {"retry_count": 1, "retried_tokens_est": 4_000}})
        _seed(s, "jsonl-2", "jsonl_only", cost_effective_usd_micros=2_000_000,
             info={"errors": {"retry_count": 3, "retried_tokens_est": 9_000}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 5)
    assert card["evidence"] == {"sessions": 2, "est_tokens": 13_000}
    assert card["est_usd"] == 3.0


def test_insight_5_absent_without_retries(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", info={"errors": {"retry_count": 0, "retried_tokens_est": 0}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 5 for c in result["insights"])


def test_insight_5_absent_for_otel_only(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only",
             info={"errors": {"retry_count": 1, "retried_tokens_est": 5_000}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 5 for c in result["insights"])


# ----------------------------------------------------------------------- #28

def test_insight_28_context_weight(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             info={"carry": {"cache_read_tokens": 400_000, "carry_usd_micros": 4_000_000,
                             "peak_context": 200_000, "mean_context": 100_000, "requests": 4}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 28)
    assert card["mode"] == "jsonl"
    assert card["kind"] == "cost"
    assert card["est_usd"] == 4.0
    assert card["evidence"] == {"mean_context": 100_000, "requests": 4, "cache_read_tokens": 400_000}
    assert "100,000" in card["observation"] and "4 requests" in card["observation"]
    _assert_lexicon_clean(card)


def test_insight_28_weights_mean_context_by_request_count(engine, catalog):
    with session_scope() as s:
        # A short, high-context session shouldn't dilute a long, low-context one 50/50.
        _seed(s, "short", "jsonl_only",
             info={"carry": {"cache_read_tokens": 100, "carry_usd_micros": 1_000,
                             "peak_context": 200_000, "mean_context": 200_000, "requests": 1}})
        _seed(s, "long", "jsonl_only",
             info={"carry": {"cache_read_tokens": 100, "carry_usd_micros": 1_000,
                             "peak_context": 10_000, "mean_context": 10_000, "requests": 9}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 28)
    # (200_000*1 + 10_000*9) / 10 = 29_000, not the naive average of 105_000
    assert card["evidence"]["mean_context"] == 29_000
    assert card["evidence"]["requests"] == 10


def test_insight_28_absent_without_carry_data(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 28 for c in result["insights"])


def test_insight_28_absent_when_carry_is_entirely_unpriced(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             info={"carry": {"cache_read_tokens": 400_000, "carry_usd_micros": None,
                             "peak_context": 200_000, "mean_context": 100_000, "requests": 4}})

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 28 for c in result["insights"])


# ----------------------------------------------------------------------- #29

def test_insight_29_read_then_discard(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=2_000_000,
             info={"read_then_discard": {
                 "events": [{"compaction_ts": "t1", "bytes_read": 40_000, "est_tokens": 10_000}],
                 "total_bytes_read": 40_000, "total_est_tokens": 10_000,
             }})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 29)
    assert card["mode"] == "jsonl"
    assert card["kind"] == "cost"
    assert card["est_tokens"] == 10_000
    assert card["est_usd"] == 2.0
    assert "10,000" in card["observation"]
    _assert_lexicon_clean(card)


def test_insight_29_absent_without_discarded_reads(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 29 for c in result["insights"])


# ----------------------------------------------------------------------- #30

def _turn(index, tokens, files_changed=0):
    return {"index": index, "tokens": tokens, "files_changed": files_changed}


def test_insight_30_flags_a_long_tail_session(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=1_000_000, turns=[
            _turn(1, 1_000, files_changed=1),
            _turn(2, 1_000, files_changed=0),
            _turn(3, 1_000, files_changed=0),
            _turn(4, 1_000, files_changed=0),
        ])

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 30)
    assert card["evidence"] == {"sessions": 1, "trailing_tokens": 3_000}
    assert card["est_usd"] == 1.0
    _assert_lexicon_clean(card)


def test_insight_30_absent_below_the_trailing_turn_floor(engine, catalog):
    with session_scope() as s:
        # Only one trailing turn -- under the default floor of 3.
        _seed(s, "jsonl-1", "jsonl_only", turns=[
            _turn(1, 1_000, files_changed=1),
            _turn(2, 1_000, files_changed=0),
        ])

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 30 for c in result["insights"])


def test_insight_30_absent_below_the_trailing_token_share(engine, catalog):
    with session_scope() as s:
        # 3 trailing turns of 1 token each vs. a 10,000-token productive turn -- well under 30%.
        _seed(s, "jsonl-1", "jsonl_only", turns=[
            _turn(1, 10_000, files_changed=1),
            _turn(2, 1, files_changed=0),
            _turn(3, 1, files_changed=0),
            _turn(4, 1, files_changed=0),
        ])

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 30 for c in result["insights"])


def test_insight_30_absent_without_turns(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 30 for c in result["insights"])


# ----------------------------------------------------------------------- #31

def test_insight_31_reports_interrupted_requests_as_recoverable_spend(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=5_000_000,
             info={"interrupted": {"events": [{"ts": "t1", "tokens": 150}], "count": 1, "total_tokens": 150,
                                   "total_usd_micros": 250_000}})

    with session_scope() as s:
        result = _insights(s, catalog)
    card = next(c for c in result["insights"] if c["insight_id"] == 31)
    assert card["evidence"] == {"interruptions": 1, "sessions": 1, "tokens": 150}
    assert card["est_usd"] == 0.25            # the fold's priced discarded response
    assert card["action_text"]
    assert card["est_tokens"] == 150
    _assert_lexicon_clean(card)


def test_insight_31_absent_without_interruptions(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only")

    with session_scope() as s:
        result = _insights(s, catalog)
    assert not any(c["insight_id"] == 31 for c in result["insights"])


# ----------------------------------------------------------------------- catalog behavior

def test_insights_are_ranked_by_estimated_recoverable_spend(engine, catalog):
    with session_scope() as s:
        _seed(s, "small", "jsonl_only", cost_effective_usd_micros=100_000,
             files={"a.py": {"lines_added": 1, "lines_removed": 0}},
             info={"practices": {"verified": False}})
        _seed(s, "big", "otel_only", cost_effective_usd_micros=9_000_000,
             otel={"by_query_source": {
                 "interactive": {"requests": 1, "cost_usd_micros": 0, "cost_est_usd_micros": 0,
                                 "cost_effective_usd_micros": 0},
                 "sdk": {"requests": 1, "cost_usd_micros": 9_000_000, "cost_est_usd_micros": 0,
                        "cost_effective_usd_micros": 9_000_000},
             }})

    with session_scope() as s:
        result = _insights(s, catalog)
    ids = [c["insight_id"] for c in result["insights"]]
    assert ids.index(26) < ids.index(14)           # $9 beats $0.1


def test_empty_range_returns_no_insights(engine, catalog):
    with session_scope() as s:
        result = _insights(s, catalog)
    assert result["insights"] == []
    assert result["modes_present"] == []
