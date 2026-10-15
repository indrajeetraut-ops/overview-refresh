"""P7: `session_info` content assembly (plan §5 P7, `org/content.py`)."""
_ROOT_ONLY_KEYS = ("story", "context_curve", "segments", "practices")

_SCHEMA = {
    "title": str, "repo": dict, "cache": dict, "carry": dict, "channel_cost": dict,
    "cache_busts": dict, "resume_cost": dict, "interrupted": dict, "compactions": list,
    "reasoning_tokens": int, "duration_ms": int, "event_count": int, "own_event_count": int,
    "mcp_servers": dict, "git_commands": dict, "read_then_discard": dict, "synthetic": dict,
    "tokens_total": int,
}


def test_schema_present_with_types(records):
    resolved, plans, work, git_result, recs = records
    checked_root = checked_child = 0
    for plan in plans:
        rec = recs[plan.session_id]
        is_root = plan.parent_session_id is None
        for key, type_ in _SCHEMA.items():
            assert key in rec.info, (plan.session_id, key)
            assert isinstance(rec.info[key], type_), (plan.session_id, key, type(rec.info[key]))
        if is_root:
            for key in _ROOT_ONLY_KEYS:
                assert key in rec.info, (plan.session_id, key)
            checked_root += 1
        else:
            for key in _ROOT_ONLY_KEYS:
                assert key not in rec.info, (plan.session_id, key)
            assert rec.turns == []
            assert rec.files == {}
            checked_child += 1
    assert checked_root > 0 and checked_child > 0


def test_hit_ratio_formula(records):
    resolved, plans, work, git_result, recs = records
    checked = 0
    for rec in recs.values():
        total_cr = sum(m["cache_read"] for m in rec.models.values())
        total_input = sum(m["input"] for m in rec.models.values())
        expected = round(total_cr / (total_cr + total_input), 4) if (total_cr + total_input) else 0.0
        assert rec.info["cache"]["hit_ratio"] == expected
        checked += 1
    assert checked > 0


def test_cost_consistency_i5(records):
    """I5: channel costs + carry reconcile to the session's own priced cost within 4 micros;
    every recoverable total and carry stay <= cost."""
    resolved, plans, work, git_result, recs = records
    checked = 0
    for rec in recs.values():
        cc = rec.info["channel_cost"]
        carry = rec.info["carry"]["carry_usd_micros"]
        total = cc["input"]["usd_micros"] + cc["output"]["usd_micros"] + cc["recache"]["usd_micros"] + carry
        assert abs(total - rec.cost_effective_usd_micros) <= 4, rec.session_id
        assert carry <= rec.cost_effective_usd_micros + 4
        for total_key in ("total_usd_micros",):
            busts_total = rec.info["cache_busts"][total_key]
            if busts_total is not None:
                assert busts_total <= rec.cost_effective_usd_micros + 4
            resume_total = rec.info["resume_cost"][total_key]
            if resume_total is not None:
                assert resume_total <= rec.cost_effective_usd_micros + 4
        checked += 1
    assert checked > 0


def test_segments_sum_to_session(records):
    resolved, plans, work, git_result, recs = records
    checked = 0
    for rec in recs.values():
        segments = rec.info.get("segments")
        if not segments:
            continue
        seg_models: dict[str, dict[str, int]] = {}
        seg_cost = 0
        for seg in segments:
            seg_cost += seg["cost_usd_micros"]
            for model, cats in seg["models"].items():
                bucket = seg_models.setdefault(model, {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
                for k, v in cats.items():
                    bucket[k] += v
        assert seg_models == rec.models, rec.session_id
        checked += 1
    assert checked > 0


def test_counters_consistent_with_lists(records):
    resolved, plans, work, git_result, recs = records
    for plan in plans:
        rec = recs[plan.session_id]
        assert rec.compaction_count == len(rec.info["compactions"])
        assert rec.subagent_count == len(plan.children)
        if plan.parent_session_id is None:
            assert rec.turn_count == len(rec.turns)
            assert rec.long_turn_count == sum(1 for t in rec.turns if t["long"])


def _pricer_and_day():
    from datetime import date
    from common.model_pricing import Pricer as CommonPricer
    from forecast.synthetic.org.pricing import OrgPricer
    return OrgPricer(CommonPricer.load(None, None)), date(2026, 9, 1)


def test_request_micros_prices_the_lifetimes_separately():
    pricer, at = _pricer_and_day()
    model = "claude-sonnet-5"
    tokens = {"input": 100, "output": 50, "cache_read": 1_000, "cache_write": 7_000}
    detail = [{"ttl_s": 300, "tokens": 2_000}, {"ttl_s": 3600, "tokens": 5_000}]
    split = pricer.request_micros(model, tokens, at, detail)
    explicit = pricer.price_micros(model, {"input": 100, "output": 50, "cache_read": 1_000,
                                            "cache_write_5m": 2_000, "cache_write_1h": 5_000}, at)
    combined_at_1h = pricer.request_micros(model, tokens, at)
    assert split == explicit
    # A bare combined cache_write is billed at the 1-hour rate, so the split is never dearer.
    assert split <= combined_at_1h


def test_interrupted_events_price_from_the_split_and_keep_the_combined_tokens():
    from datetime import datetime, timezone
    from forecast.synthetic.org.content import _interrupted
    from forecast.synthetic.org.requests import Ledger, Request

    pricer, at = _pricer_and_day()
    req = Request(index=0, ts=datetime(2026, 9, 1, 10, tzinfo=timezone.utc), model="claude-sonnet-5",
                  input=10, cache_read=500, cache_write_5m=2_000, cache_write_1h=5_000,
                  output=40, turn=0, kind="first", interrupted=True)
    event = _interrupted(pricer, Ledger(requests=[req], turn_count=1), at)["events"][0]
    assert event["tokens"]["cache_write"] == 7_000
    assert event["usd_micros"] == pricer.price_micros("claude-sonnet-5", {
        "input": 10, "output": 40, "cache_read": 500,
        "cache_write_5m": 2_000, "cache_write_1h": 5_000}, at)


def test_default_cache_lifetime_is_one_hour():
    from forecast.synthetic.org.config import SessionsDefaults
    assert SessionsDefaults().ttl_s == 3600
