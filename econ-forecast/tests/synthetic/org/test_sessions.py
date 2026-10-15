"""P4: session split (I1), subagents, and day layout (plan §5 P4)."""
import random
from datetime import date, timedelta

import pytest

from forecast.synthetic.org.sessions import (
    PROCESSED_TOKENS_PER_MIN, SessionPlan, _split_group, _total_processed, plan_sessions,
)

KINDS = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")


def _amounts(**given):
    """Five-type amounts with every unnamed type zero."""
    return {kind: given.get(kind, 0) for kind in KINDS}


# --------------------------------------------------------------------------------------------
# Exactness (I1) and edge cases, at the `_split_group` level -- one usage group in isolation
# --------------------------------------------------------------------------------------------

def _group_ctx(resolved, series_inputs):
    l1_user = series_inputs["teams"][0].user_ids[0]
    project = resolved.config.repos[0].project
    return l1_user, project, "claude_code", date(2026, 1, 5)


def test_split_exact_per_category(resolved, series_inputs):
    """Randomized property test: 200 groups, each model/category conserved exactly."""
    l1_user, project, agent, base_date = _group_ctx(resolved, series_inputs)
    rng = random.Random(20260918)
    for i in range(200):
        totals = {
            "claude-sonnet-5": _amounts(input=rng.randint(0, 20_000),
                                        cache_write_5m=rng.randint(0, 100_000),
                                        cache_write_1h=rng.randint(0, 2_000_000),
                                        cache_read=rng.randint(0, 8_000_000),
                                        output=rng.randint(0, 100_000)),
            "claude-opus-5": _amounts(input=rng.randint(0, 5_000),
                                      cache_write_5m=rng.randint(0, 30_000),
                                      cache_write_1h=rng.randint(0, 500_000),
                                      cache_read=rng.randint(0, 2_000_000),
                                      output=rng.randint(0, 30_000)),
        }
        if not any(any(cats.values()) for cats in totals.values()):
            continue
        the_date = base_date + timedelta(days=i)
        plans = _split_group(resolved, the_date, l1_user, project, agent, totals)
        actual = {"claude-sonnet-5": _amounts(), "claude-opus-5": _amounts()}
        for plan in plans:
            for m, cats in plan.models.items():
                for c, v in cats.items():
                    actual[m][c] += v
        assert actual == totals, (i, totals, actual)


def test_single_token_group(resolved, series_inputs):
    l1_user, project, agent, the_date = _group_ctx(resolved, series_inputs)
    totals = {"claude-sonnet-5": _amounts(input=1)}
    plans = _split_group(resolved, the_date, l1_user, project, agent, totals)
    assert len(plans) == 1
    assert plans[0].models["claude-sonnet-5"]["input"] == 1


def test_huge_group(resolved, series_inputs):
    l1_user, project, agent, the_date = _group_ctx(resolved, series_inputs)
    totals = {"claude-sonnet-5": _amounts(cache_write_1h=5 * 10**11)}
    plans = _split_group(resolved, the_date, l1_user, project, agent, totals)
    team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
    max_per_day = resolved.team_effective[team_name].sessions.max_per_day
    assert len(plans) == max_per_day
    assert sum(p.models["claude-sonnet-5"]["cache_write_1h"] for p in plans) == 5 * 10**11


def test_zero_share_model_absent(resolved, series_inputs):
    l1_user, project, agent, the_date = _group_ctx(resolved, series_inputs)
    totals = {"claude-sonnet-5": _amounts(input=1_000, cache_write_1h=99_000, cache_read=50_000, output=1_000),
             "claude-opus-5": _amounts()}
    plans = _split_group(resolved, the_date, l1_user, project, agent, totals)
    present = {m for p in plans for m in p.models}
    assert "claude-opus-5" not in present
    assert "claude-sonnet-5" in present


def test_empty_session_merged(resolved, series_inputs):
    l1_user, project, agent, the_date = _group_ctx(resolved, series_inputs)
    # A tiny total relative to `mean_input_tokens` drives `n` down via the merge loop; no
    # session in the result may end up with an empty `models` dict.
    totals = {"claude-sonnet-5": _amounts(input=5),
             "claude-opus-5": _amounts(input=3)}
    plans = _split_group(resolved, the_date, l1_user, project, agent, totals)
    assert all(p.models for p in plans)
    assert sum(p.models.get("claude-sonnet-5", {}).get("input", 0) for p in plans) == 5
    assert sum(p.models.get("claude-opus-5", {}).get("input", 0) for p in plans) == 3


# --------------------------------------------------------------------------------------------
# Subagents
# --------------------------------------------------------------------------------------------

def test_children_carved_from_parent(resolved, usage_records):
    plans = plan_sessions(resolved, usage_records)
    by_id = {p.session_id: p for p in plans}
    teams_cfg = resolved.team_effective
    found_children = False
    for plan in plans:
        if plan.parent_session_id is None:
            continue
        found_children = True
        parent = by_id[plan.parent_session_id]
        team_name = next(t.team_name for t in resolved.teams if parent.l1_user in t.user_ids)
        min_input = teams_cfg[team_name].sessions.min_input_tokens
        assert _total_processed(parent.models) >= 0  # parent never goes negative
        for cats in parent.models.values():
            assert all(cats[kind] >= 0 for kind in KINDS)
    assert found_children, "expected at least one subagent to spawn over the whole series"

    # Exactness still holds with subagents in the mix (re-verified at the plan_sessions level).
    expected = {}
    for row in usage_records:
        key = (row.date, row.user_id, row.model)
        bucket = expected.setdefault(key, _amounts())
        for kind in KINDS:
            bucket[kind] += getattr(row.tokens, kind) or 0
    actual = {}
    for plan in plans:
        for m, cats in plan.models.items():
            key = (plan.date, plan.l1_user, m)
            bucket = actual.setdefault(key, _amounts())
            for c, v in cats.items():
                bucket[c] += v
    assert actual == expected


def test_child_within_parent(resolved, usage_records):
    plans = plan_sessions(resolved, usage_records)
    by_id = {p.session_id: p for p in plans}
    checked = 0
    for plan in plans:
        if plan.parent_session_id is None:
            continue
        parent = by_id[plan.parent_session_id]
        assert parent.started_at <= plan.started_at
        assert plan.ended_at <= parent.ended_at
        checked += 1
    assert checked > 0


# --------------------------------------------------------------------------------------------
# Day layout
# --------------------------------------------------------------------------------------------

def test_layout_same_local_day(resolved, usage_records):
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(resolved.series.series.timezone)
    plans = plan_sessions(resolved, usage_records)
    for plan in plans:
        if plan.parent_session_id is not None:
            continue
        assert plan.started_at.astimezone(tz).date() == plan.date
        assert plan.ended_at.astimezone(tz).date() == plan.date


def test_layout_no_overlap_roots(resolved, usage_records):
    plans = plan_sessions(resolved, usage_records)
    by_user_day: dict[tuple, list] = {}
    for plan in plans:
        if plan.parent_session_id is not None:
            continue
        by_user_day.setdefault((plan.l1_user, plan.date), []).append((plan.started_at, plan.ended_at))
    for key, intervals in by_user_day.items():
        intervals.sort()
        for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
            assert e1 <= s2, (key, s1, e1, s2, e2)


def test_layout_deterministic(resolved, usage_records):
    first = plan_sessions(resolved, usage_records)
    second = plan_sessions(resolved, usage_records)
    key = lambda p: p.session_id
    first_sorted = sorted(first, key=key)
    second_sorted = sorted(second, key=key)
    assert [p.session_id for p in first_sorted] == [p.session_id for p in second_sorted]
    for a, b in zip(first_sorted, second_sorted):
        assert a.started_at == b.started_at
        assert a.ended_at == b.ended_at
        assert a.models == b.models
