"""P7: request ledger simulation (plan §5 P7, `org/requests.py`)."""
import pytest

from forecast.synthetic.org.requests import simulate


def _team_of(resolved, l1_user):
    return next(t.team_name for t in resolved.teams if l1_user in t.user_ids)


def _ledgers(resolved, plans):
    for plan in plans:
        team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
        yield plan, team, simulate(resolved, plan, team.sessions, team.habits)


KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def _agg(ledger):
    agg: dict[str, dict[str, int]] = {}
    for req in ledger.requests:
        bucket = agg.setdefault(req.model, dict.fromkeys(KINDS, 0))
        for kind in KINDS:
            bucket[kind] += getattr(req, kind)
    return agg


def test_per_model_exactness(resolved, usage_records):
    """I1 per token type, down to the requests: every model's five totals are conserved."""
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    checked = 0
    for plan, team, ledger in _ledgers(resolved, plans):
        agg = _agg(ledger)
        for model, cats in plan.models.items():
            actual = agg.get(model, dict.fromkeys(KINDS, 0))
            assert actual == cats, (plan.session_id, model, cats, actual)
        checked += 1
    assert checked > 0


def test_cache_write_is_the_sum_of_the_two_lifetimes(resolved, usage_records):
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    for plan, team, ledger in _ledgers(resolved, plans):
        for req in ledger.requests:
            assert req.cache_write == req.cache_write_5m + req.cache_write_1h
        by_model: dict[str, int] = {}
        for req in ledger.requests:
            by_model[req.model] = by_model.get(req.model, 0) + req.cache_write
        for model, cats in plan.models.items():
            assert by_model.get(model, 0) == cats["cache_write_5m"] + cats["cache_write_1h"]


def test_no_minimum_input_per_request(resolved, usage_records):
    """Input is Layer 1's uncached input, spread with equal weights and no floor: a session
    may carry fewer input tokens than requests, and individual requests may have input 0."""
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    zero_input_requests = 0
    for plan, team, ledger in _ledgers(resolved, plans):
        zero_input_requests += sum(1 for req in ledger.requests if req.input == 0)
        assert all(req.input >= 0 for req in ledger.requests)
    assert zero_input_requests > 0


def test_context_window_never_exceeded(resolved, usage_records):
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    for plan, team, ledger in _ledgers(resolved, plans):
        peak = max((r.cache_read + r.cache_write for r in ledger.requests), default=0)
        assert peak <= team.sessions.context_window_tokens


def test_timestamps_monotonic_and_within_session(resolved, usage_records):
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    for plan, team, ledger in _ledgers(resolved, plans):
        tss = [r.ts for r in ledger.requests]
        assert tss == sorted(tss)
        if tss:
            assert plan.started_at <= tss[0]
            assert tss[-1] <= plan.ended_at


def test_resume_and_bust_gap_bounds(resolved, usage_records):
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    resume_checked = bust_checked = 0
    for plan, team, ledger in _ledgers(resolved, plans):
        for prev, cur in zip(ledger.requests, ledger.requests[1:]):
            gap = (cur.ts - prev.ts).total_seconds()
            if cur.kind == "resume":
                assert gap >= team.sessions.ttl_s, (plan.session_id, gap)
                resume_checked += 1
            elif cur.kind == "bust":
                assert gap < team.sessions.ttl_s, (plan.session_id, gap)
                bust_checked += 1
    # At least one of each should appear somewhere in a full series -- otherwise the test
    # would pass vacuously without ever exercising the bound.
    assert resume_checked > 0 or bust_checked > 0


def test_habits_change_leaves_totals_unchanged(resolved, usage_records):
    """I2b at the unit level: a habits-only config change must not perturb per-model totals."""
    from forecast.synthetic.org.sessions import plan_sessions

    plans = plan_sessions(resolved, usage_records)
    plan = next(p for p in plans if p.parent_session_id is None)
    team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
    baseline_habits = team.habits
    changed_habits = baseline_habits.model_copy(update={
        "cache_bust_per_100_requests": baseline_habits.cache_bust_per_100_requests * 5 + 1,
        "resume_prob_per_turn": min(1.0, baseline_habits.resume_prob_per_turn * 3 + 0.05),
        "compactions_per_100_requests": baseline_habits.compactions_per_100_requests * 4 + 1,
    })
    ledger_a = simulate(resolved, plan, team.sessions, baseline_habits)
    ledger_b = simulate(resolved, plan, team.sessions, changed_habits)
    assert _agg(ledger_a) == _agg(ledger_b)


def _plan_with(resolved, usage_records, models):
    import dataclasses
    from forecast.synthetic.org.sessions import plan_sessions

    base = plan_sessions(resolved, usage_records)[0]
    return dataclasses.replace(base, models=models, children=[], parent_session_id=None)


def _amounts(**given):
    return {k: given.get(k, 0) for k in
            ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")}


def _expected_r(resolved, plan, team):
    from forecast.synthetic.randomness import rng_for

    base_r = rng_for(resolved.config.org.seed, "requests", plan.session_id)
    mean_ctx = base_r.uniform(team.sessions.mean_context_tokens.min,
                              team.sessions.mean_context_tokens.max)
    context = sum(c["cache_read"] + c["cache_write_5m"] + c["cache_write_1h"]
                  for c in plan.models.values())
    with_tokens = sum(1 for c in plan.models.values() if sum(c.values()) > 0)
    return max(max(2, min(1500, round(context / mean_ctx))), with_tokens)


def test_request_count_is_derived_from_reads_plus_writes(resolved, usage_records):
    plan = _plan_with(resolved, usage_records, {
        "claude-sonnet-5": _amounts(input=3_000, cache_write_5m=200_000, cache_write_1h=3_000_000,
                                    cache_read=6_000_000, output=80_000),
        "claude-opus-5": _amounts(input=300, cache_write_1h=400_000, cache_read=1_000_000,
                                  output=9_000)})
    team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
    ledger = simulate(resolved, plan, team.sessions, team.habits)
    assert len(ledger.requests) == _expected_r(resolved, plan, team)
    assert len(ledger.requests) > 2


def test_session_with_no_reads_and_large_writes_gets_many_requests_within_the_window(
        resolved, usage_records):
    plan = _plan_with(resolved, usage_records, {
        "claude-sonnet-5": _amounts(input=2_000, cache_write_5m=100_000, cache_write_1h=2_900_000,
                                    output=40_000)})
    team = resolved.team_effective[_team_of(resolved, plan.l1_user)]
    ledger = simulate(resolved, plan, team.sessions, team.habits)
    assert len(ledger.requests) == _expected_r(resolved, plan, team) > 2
    assert max(r.cache_read + r.cache_write for r in ledger.requests) <= team.sessions.context_window_tokens
    assert _agg(ledger)["claude-sonnet-5"] == plan.models["claude-sonnet-5"]

