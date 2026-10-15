"""P7: parity with `server.worker.graph_fold` (test-side import only, per plan §5 P7)."""
from datetime import datetime

from forecast.synthetic.org.content import _story_downsample, _story_shape


def test_long_turn_threshold_matches():
    from server.worker import graph_fold

    from forecast.synthetic.org.content import _LONG_TURN_THRESHOLD_S
    assert _LONG_TURN_THRESHOLD_S == graph_fold._LONG_TURN_THRESHOLD_S


def test_tokens_total_of_matches():
    from server.worker.graph_fold import tokens_total_of

    models = {"claude-sonnet-5": {"input": 1000, "output": 500, "cache_read": 2000, "cache_write": 300},
             "claude-opus-5": {"input": 200, "output": 50, "cache_read": 100, "cache_write": 10}}
    mine = sum(sum(bucket.values()) for bucket in models.values())
    assert mine == tokens_total_of(models)


def test_channel_cost_of_matches():
    from common.model_pricing import Pricer as CommonPricer
    from server.worker.graph_fold import channel_cost_of

    from forecast.synthetic.org.pricing import OrgPricer

    pricing = CommonPricer.load(None, None)
    pricer = OrgPricer(pricing)
    models = {"claude-sonnet-5": {"input": 1000, "output": 500, "cache_read": 2000, "cache_write": 300},
             "claude-opus-5": {"input": 200, "output": 50, "cache_read": 100, "cache_write": 10}}
    at = datetime(2026, 6, 1)
    assert pricer.channel_micros(models, at.date()) == channel_cost_of(models, pricing, at)


def test_channel_cost_of_matches_over_generated_sessions(records):
    from server.worker.graph_fold import channel_cost_of

    resolved, plans, work, git_result, recs = records
    checked = 0
    for plan in plans[:200]:
        rec = recs[plan.session_id]
        at = plan.started_at
        real = channel_cost_of(rec.models, _pricing_for(resolved), at)
        assert rec.info["channel_cost"] == real, plan.session_id
        checked += 1
    assert checked > 0


def _pricing_for(resolved):
    from common.model_pricing import Pricer as CommonPricer
    return CommonPricer.load(None, None)


def test_story_shape_matches():
    from server.worker.graph_fold import _story_shape as real_story_shape

    curves = [
        [[0, 0, 0], [1, 100, 100], [2, 250, 250], [5, 900, 900], [6, 950, 200], [10, 2000, 2000]],
        [[0, 0, 0], [100, 5000, 5000]],
        [[0, 0, 0], [10, 100, 100], [20, 150, 150], [1000, 200, 200]],
        [],
        [[0, 0, 0]],
    ]
    for curve in curves:
        assert _story_shape(curve) == real_story_shape(curve), curve


def test_story_downsample_matches():
    from server.worker.graph_fold import _story_downsample as real_downsample

    curve = [[float(i), float(i * 100), float(i * 100)] for i in range(200)]
    for max_points in (1, 5, 64, 500):
        assert _story_downsample(curve, max_points) == real_downsample(curve, max_points)


def test_rewrite_recoverable_usd_micros_matches():
    from common.model_pricing import Pricer as CommonPricer
    from server.worker.graph_fold import _rewrite_recoverable_usd_micros

    from forecast.synthetic.org.pricing import OrgPricer

    pricing = CommonPricer.load(None, None)
    pricer = OrgPricer(pricing)
    at = datetime(2026, 6, 1)
    cases = [
        ("claude-sonnet-5", 500, 2000, 300),
        ("claude-sonnet-5", 0, 0, 0),
        ("claude-opus-5", 10000, 5000, 5000),
        ("claude-haiku-4-5-20251001", 1234, 987, 100),
    ]
    for model, rewrite_tokens, context_before, cache_read_tokens in cases:
        mine = pricer.rewrite_recoverable_usd_micros(model, rewrite_tokens, context_before,
                                                      cache_read_tokens, at.date())
        real = _rewrite_recoverable_usd_micros(model, rewrite_tokens, context_before,
                                               cache_read_tokens, pricing, at)
        assert mine == real, (model, rewrite_tokens, context_before, cache_read_tokens)


def test_cache_busts_and_resume_cost_match_the_real_formula_directly(resolved, usage_records,
):
    """Every `cache_busts`/`resume_cost` event this port prices, re-derived directly against
    `graph_fold._rewrite_recoverable_usd_micros` with the SAME per-request inputs the port used
    -- the concrete parity the port exists for, not just the isolated cases above."""
    from common.model_pricing import Pricer as CommonPricer
    from server.worker.graph_fold import _rewrite_recoverable_usd_micros

    from forecast.synthetic.org.content import _cache_busts, _resume_cost, _team_of
    from forecast.synthetic.org.pricing import OrgPricer
    from forecast.synthetic.org.requests import simulate
    from forecast.synthetic.org.sessions import plan_sessions

    pricing = CommonPricer.load(None, None)
    pricer = OrgPricer(pricing)
    plans = plan_sessions(resolved, usage_records)
    checked_busts = checked_resumes = 0
    for plan in plans:
        team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
        # ttl_s 300 for this test only: with the default 3600 s resume gaps (>= 3660 s) rarely
        # fit a session (cache-write contract design 8), which would leave the resume parity
        # check below with nothing to compare.
        sessions = team.sessions.model_copy(update={"ttl_s": 300})
        ledger = simulate(resolved, plan, sessions, team.habits)
        at = plan.started_at.date()

        busts = _cache_busts(pricer, ledger, at)
        bust_events = iter(busts["events"])
        for prev, cur in zip(ledger.requests, ledger.requests[1:]):
            if cur.kind != "bust":
                continue
            context_before = prev.cache_read + prev.cache_write
            expected = _rewrite_recoverable_usd_micros(cur.model, cur.cache_write, context_before,
                                                        cur.cache_read, pricing, at)
            assert next(bust_events)["usd_micros"] == expected
            checked_busts += 1

        resumes = _resume_cost(pricer, ledger, sessions, at)
        resume_events = iter(resumes["events"])
        for prev, cur in zip(ledger.requests, ledger.requests[1:]):
            if cur.kind != "resume":
                continue
            context_before = prev.cache_read + prev.cache_write
            expected = _rewrite_recoverable_usd_micros(cur.model, cur.cache_write, context_before,
                                                        cur.cache_read, pricing, at)
            assert next(resume_events)["recoverable_usd_micros"] == expected
            checked_resumes += 1
    assert checked_busts > 0
    assert checked_resumes > 0
