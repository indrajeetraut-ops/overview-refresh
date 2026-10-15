"""P5: link flags, calibration, work streams, features, backlog (plan §5 P5)."""
from collections import Counter, defaultdict
from datetime import date, timedelta

import pytest

from forecast.synthetic.org import ids
from forecast.synthetic.org.config import Module, Repo
from forecast.synthetic.org.sessions import _total_processed, plan_sessions
from forecast.synthetic.org.work import (
    WorkItem, _calibrate_base, _features, _link_flags, _ramp, compute_work,
)


@pytest.fixture
def work_result(resolved, usage_records):
    plans = plan_sessions(resolved, usage_records)
    return plans, compute_work(resolved, plans)


# --------------------------------------------------------------------------------------------
# Budget accounting
# --------------------------------------------------------------------------------------------

def test_budget_accounting(resolved, work_result):
    plans, result = work_result
    roots = [p for p in plans if p.parent_session_id is None]
    children_by_parent = defaultdict(list)
    for p in plans:
        if p.parent_session_id:
            children_by_parent[p.parent_session_id].append(p)

    def inclusive(root):
        total = _total_processed(root.models) + sum(c["output"] for c in root.models.values())
        for child in children_by_parent.get(root.session_id, ()):
            total += _total_processed(child.models) + sum(c["output"] for c in child.models.values())
        return total

    total_all = sum(inclusive(r) for r in roots)
    total_spent = sum(item.spent for item in result.items.values())
    total_unlinked = sum(inclusive(r) for r in roots if not result.linked.get(r.session_id))
    assert total_spent + total_unlinked == pytest.approx(total_all, rel=0, abs=1e-6)


def test_unopened_items_have_no_pr(resolved, work_result):
    plans, result = work_result
    unopened = [item for item in result.items.values() if not item.has_pr]
    for item in unopened:
        assert item.opened_at is None
        # Its sessions are still accounted for as linked (for budget purposes).
        for sid in item.sessions:
            assert result.linked.get(sid) is True


# --------------------------------------------------------------------------------------------
# Calibration (I6) + monotonic $/KLOC across contrasting teams -- a purpose-built 4-team,
# 90-day scenario (the checked-in example config does not exist until P9).
# --------------------------------------------------------------------------------------------

def test_calibration_hits_target(four_team_org):
    """I6: realised (opened * (1 - abandon_rate)) PRs/dev-week within +-25% of target. Merge
    outcomes don't exist until P6 (git.py); `opened * (1 - abandon_rate)` is exactly what the
    calibration formula's own `(1 - abandon_rate)` factor targets (see `_calibrate_base`), so it
    is the correct P5-level proxy for "will eventually merge". Re-verified against the real
    example config's actual merged-PR count once P6/P9 exist (see the progress log)."""
    resolved, plans, result = four_team_org
    series = resolved.series.series
    series_end = series.start_date + timedelta(days=series.days - 1)
    dev_weeks = sum(((series_end - j).days + 1) / 7 for j in result.first_active_date.values())

    opened_adj = 0.0
    loc_per_dollar: dict[str, float] = {}
    for team_name in resolved.team_order:
        abandon = resolved.team_effective[team_name].review.abandon_rate
        team_items = [it for it in result.items.values()
                     if it.l1_user in {u for t in resolved.teams if t.team_name == team_name for u in t.user_ids}]
        opened = [it for it in team_items if it.has_pr]
        opened_adj += len(opened) * (1 - abandon)
        loc = sum(it.size_loc for it in opened)
        spent = sum(it.spent for it in opened)
        loc_per_dollar[team_name] = loc / spent if spent else 0.0

    target = resolved.config.calibration.target_merged_prs_per_dev_week
    rate = opened_adj / dev_weeks
    assert 0.75 * target <= rate <= 1.25 * target, (rate, target)

    # Monotonic: the team with the lowest tokens_per_loc_multiplier has the highest LOC/token
    # (a proxy for LOC/$: all four teams share the same model mix and pricing table, so tokens
    # and dollars are proportional here).
    by_multiplier = sorted(resolved.team_order,
                           key=lambda name: resolved.team_effective[name].tokens_per_loc_multiplier)
    efficiencies = [loc_per_dollar[name] for name in by_multiplier]
    assert efficiencies == sorted(efficiencies, reverse=True), (by_multiplier, efficiencies)


# --------------------------------------------------------------------------------------------
# Parallel items bound
# --------------------------------------------------------------------------------------------

def test_parallel_items_bounded(build_org_config, org_config_dict, series_inputs, usage_records):
    from forecast.synthetic.org.config import validate_org_inputs

    org_config_dict["teams"]["platform"] = {"work": {"parallel_items": 2, "new_item_prob": 0.9}}
    cfg = build_org_config(org_config_dict)
    resolved = validate_org_inputs(cfg, series_inputs["manifest"], series_inputs["users"],
                                   series_inputs["teams"], series_inputs["series_cfg"],
                                   series_inputs["pricing"])
    plans = plan_sessions(resolved, usage_records)
    result = compute_work(resolved, plans)

    by_user_repo: dict[tuple, list[WorkItem]] = defaultdict(list)
    for item in result.items.values():
        by_user_repo[(item.l1_user, item.repo.name)].append(item)

    for (l1_user, repo_name), items in by_user_repo.items():
        team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
        cap = resolved.team_effective[team_name].work.parallel_items
        items = sorted(items, key=lambda it: it.seq)
        for item in items:
            if item.first_session_at is None:
                continue
            concurrent = sum(
                1 for other in items
                if other.first_session_at is not None
                and other.first_session_at <= item.first_session_at
                and (other.finalized_at is None or other.finalized_at >= item.first_session_at))
            assert concurrent <= cap, (l1_user, repo_name, item.item_id, concurrent, cap)


# --------------------------------------------------------------------------------------------
# Feature sizes, sharing
# --------------------------------------------------------------------------------------------

def test_feature_sizes_respected(resolved, work_result):
    plans, result = work_result
    for feature in result.features.values():
        team = resolved.team_effective[feature.team_name]
        assert len(feature.items) <= max(int(k) for k in team.work.feature_size)
        assert 1 <= len(feature.items)


def test_shared_feature_same_team_same_repo(resolved, work_result):
    plans, result = work_result
    shared = [f for f in result.features.values() if f.issue_owner]
    for feature in shared:
        owner = result.features[feature.issue_owner]
        assert owner.team_name == feature.team_name
        assert owner.repo.name == feature.repo.name
        assert owner.l1_user != feature.l1_user


def test_link_mix_frequencies(resolved):
    """2k features (synthesized directly against `_features`, bypassing the full session
    pipeline -- generating 2k real sessions/items would be far slower) within +-5pp of the
    configured `issue_link_mix`."""
    l1_user = next(iter(resolved.users_by_id))
    module = Module(path="src/", weight=1.0, extensions={".py": 1.0})
    items: dict[str, WorkItem] = {}
    n = 2000
    for i in range(n):
        repo = Repo(project=f"proj{i}", name=f"repo-{i}", modules=[module])
        item_id = f"repo-{i}:{l1_user}:0"
        items[item_id] = WorkItem(item_id=item_id, l1_user=l1_user, repo=repo, seq=0,
                                  size_loc=100, change_type="feature", budget=1000.0,
                                  module=module)
    features = _features(resolved, items)
    assert len(features) == n
    counts = Counter(f.link_method for f in features.values())
    team_name = next(t.team_name for t in resolved.teams if l1_user in t.user_ids)
    expected = resolved.team_effective[team_name].work.issue_link_mix
    for method, share in expected.items():
        observed = counts[method] / n
        assert abs(observed - share) <= 0.05, (method, observed, share)


# --------------------------------------------------------------------------------------------
# Ramp
# --------------------------------------------------------------------------------------------

def test_ramp_increases_budget_early(resolved):
    onboarding = resolved.config.onboarding
    series_start = resolved.series.series.start_date
    l1_user = next(iter(resolved.users_by_id))
    first_active = {l1_user: series_start + timedelta(days=10)}  # after start+7, so ramp applies

    early = _ramp(resolved, l1_user, first_active, first_active[l1_user])
    mid = _ramp(resolved, l1_user, first_active,
               first_active[l1_user] + timedelta(days=onboarding.ramp_days // 2))
    late = _ramp(resolved, l1_user, first_active,
                first_active[l1_user] + timedelta(days=onboarding.ramp_days * 2))

    assert early == pytest.approx(onboarding.initial_multiplier)
    assert 1.0 < mid < early
    assert late == pytest.approx(1.0)

    # A user active from day 1 (before start+7) never ramps.
    no_ramp_user = next(iter(resolved.users_by_id))
    always_active = {no_ramp_user: series_start}
    assert _ramp(resolved, no_ramp_user, always_active, series_start + timedelta(days=5)) == 1.0
