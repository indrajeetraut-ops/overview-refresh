"""F3 (repo rollup) / F5 (reasoning) / X3 (recoverable waterfall) + shared derived metrics
(`server/api/manager/aggregate.py`). Pure functions over plain rows -- no DB needed."""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

from common.model_pricing import Pricer
from server.api import precedence
from server.api.config import ApiConfig, ModelFitConfig, PeerConfig, UtilizationConfig
from server.api.errors import BadRequestError
from server.api.manager import aggregate as agg
from server.storage import session_scalars

#: Default A5-peer-task-mix thresholds (config/api.yaml `peer:` block).
PEER = PeerConfig()

#: Default A2/A4 thresholds (config/api.yaml `utilization:` block), for the band/room-to-save
#: tests below -- the numbers moved out of aggregate.py into config, values unchanged.
UTIL = UtilizationConfig()

#: The real model_pricing catalog -- same precedent as `test_segments_pricing.py`'s own
#: module-level `PRICING`, used here only for the X4 savings-ratio tests (§3.4).
PRICING = Pricer.load(None, None)


def row(**kw):
    """A manager scope-fetch row. `info`/`files` (still accepted, for the handful of
    functions -- `model_fit`, `segment_cost_by_repo_key` -- that read them directly) also drive
    the D3 row-contract scalars (`recoverable_*`, `reasoning_tokens`, `has_files`,
    `session_type`) via `session_scalars.fold_scalars`, the same derivation the real SQL
    mirrors and the real fold writes -- so a fixture built from `info=` alone still exercises
    `aggregate.recoverable`/`reasoning_share`/etc, which read the scalars, not `info`. An
    explicit scalar kwarg overrides the derived one."""
    defaults = dict(
        parent_session_id=None, user_id="u1", repo_id=None, repo_id_provenance=None,
        cost_effective_usd_micros=0, tokens_total=0, info={}, files=None, agent="claude_code",
    )
    defaults.update(kw)
    scalars = session_scalars.fold_scalars(defaults["info"], defaults["files"])
    return SimpleNamespace(**{**scalars, **defaults})


# ------------------------------------------------------------------------------- F3

def test_repo_rollup_two_users_local_repos_do_not_merge():
    rows = [
        row(user_id="alice", repo_id="local:api", repo_id_provenance="path", cost_effective_usd_micros=1_000_000),
        row(user_id="bob", repo_id="local:api", repo_id_provenance="path", cost_effective_usd_micros=2_000_000),
    ]
    result = agg.repo_rollup_canonical(rows)
    assert len(result) == 2
    assert {b["local_to_user"] for b in result} == {"alice", "bob"}


def test_repo_rollup_two_users_same_remote_do_merge():
    rows = [
        row(user_id="alice", repo_id="github:acme/api", repo_id_provenance="remote", cost_effective_usd_micros=1_000_000),
        row(user_id="bob", repo_id="github:acme/api", repo_id_provenance="remote", cost_effective_usd_micros=2_000_000),
    ]
    result = agg.repo_rollup_canonical(rows)
    assert len(result) == 1
    assert result[0]["cost_usd_micros"] == 3_000_000
    assert result[0]["local_to_user"] is None


def test_repo_rollup_counts_sessions_over_roots_only_sums_cost_over_every_row():
    rows = [
        row(repo_id="github:acme/api", repo_id_provenance="remote", cost_effective_usd_micros=100, tokens_total=10),
        row(repo_id="github:acme/api", repo_id_provenance="remote", cost_effective_usd_micros=50, tokens_total=5,
            parent_session_id="parent-1"),  # a sub-agent child
    ]
    result = agg.repo_rollup_canonical(rows)
    assert result[0]["sessions"] == 1          # only the root counted
    assert result[0]["cost_usd_micros"] == 150  # both rows summed
    assert result[0]["tokens_total"] == 15


def test_repo_rollup_ignores_rows_with_no_repo():
    assert agg.repo_rollup_canonical([row(repo_id=None)]) == []


# ------------------------------------------------------------------------------- F5

def test_reasoning_share_is_none_cold_start_when_never_reported():
    assert agg.reasoning_share([row(tokens_total=100), row(tokens_total=200)]) is None


def test_reasoning_share_sums_and_computes_pct_once_reported():
    rows = [
        row(tokens_total=1000, info={"reasoning_tokens": 200}),
        row(tokens_total=1000, info={"reasoning_tokens": 100}),
    ]
    result = agg.reasoning_share(rows)
    assert result == {"reasoning_tokens": 300, "tokens_total": 2000, "pct": 15.0}


# ------------------------------------------------------------------------------- X3

def test_segment_has_beat_reads_the_real_kind_to_count_tally_shape():
    """Regression: `beats` is `{kind: count}` (build_segments's own shape), not a list of
    `{"kind": ...}` records -- the bug this locks in would have made every X3/X4 rework
    detection silently see nothing (or crash) against a real session_info row."""
    assert agg.segment_has_beat({"beats": {"revision_loop": 2}}, "revision_loop") is True
    assert agg.segment_has_beat({"beats": {"revision_loop": 0}}, "revision_loop") is False
    assert agg.segment_has_beat({"beats": {}}, "revision_loop") is False
    assert agg.segment_has_beat({}, "revision_loop") is False


def _priced_row(resume=0, bust=0, interrupted=0, revision_loop_cost=0, cost=0, carry=0, recache=0):
    """A session_info row carrying the fold's priced recoverable sources. `carry`/`recache` and a
    priced revision-loop segment are set too, so tests can prove they are never counted."""
    info = {
        "channel_cost": {"output": {"usd_micros": 0}, "recache": {"usd_micros": recache}},
        "carry": {"carry_usd_micros": carry},
        "resume_cost": {"total_usd_micros": resume * 3, "total_recoverable_usd_micros": resume},
        "cache_busts": {"total_usd_micros": bust},
        "interrupted": {"total_usd_micros": interrupted},
    }
    if revision_loop_cost:
        # beats is {kind: count} (worker/segments/beats.py::build_segments), not a list of
        # beat records -- this fixture matches the real fold output exactly on purpose.
        info["segments"] = [{"cost_usd_micros": revision_loop_cost, "beats": {"revision_loop": 1}}]
    return row(cost_effective_usd_micros=cost, info=info)


def test_recoverable_sums_the_three_priced_buckets():
    rows = [_priced_row(resume=200, bust=30, interrupted=20, cost=1000)]
    result = agg.recoverable(rows)
    assert result["buckets_usd_micros"] == {"resume": 200, "cache_bust": 30, "interrupted": 20}
    assert result["recoverable_usd_micros"] == 250
    assert result["recoverable_pct"] == 0.25


def test_recoverable_never_counts_context_carry_or_ordinary_cache_writes():
    """Decision 2026-09-16: cache reads are the cheap path and cache writes are growth -- neither is
    recoverable, however large."""
    result = agg.recoverable([_priced_row(carry=900, recache=500, cost=1000)])
    assert result["recoverable_usd_micros"] == 0
    assert result["dominant_cause"] is None


def test_recoverable_does_not_count_rework_segments():
    """Rework is a coaching signal until its detector is tightened (TODO(rework-detector))."""
    result = agg.recoverable([_priced_row(revision_loop_cost=800, interrupted=10, cost=1000)])
    assert result["recoverable_usd_micros"] == 10
    assert "rework" not in result["buckets_usd_micros"]


def test_recoverable_resume_bucket_is_the_write_minus_read_share_not_the_full_reload():
    """`resume_cost.total_usd_micros` is what the reload cost; only its recoverable share counts."""
    result = agg.recoverable([_priced_row(resume=200, cost=1000)])
    assert result["buckets_usd_micros"]["resume"] == 200  # not the fixture's 600 full reload


def test_recoverable_resume_bucket_ignores_the_projected_subfield():
    """`resume_cost.projected` is a *hypothetical* next reload, not a historical spend -- it
    must never leak into the priced total."""
    info = {"resume_cost": {"total_recoverable_usd_micros": 0, "projected": {"usd_micros": 999_999_999}}}
    result = agg.recoverable([row(info=info, cost_effective_usd_micros=1000)])
    assert result["buckets_usd_micros"]["resume"] == 0


def test_recoverable_skips_an_unpriced_request_bucket_rather_than_a_partial_sum():
    info = {"cache_busts": {"total_usd_micros": None, "events": [{"ts": None, "usd_micros": 50}]}}
    result = agg.recoverable([row(info=info, cost_effective_usd_micros=1000)])
    assert result["buckets_usd_micros"]["cache_bust"] == 0


def test_recoverable_is_capped_at_each_sessions_own_spend():
    """The invariant that broke in production ($2,919 recoverable on $1,213 spend): per session,
    recoverable never exceeds cost; buckets scale down proportionally and still sum to the total."""
    capped = _priced_row(resume=600, bust=900, cost=1000)
    uncapped = _priced_row(interrupted=10, cost=1000)
    result = agg.recoverable([capped, uncapped])
    assert result["buckets_usd_micros"] == {"resume": 400, "cache_bust": 600, "interrupted": 10}
    assert result["recoverable_usd_micros"] == 1010
    assert result["recoverable_usd_micros"] <= result["total_cost_usd_micros"]
    assert sum(result["buckets_usd_micros"].values()) == result["recoverable_usd_micros"]


def test_recoverable_is_zero_for_a_session_with_no_known_spend():
    result = agg.recoverable([_priced_row(resume=200, cost=0)])
    assert result["recoverable_usd_micros"] == 0


def test_recoverable_dominant_cause_matches_the_largest_bucket():
    assert agg.recoverable([_priced_row(resume=500, bust=10, cost=1000)])["dominant_cause"] == "resume"
    assert agg.recoverable([_priced_row(resume=10, bust=500, cost=1000)])["dominant_cause"] == "cache_bust"


def test_recoverable_is_none_when_nothing_recoverable():
    result = agg.recoverable([row(cost_effective_usd_micros=1000)])
    assert result["recoverable_usd_micros"] == 0
    assert result["dominant_cause"] is None


def test_recoverable_pct_is_none_not_a_div_by_zero_with_no_spend():
    assert agg.recoverable([]) ["recoverable_pct"] is None


def test_segment_cost_by_repo_key_sums_priced_segments_per_repo():
    info = {"segments": [
        {"repo_key": "a", "cost_usd_micros": 300_000},
        {"repo_key": "a", "cost_usd_micros": 200_000},
        {"repo_key": "b", "cost_usd_micros": 50_000},
    ]}
    assert agg.segment_cost_by_repo_key(info) == {"a": 500_000, "b": 50_000}


def test_segment_cost_by_repo_key_is_all_or_nothing_per_repo():
    """One unpriced segment in a repo's own bucket makes that bucket `None`, not a partial
    sum -- a sibling repo with every segment priced is unaffected."""
    info = {"segments": [
        {"repo_key": "a", "cost_usd_micros": 300_000},
        {"repo_key": "a", "cost_usd_micros": None},
        {"repo_key": "b", "cost_usd_micros": 50_000},
    ]}
    assert agg.segment_cost_by_repo_key(info) == {"a": None, "b": 50_000}


def test_segment_cost_by_repo_key_skips_segments_with_no_repo_and_empty_when_no_segments():
    assert agg.segment_cost_by_repo_key({"segments": [{"repo_key": None, "cost_usd_micros": 10}]}) == {}
    assert agg.segment_cost_by_repo_key({}) == {}


def test_habits_reconcile_to_recoverable_spend():
    rows = [_priced_row(resume=150, bust=20, interrupted=5, revision_loop_cost=80, carry=400, cost=1000)]
    breakdown = agg.recoverable(rows)
    habits = agg.habits_from_recoverable(breakdown)
    assert sum(h["recoverable_usd_micros"] for h in habits) == breakdown["recoverable_usd_micros"]
    assert {h["category"] for h in habits} == {"resume", "cache_bust", "interrupted"}
    # tone lexicon: no banned words in the labels
    for h in habits:
        assert "waste" not in h["label"].lower()


# ------------------------------------------------------------------------------- X4

def test_model_tier_reads_the_family_name_substring():
    assert agg.model_tier("claude-opus-5") == "high"
    assert agg.model_tier("claude-haiku-4-5-20251001") == "low"
    assert agg.model_tier("claude-sonnet-5") == "mid"
    assert agg.model_tier(None) == "mid"


def test_dominant_model_picks_the_most_used_by_total_tokens():
    models = {
        "claude-haiku-4-5-20251001": {"input": 100, "output": 10},
        "claude-opus-5": {"input": 5000, "output": 500},
    }
    assert agg.dominant_model(models) == "claude-opus-5"
    assert agg.dominant_model(None) is None
    assert agg.dominant_model({}) is None


#: model-fit-reasoning-demand-rework plan: the real config defaults, used directly rather
#: than re-typed, so a default drifting from these tests is a loud test failure.
_MODEL_FIT_CONFIG = ModelFitConfig()


def test_segment_reasoning_demand_plan_with_no_rework_scores_high():
    """Regression -- the whole point of this rework: PLAN produces little or no code but is
    the most reasoning-heavy work a session does; the old heuristic read "no files touched"
    as light. `beats={}` (no rework/stall of its own either) still scores high on the label
    weight alone."""
    segment = {"label": "PLAN", "beats": {}, "files": 0}
    assert agg.segment_reasoning_demand(segment, _MODEL_FIT_CONFIG) == "high"


def test_segment_reasoning_demand_comprehend_high_single_file_misc_low():
    comprehend = {"label": "COMPREHEND", "beats": {}, "files": 2}
    misc = {"label": "MISC", "beats": {}, "files": 1}
    assert agg.segment_reasoning_demand(comprehend, _MODEL_FIT_CONFIG) == "high"
    assert agg.segment_reasoning_demand(misc, _MODEL_FIT_CONFIG) == "low"


def test_segment_fit_good_high_tier_on_high_demand_plan_segment():
    """The direct assertion the old code got wrong: a `high` tier model spent on a PLAN
    segment is the textbook justified case -- never `over`."""
    segment = {"label": "PLAN", "beats": {}, "files": 0}
    assert agg.segment_fit(segment, "high", ModelFitConfig()) == "good"


def test_segment_fit_over_high_tier_on_a_low_demand_mechanical_segment():
    segment = {"label": "MISC", "beats": {}, "files": 1}
    assert agg.segment_fit(segment, "high", ModelFitConfig()) == "over"


def test_segment_fit_under_low_tier_on_a_revision_loop():
    """Inverts the old rule: a rework loop used to *suppress* `over`; now it's a high-demand
    signal that can *drive* `under` on a weak model instead -- the intended correction."""
    segment = {"label": "IMPLEMENT", "beats": {"revision_loop": 2}, "files": 1}
    assert agg.segment_fit(segment, "low", ModelFitConfig()) == "under"


def test_segment_fit_good_mid_tier_regardless_of_demand():
    assert agg.segment_fit({"label": "PLAN", "beats": {}}, "mid", ModelFitConfig()) == "good"
    assert agg.segment_fit({"label": "MISC", "beats": {}}, "mid", ModelFitConfig()) == "good"


def _segment(label, cost=0, revision_loop=False, t0=None, **kw):
    beats = {"revision_loop": 1} if revision_loop else {}
    seg = {"label": label, "cost_usd_micros": cost, "beats": beats, **kw}
    if t0:
        seg["t0"] = t0
    return seg


def _config(**model_fit_overrides) -> ApiConfig:
    return ApiConfig(model_fit=ModelFitConfig(**model_fit_overrides))


def test_model_fit_is_none_cold_start_with_no_segments():
    config = _config()
    assert agg.model_fit(row(info={}), config) is None
    assert agg.model_fit(row(info={"segments": []}), config) is None


def test_model_fit_golden_session_s1_over_an_opus_session_on_mechanical_work():
    """EFF X4 Verify: golden session s1 -- an oversized model spent entirely on mechanical,
    clean-outcome segments -- reproduces an `over` verdict with a real, priced savings
    estimate. (The pre-rework golden used COMPREHEND here; COMPREHEND is now a high-demand
    label and must never flag `over` -- MISC/REVIEW replace it as the genuinely light work.)
    """
    info = {
        "segments": [
            _segment("MISC", cost=400_000, t0="2026-01-01T00:00:00Z"),
            _segment("REVIEW", cost=100_000, t0="2026-01-01T00:00:00Z"),
        ],
    }
    s1 = row(info=info, models={"claude-opus-5": {"input": 1000, "output": 200}})
    result = agg.model_fit(s1, _config(), PRICING)
    assert result["verdict"] == "over"
    # claude-opus-4-8 ($5/$25, blended $15/M) vs claude-sonnet-5's 2026-01-01 block
    # ($2/$10, blended $6/M) -> ratio 0.4 -> savings = 500_000 * (1 - 0.4).
    assert result["savings_usd_micros"] == 300_000
    assert [s["fit"] for s in result["segments"]] == ["over", "over"]
    assert "opus" not in result["note"].lower()  # tone: names the tier, not the model


def test_model_fit_savings_is_none_without_a_pricing_table():
    """§3.4's honesty constraint: no pricing table means the ratio can't be priced, so
    `savings_usd_micros` is `None` -- never a guessed number wearing a real one's clothes."""
    info = {"segments": [_segment("MISC", cost=400_000, t0="2026-01-01T00:00:00Z")]}
    s1 = row(info=info, models={"claude-opus-5": {"input": 1000, "output": 200}})
    assert agg.model_fit(s1, _config(), None)["savings_usd_micros"] is None


def test_model_fit_savings_for_a_segment_dated_before_the_catalog_uses_the_earliest_row():
    """Design §4 #7 (behaviour change): the legacy table returned `None` for a segment dated
    before the reference model's earliest price block; the catalog applies its earliest row
    instead, so the 1999 segment prices like any other (500_000-style ratio 0.4 -> 0.6 saved)."""
    info = {"segments": [_segment("MISC", cost=400_000, t0="1999-01-01T00:00:00Z")]}
    s1 = row(info=info, models={"claude-opus-5": {"input": 1000, "output": 200}})
    assert agg.model_fit(s1, _config(), PRICING)["savings_usd_micros"] == 240_000


def test_model_fit_savings_is_none_when_the_reference_model_has_no_price(tmp_path):
    """Same honesty constraint, the other trigger: the tier's reference model is not in the
    price table at all."""
    import importlib.util
    from datetime import date
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "common" / "model_pricing" / "helpers.py"
    spec = importlib.util.spec_from_file_location("_pricing_test_helpers", path)
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    pricer = helpers.make_pricer(tmp_path, [helpers.row("unrelated-model", date(2026, 1, 1),
                                                         per_million={"input": 1, "output": 2})])
    info = {"segments": [_segment("MISC", cost=400_000, t0="2026-10-01T00:00:00Z")]}
    s1 = row(info=info, models={"claude-opus-5": {"input": 1000, "output": 200}})
    assert agg.model_fit(s1, _config(), pricer)["savings_usd_micros"] is None


def test_model_fit_golden_session_s2_under_a_haiku_session_driving_rework():
    """EFF X4 Verify: golden session s2 -- a cheap model drove a rework loop -- reproduces
    an `under` verdict (the rework loop now *drives* it, rather than merely failing to
    suppress an `over`); `savings_usd_micros` stays 0 (downgrading further isn't the fix)."""
    info = {
        "segments": [
            _segment("IMPLEMENT", cost=50_000, revision_loop=True),
            _segment("BUG_FIX", cost=30_000),
        ],
    }
    s2 = row(info=info, models={"claude-haiku-4-5-20251001": {"input": 800, "output": 150}})
    result = agg.model_fit(s2, _config(), PRICING)
    assert result["verdict"] == "under"
    assert result["savings_usd_micros"] == 0
    assert [s["fit"] for s in result["segments"]] == ["under", "good"]


def test_model_fit_verdict_good_when_no_segment_is_flagged():
    info = {"segments": [_segment("IMPLEMENT", cost=100_000)]}
    s = row(info=info, models={"claude-sonnet-5": {"input": 500, "output": 100}})
    result = agg.model_fit(s, _config(), PRICING)
    assert result["verdict"] == "good"
    assert result["savings_usd_micros"] == 0
    assert result["segments"][0]["fit"] == "good"


def test_pick_model_fit_verdict_under_wins_over_over_when_both_present():
    """A session's tier is singular today (its one dominant model), so `over` (needs `high`)
    and `under` (needs `low`) can never actually co-occur through `model_fit` itself yet
    (TODO(model-fit-per-segment)) -- this tests the precedence rule directly, independent of
    that limitation: `under`, a cheap model actively causing rework, is the sharper, more
    actionable finding and should win (EFF frames "Quick & dirty" as the costlier failure
    mode)."""
    segments = [{"fit": "over"}, {"fit": "under"}, {"fit": "good"}]
    verdict, flagged = agg._pick_model_fit_verdict(segments)
    assert verdict == "under"
    assert flagged == [{"fit": "under"}]


# ------------------------------------------------------------------------ shared derived metrics

def test_blended_cost_per_pr_is_none_with_no_merged_prs():
    assert agg.blended_cost_per_pr_usd_micros(50_000_000, 0) is None


def test_blended_cost_per_pr_divides_scope_spend_by_merged_count():
    assert agg.blended_cost_per_pr_usd_micros(50_000_000, 2) == 25_000_000


def test_rework_rate_is_none_when_nothing_classified():
    prs = [SimpleNamespace(rework_ratio=None), SimpleNamespace(rework_ratio=None)]
    assert agg.rework_rate(prs) is None


def test_rework_rate_averages_over_classified_prs_only():
    prs = [SimpleNamespace(rework_ratio=0.2), SimpleNamespace(rework_ratio=None),
          SimpleNamespace(rework_ratio=0.4)]
    assert agg.rework_rate(prs) == pytest.approx(0.3)


def test_budget_pct_is_none_with_no_budget():
    assert agg.budget_pct(1000, None) is None
    assert agg.budget_pct(1000, 0) is None


def test_budget_pct_divides_spend_by_window_budget():
    assert agg.budget_pct(1000, 2000) == 0.5


# ------------------------------------------------------ cost-based seats (2026-09-26 plan)

_UTC = timezone.utc
_TO = datetime(2026, 9, 22, tzinfo=_UTC)


def _window(days: int) -> tuple[datetime, datetime]:
    return _TO - timedelta(days=days), _TO


def test_window_days_is_the_span_and_rejects_an_empty_window():
    assert agg.window_days(*_window(15)) == 15
    with pytest.raises(BadRequestError):
        agg.window_days(_TO, _TO)


def test_budget_usd_micros_converts_decimal_and_string():
    assert agg.budget_usd_micros(Decimal("9000.00")) == 9_000_000_000
    assert agg.budget_usd_micros("12.50") == 12_500_000
    assert agg.budget_usd_micros(None) is None


def test_window_budget_scales_by_window_days():
    monthly = 3_000_000_000  # $3,000
    assert agg.window_budget_usd_micros(monthly, *_window(30), "monthly") == monthly
    assert agg.window_budget_usd_micros(monthly, *_window(15), "monthly") == monthly // 2
    assert agg.window_budget_usd_micros(monthly, *_window(60), "monthly") == monthly * 2


def test_window_budget_none_without_budget():
    assert agg.window_budget_usd_micros(None, *_window(30), "monthly") is None
    assert agg.window_budget_usd_micros(0, *_window(30), "monthly") is None


def test_seat_allowance_full_window_when_seat_predates_it():
    frm, to = _window(30)
    assert agg.seat_allowance_usd_micros(450_000_000, frm - timedelta(days=90), frm, to,
                                         "monthly") == 450_000_000


def test_seat_allowance_prorates_from_seat_start():
    frm, to = _window(30)
    # joined 10 days into a 30-day window -> 20 days' worth
    assert agg.seat_allowance_usd_micros(300_000_000, frm + timedelta(days=10), frm, to,
                                         "monthly") == 200_000_000


def test_seat_allowance_none_when_seat_starts_after_window_or_no_budget():
    frm, to = _window(30)
    assert agg.seat_allowance_usd_micros(300_000_000, to, frm, to, "monthly") is None
    assert agg.seat_allowance_usd_micros(None, None, frm, to, "monthly") is None


def test_in_grace_boundary():
    assert agg.in_grace(_TO - timedelta(days=13), _TO, 14) is True
    assert agg.in_grace(_TO - timedelta(days=14), _TO, 14) is False  # exactly 14 days: not new
    assert agg.in_grace(None, _TO, 14) is False


def test_is_low_use_needs_allowance_band_and_no_grace():
    old = _TO - timedelta(days=60)
    assert agg.is_low_use(0.2, old, _TO, UTIL) is True
    assert agg.is_low_use(0.5, old, _TO, UTIL) is False
    assert agg.is_low_use(None, old, _TO, UTIL) is False
    assert agg.is_low_use(0.2, _TO - timedelta(days=3), _TO, UTIL) is False  # new seat


def test_usage_pct_none_without_allowance():
    assert agg.usage_pct(100, None) is None
    assert agg.usage_pct(100, 400) == 0.25


def test_usage_status_bands():
    assert agg.usage_status(None, UTIL) is None
    assert agg.usage_status(0.2, UTIL) == "under"
    assert agg.usage_status(0.6, UTIL) == "healthy"
    assert agg.usage_status(0.99, UTIL) == "over"


def test_seat_usage_status_turns_under_into_new_inside_grace_only():
    old, new = _TO - timedelta(days=60), _TO - timedelta(days=3)
    assert agg.seat_usage_status(0.2, old, _TO, UTIL) == "under"
    assert agg.seat_usage_status(0.2, new, _TO, UTIL) == "new"
    assert agg.seat_usage_status(1.17, new, _TO, UTIL) == "over"   # grace never hides over-use
    assert agg.seat_usage_status(0.6, new, _TO, UTIL) == "healthy"
    assert agg.seat_usage_status(None, old, _TO, UTIL) is None


def test_person_headline_status_is_on_track_only_when_nothing_flags():
    assert agg.person_headline_status("room-to-save", "over") == "room-to-save"
    assert agg.person_headline_status("on-track", "over") == "over"
    assert agg.person_headline_status("on-track", "under") == "under"
    assert agg.person_headline_status("on-track", "new") == "new"
    assert agg.person_headline_status("on-track", "healthy") == "on-track"
    assert agg.person_headline_status("on-track", None) == "on-track"


def test_avg_session_minutes_root_only():
    from datetime import datetime, timedelta

    t0 = datetime(2026, 1, 1)
    rows = [
        row(started_at=t0, ended_at=t0 + timedelta(minutes=30)),
        row(started_at=t0, ended_at=t0 + timedelta(minutes=90), parent_session_id="p1"),  # subagent
    ]
    assert agg.avg_session_minutes(rows) == 30


def test_sessions_with_no_output_pct():
    rows = [row(files={"a.py": {}}), row(files={}), row(files=None)]
    assert agg.sessions_with_no_output_pct(rows) == pytest.approx(2 / 3)


def test_session_type_counts_root_only_and_omits_unset():
    impl = [{"label": "IMPLEMENT", "turns": 9}, {"label": "PLAN", "turns": 2}]
    rows = [
        row(info={"segments": impl}),
        row(info={"segments": [{"label": "COMPREHEND", "turns": 1}, {"label": "IMPLEMENT", "turns": 4}]}),
        row(info={"segments": [{"label": "BUG_FIX", "turns": 5}]}, parent_session_id="p1"),  # subagent: excluded
        row(info={}),  # no segments yet: omitted, not "unknown"
        row(info={"segments": []}),
    ]
    assert agg.session_type_counts(rows) == [{"type": "IMPLEMENT", "count": 2}]


def test_dominant_segment_label_picks_most_turns():
    assert agg.dominant_segment_label([]) is None
    assert agg.dominant_segment_label([{"label": "PLAN", "turns": 3}, {"label": "REVIEW", "turns": 7}]) == "REVIEW"


def test_seats_under_threshold_is_none_with_no_allowance():
    assert agg.seats_under_threshold(["a"], {"a": 10}, {"a": None}, {}, _TO, UTIL) is None


def test_seats_under_counts_members_below_the_band_including_idle_ones():
    old = _TO - timedelta(days=90)
    members = ["heavy", "light", "idle"]
    spend = {"heavy": 900, "light": 100}          # "idle" has no sessions at all
    allowance = {"heavy": 1000, "light": 1000, "idle": 1000}
    starts = {uid: old for uid in members}
    # under-band 0.40 -> light (10%) and idle (0%) count; heavy (90%) doesn't
    assert agg.seats_under_threshold(members, spend, allowance, starts, _TO, UTIL) == 2


def test_seats_under_respects_the_new_seat_grace_period():
    members = ["new"]
    starts = {"new": _TO - timedelta(days=5)}
    assert agg.seats_under_threshold(members, {}, {"new": 1000}, starts, _TO, UTIL) == 0


def _typed(label, **kw):
    return row(info={"segments": [{"label": label, "turns": 5}]}, **kw)


def test_agent_task_mix_counts_root_sessions_per_agent_and_type():
    rows = [
        _typed("IMPLEMENT", agent="codex"),
        _typed("IMPLEMENT", agent="claude_code"),
        _typed("IMPLEMENT", agent="claude_code"),
        _typed("BUG_FIX", agent="claude_code"),
        _typed("PLAN", agent="claude_code"),
        _typed("BUG_FIX", agent="claude_code", parent_session_id="p1"),  # sub-agent: excluded
        row(info={}, agent="codex"),                                       # no type: omitted
        row(info={"segments": []}, agent="cursor"),                        # cursor has none at all
    ]
    assert agg.agent_task_mix(rows) == [
        {"agent": "claude_code", "type": "IMPLEMENT", "count": 2},   # count desc ...
        {"agent": "claude_code", "type": "BUG_FIX", "count": 1},     # ... then type asc on ties
        {"agent": "claude_code", "type": "PLAN", "count": 1},
        {"agent": "codex", "type": "IMPLEMENT", "count": 1},
    ]
    assert agg.agent_task_mix([]) == []


def test_agent_task_mix_summed_per_type_equals_session_type_counts():
    rows = [_typed(t, agent=a, parent_session_id=p) for t, a, p in [
        ("IMPLEMENT", "claude_code", None), ("IMPLEMENT", "codex", None), ("BUG_FIX", "codex", None),
        ("REVIEW", "claude_code", None), ("IMPLEMENT", "cursor", "p"), ("PLAN", "cursor", None),
    ]] + [row(info={}, agent="codex")]
    per_type: dict[str, int] = {}
    for p in agg.agent_task_mix(rows):
        per_type[p["type"]] = per_type.get(p["type"], 0) + p["count"]
    assert per_type == {t["type"]: t["count"] for t in agg.session_type_counts(rows)}


def test_velocity_weekly():
    from datetime import datetime

    rows = [SimpleNamespace(week=datetime(2026, 1, 5), n=3), SimpleNamespace(week=datetime(2026, 1, 12), n=1)]
    assert agg.velocity_weekly(rows) == [
        {"week": "2026-01-05", "merged_prs": 3},
        {"week": "2026-01-12", "merged_prs": 1},
    ]


def pr_row(additions=None, deletions=None, artifact_id="pr"):
    stats = {"additions": additions, "deletions": deletions} if additions is not None else {}
    return SimpleNamespace(stats=stats, artifact_id=artifact_id)


# ------------------------------------------------------------------- A5-peer-task-mix: $/KLOC

def test_linked_work_volume_counts_only_prs_with_linked_sessions():
    """An unlinked PR's lines don't count, and neither does an unlinked session's cost."""
    prs = [pr_row(100, 20, "a"), pr_row(50, 5, "b"), pr_row(400, 0, "unlinked")]
    volume = agg.linked_work_volume(prs, {"a": ["s1"], "b": ["s2"]}, {"s1": 3_000_000, "s2": 1_000_000}, PEER)
    assert volume == {"merged_prs": 3, "sizable_prs": 3, "linked_prs": 2, "lines_changed": 175,
                      "cost_usd_micros": 4_000_000,
                      "cost_per_kloc_usd_micros": agg.cost_per_kloc_usd_micros(4_000_000, 175)}


def test_linked_work_volume_costs_a_session_shared_by_two_prs_once():
    prs = [pr_row(300, 0, "a"), pr_row(300, 0, "b")]
    volume = agg.linked_work_volume(prs, {"a": ["s1"], "b": ["s1"]}, {"s1": 6_000_000}, PEER)
    assert volume["cost_usd_micros"] == 6_000_000
    assert volume["lines_changed"] == 600


def test_linked_work_volume_sizes_every_statted_pr_by_default():
    """The PR-size cap is off by default (TODO(peer-generated-lines)); a PR without line stats still
    can't be sized -- out of lines, cost, and the sizable-PR denominator alike."""
    prs = [pr_row(300, 0, "a"), pr_row(400_000, 0, "vendored"), pr_row(artifact_id="no-stats")]
    links = {"a": ["s1"], "vendored": ["s2"], "no-stats": ["s3"]}
    costs = {"s1": 1_000_000, "s2": 50_000_000, "s3": 9_000_000}
    volume = agg.linked_work_volume(prs, links, costs, PEER)
    assert (volume["sizable_prs"], volume["linked_prs"], volume["lines_changed"], volume["cost_usd_micros"]) == \
        (2, 2, 400_300, 51_000_000)


def test_linked_work_volume_can_still_cap_pr_size_when_configured():
    prs = [pr_row(300, 0, "a"), pr_row(400_000, 0, "vendored")]
    links = {"a": ["s1"], "vendored": ["s2"]}
    costs = {"s1": 1_000_000, "s2": 50_000_000}
    capped = agg.linked_work_volume(prs, links, costs, PeerConfig(max_pr_lines=10_000))
    assert (capped["sizable_prs"], capped["lines_changed"], capped["cost_usd_micros"]) == (1, 300, 1_000_000)


def test_linked_work_volume_has_no_rate_without_linked_cost():
    volume = agg.linked_work_volume([pr_row(300, 0, "a")], {}, {}, PEER)
    assert volume["cost_per_kloc_usd_micros"] is None


def test_cost_per_kloc_is_none_with_no_lines():
    assert agg.cost_per_kloc_usd_micros(50_000_000, None) is None
    assert agg.cost_per_kloc_usd_micros(50_000_000, 0) is None


def test_cost_per_kloc_divides_cost_by_thousands_of_lines():
    assert agg.cost_per_kloc_usd_micros(50_000_000, 2_000) == 25_000_000


def _vol(per_kloc, linked=3, sizable=3, lines=3_000):
    return {"merged_prs": sizable, "sizable_prs": sizable, "linked_prs": linked, "lines_changed": lines,
            "cost_usd_micros": 1, "cost_per_kloc_usd_micros": per_kloc}


def test_cheapest_peer_returns_the_genuinely_lower_cost_per_kloc():
    candidates = {"me": _vol(90), "cheap": _vol(20), "expensive": _vol(100)}
    assert agg.cheapest_peer(candidates, "me", PEER) == {"user_id": "cheap", "cost_per_kloc_usd_micros": 20}


def test_cheapest_peer_skips_a_teammate_with_no_session_telemetry():
    """The live bug: lots of merged PRs, zero linked sessions -- read as $0/KLOC and named
    cheapest. With no linked work there is no rate, so they aren't comparable."""
    candidates = {"me": _vol(90), "no-telemetry": _vol(None, linked=0, sizable=10, lines=0)}
    assert agg.cheapest_peer(candidates, "me", PEER) is None


def test_cheapest_peer_requires_enough_linked_prs():
    candidates = {"me": _vol(90), "one-pr": _vol(10, linked=2, sizable=2)}
    assert agg.cheapest_peer(candidates, "me", PEER) is None


def test_cheapest_peer_requires_most_of_a_persons_work_to_be_linked():
    candidates = {"me": _vol(90), "mostly-invisible": _vol(10, linked=3, sizable=7)}
    assert agg.cheapest_peer(candidates, "me", PEER) is None
    assert agg.cheapest_peer(candidates, "me", PeerConfig(min_linked_share=0.4))["user_id"] == "mostly-invisible"


def test_cheapest_peer_is_none_when_the_subject_is_not_comparable():
    candidates = {"cheap": _vol(20)}
    assert agg.cheapest_peer({**candidates, "me": _vol(90, lines=50)}, "me", PEER) is None      # line floor
    assert agg.cheapest_peer({**candidates, "me": _vol(90, linked=1, sizable=1)}, "me", PEER) is None
    assert agg.cheapest_peer(candidates, "me", PEER) is None                                      # no volume at all


def test_cheapest_peer_respects_the_scale_band():
    """Default band 0.2x-5x of the subject's linked lines; a candidate outside it isn't comparable
    even with a lower $/KLOC -- unless the band is disabled."""
    candidates = {"me": _vol(90, lines=1_000), "huge": _vol(10, lines=50_000)}
    assert agg.cheapest_peer(candidates, "me", PEER) is None
    wide_open = PeerConfig(band_low=None, band_high=None)
    assert agg.cheapest_peer(candidates, "me", wide_open) == {"user_id": "huge", "cost_per_kloc_usd_micros": 10}


def test_group_rows_by_user():
    rows = [row(user_id="a"), row(user_id="a"), row(user_id="b"), row(user_id=None)]
    by_user = agg.group_rows_by_user(rows)
    assert len(by_user["a"]) == 2
    assert len(by_user["b"]) == 1
    assert None not in by_user


def test_room_to_save_needs_both_the_share_and_the_floor():
    frm, to = _window(30)   # floor = $10
    # 12% share, $24 recoverable -> flagged
    assert agg.person_status(24_000_000, 200_000_000, frm, to, UTIL) == "room-to-save"
    # 12% share but only $3 recoverable (below the $10 floor) -> not flagged
    assert agg.person_status(3_000_000, 25_000_000, frm, to, UTIL) == "on-track"
    # $200 recoverable but only 8% of spend -> not flagged
    assert agg.person_status(200_000_000, 2_500_000_000, frm, to, UTIL) == "on-track"


def test_room_to_save_floor_scales_with_the_window():
    # $6 recoverable at a 15% share: over the $5 floor of a 15-day window, under a 30-day one's $10
    assert agg.person_status(6_000_000, 40_000_000, *_window(15), UTIL) == "room-to-save"
    assert agg.person_status(6_000_000, 40_000_000, *_window(30), UTIL) == "on-track"


def test_room_to_save_never_with_zero_spend():
    assert agg.person_status(0, 0, *_window(30), UTIL) == "on-track"


def test_avg_time_to_merge_days():
    from datetime import datetime, timedelta

    t0 = datetime(2026, 1, 1)
    prs = [
        SimpleNamespace(created_at=t0, merged_at=t0 + timedelta(days=2)),
        SimpleNamespace(created_at=t0, merged_at=t0 + timedelta(days=4)),
        SimpleNamespace(created_at=t0, merged_at=None),  # not merged: excluded
    ]
    assert agg.avg_time_to_merge_days(prs) == 3
    assert agg.avg_time_to_merge_days([]) is None


def test_last_active_at_is_the_max_last_event_at():
    from datetime import datetime

    t1, t2 = datetime(2026, 1, 1), datetime(2026, 1, 5)
    rows = [row(last_event_at=t1), row(last_event_at=t2)]
    assert agg.last_active_at(rows) == t2
    assert agg.last_active_at([]) is None


def test_repo_contributors_and_merged_prs_by_repo():
    rows = [
        row(user_id="a", repo_id="github:x/y", repo_id_provenance="remote"),
        row(user_id="b", repo_id="github:x/y", repo_id_provenance="remote"),
    ]
    contributors = agg.repo_contributors(rows)
    assert contributors[("github:x/y", None)] == {"a", "b"}

    prs = [SimpleNamespace(repo_id="github:x/y"), SimpleNamespace(repo_id="github:x/y")]
    assert len(agg.merged_prs_by_repo(prs)["github:x/y"]) == 2


def test_sort_desc_none_last():
    items = [{"name": "a", "v": 5}, {"name": "b", "v": None}, {"name": "c", "v": 10}]
    result = agg.sort_desc_none_last(items, "v")
    assert [it["name"] for it in result] == ["c", "a", "b"]


def test_paginate_and_search_by_name():
    items = [{"name": "Platform"}, {"name": "Growth"}, {"name": "Platform-2"}]
    assert agg.search_by_name(items, "plat") == [{"name": "Platform"}, {"name": "Platform-2"}]
    assert agg.search_by_name(items, None) == items
    assert agg.paginate(items, limit=2, offset=1) == [{"name": "Growth"}, {"name": "Platform-2"}]


def _utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_elapsed_utc_days_stop_at_now_or_the_exclusive_end():
    frm, to = _utc(2026, 1, 1), _utc(2026, 1, 4)
    # to is exclusive: a window ending at midnight does not include the next day
    assert agg.elapsed_utc_days(frm, to, _utc(2026, 2, 1)) == [
        date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    # mid-window: today is included, later days are not
    assert agg.elapsed_utc_days(frm, to, _utc(2026, 1, 2, 9)) == [date(2026, 1, 1), date(2026, 1, 2)]
    # a rolling window starts mid-day: its first (partial) day counts
    assert agg.elapsed_utc_days(_utc(2026, 1, 1, 18), _utc(2026, 1, 3, 6), _utc(2026, 1, 3, 6)) == [
        date(2026, 1, 1), date(2026, 1, 2), date(2026, 1, 3)]
    # not started yet: nothing has elapsed
    assert agg.elapsed_utc_days(frm, to, _utc(2025, 12, 31)) == []
    # a naive datetime is taken as UTC
    assert agg.elapsed_utc_days(frm, to, datetime(2026, 1, 1, 5)) == [date(2026, 1, 1)]


CHAIN = ["otel_vendor", "estimated"]


def _spend_row(started_at, cost, vendor=None, **kw):
    return row(started_at=started_at, cost_effective_usd_micros=cost,
               otel_vendor_cost_usd_micros=vendor, **kw)


def test_daily_spend_zero_fills_and_buckets_on_the_utc_day():
    frm, to = _utc(2026, 1, 1), _utc(2026, 1, 6)
    rows = [
        _spend_row(_utc(2026, 1, 2, 23, 59, 59), 10),      # last second of the 2nd
        _spend_row(_utc(2026, 1, 3, 0, 0, 0), 20, vendor=15),  # first of the 3rd; the chain wins
        _spend_row(_utc(2026, 1, 3, 8), 5, parent_session_id="p"),  # sub-agents count too
    ]
    out = agg.daily_spend(rows, CHAIN, frm, to, now=_utc(2026, 2, 1))
    assert out == [{"day": date(2026, 1, d), "spend_usd_micros": v}
                   for d, v in [(1, 0), (2, 10), (3, 20), (4, 0), (5, 0)]]


def test_daily_spend_counts_a_session_that_began_before_the_window_on_its_first_day():
    frm, to = _utc(2026, 1, 10), _utc(2026, 1, 13)
    rows = [_spend_row(_utc(2026, 1, 8, 12), 7), _spend_row(_utc(2026, 1, 11, 1), 3)]
    out = agg.daily_spend(rows, CHAIN, frm, to, now=_utc(2026, 2, 1))
    assert [p["spend_usd_micros"] for p in out] == [7, 3, 0]


def test_daily_spend_clamps_out_of_range_days_so_the_sum_still_reconciles():
    frm, to = _utc(2026, 1, 1), _utc(2026, 1, 31)
    now = _utc(2026, 1, 3, 12)
    rows = [
        _spend_row(_utc(2026, 1, 2), 4),
        _spend_row(_utc(2026, 1, 20), 6),   # dated after `now`: clamped into today
        _spend_row(_utc(2026, 1, 3), 1),
    ]
    out = agg.daily_spend(rows, CHAIN, frm, to, now)
    assert [(p["day"].day, p["spend_usd_micros"]) for p in out] == [(1, 0), (2, 4), (3, 7)]
    assert sum(p["spend_usd_micros"] for p in out) == precedence.resolve_aggregate(rows, CHAIN)[0]


def test_daily_spend_skips_unresolvable_rows_like_resolve_aggregate():
    rows = [_spend_row(_utc(2026, 1, 1, 1), None), _spend_row(_utc(2026, 1, 1, 2), 9)]
    out = agg.daily_spend(rows, CHAIN, _utc(2026, 1, 1), _utc(2026, 1, 2), now=_utc(2026, 1, 5))
    assert out == [{"day": date(2026, 1, 1), "spend_usd_micros": 9}]


def test_daily_spend_empty_and_not_yet_started_windows():
    frm, to = _utc(2026, 1, 1), _utc(2026, 1, 3)
    assert agg.daily_spend([], CHAIN, frm, to, now=_utc(2026, 2, 1)) == [
        {"day": date(2026, 1, 1), "spend_usd_micros": 0},
        {"day": date(2026, 1, 2), "spend_usd_micros": 0}]
    assert agg.daily_spend([], CHAIN, frm, to, now=_utc(2025, 12, 1)) == []


def test_group_rows_by_agent_partitions_every_row_once():
    a, b, c, d = (row(agent="claude_code"), row(agent="codex"),
                  row(agent="claude_code", parent_session_id="p1"),  # sub-agents are kept
                  row(agent="cursor"))
    groups = agg.group_rows_by_agent([a, b, c, d])
    assert groups == {"claude_code": [a, c], "codex": [b], "cursor": [d]}
    assert sum(len(g) for g in groups.values()) == 4
    assert agg.group_rows_by_agent([]) == {}


def test_per_agent_resolved_cost_sums_to_the_whole_scope_cost():
    """The B3 invariant at the aggregate level: resolution is per row, so resolving each agent's
    group and summing equals resolving the whole scope -- including rows where the chain's
    vendor rung and the raw `cost_effective_usd_micros` disagree."""
    chain = ["otel_vendor", "estimated"]
    rows = [
        row(agent="claude_code", cost_effective_usd_micros=100, otel_vendor_cost_usd_micros=90),
        row(agent="claude_code", cost_effective_usd_micros=50, otel_vendor_cost_usd_micros=None),
        row(agent="codex", cost_effective_usd_micros=500, otel_vendor_cost_usd_micros=480),
        row(agent="cursor", cost_effective_usd_micros=7, otel_vendor_cost_usd_micros=None),
    ]
    whole, _ = precedence.resolve_aggregate(rows, chain)
    per_agent = {a: precedence.resolve_aggregate(g, chain)[0]
                 for a, g in agg.group_rows_by_agent(rows).items()}
    assert per_agent == {"claude_code": 140, "codex": 480, "cursor": 7}
    assert sum(per_agent.values()) == whole == 627


# ------------------------------------------------------------------------------- D2

def _channel_row(output=0, recache=0, carry=0, input_=0, priced=True):
    """`channel_priced`/`channel_*` are person/PR-only SQL fragments (D7 -- never promoted to
    columns, so `session_scalars` has no Python mirror of them); the fixture sets the scalars
    directly, the same shape `person_session_facts`/`sessions_by_ids` actually select."""
    return row(
        channel_priced=priced,
        channel_output_usd_micros=output if priced else 0,
        channel_recache_usd_micros=recache if priced else 0,
        channel_input_usd_micros=input_ if priced else 0,
        channel_carry_usd_micros=carry if priced else 0,
    )


def test_channel_totals_sums_across_sessions():
    rows = [_channel_row(output=100, carry=10), _channel_row(output=50, recache=5, input_=2)]
    result = agg.channel_totals(rows)
    assert result["totals_usd_micros"] == {"carry": 10, "recache": 5, "output": 150, "input": 2}
    assert result["complete"] is True


def test_channel_totals_incomplete_when_any_row_is_unpriced():
    rows = [_channel_row(output=100), _channel_row(priced=False)]
    result = agg.channel_totals(rows)
    assert result["complete"] is False
    assert result["totals_usd_micros"]["output"] == 100  # the priced row still counts


def test_channel_totals_is_none_cold_start_with_no_priced_rows_at_all():
    assert agg.channel_totals([_channel_row(priced=False)]) is None
    assert agg.channel_totals([]) is None


def test_pre_post_open_cost_split_partitions_at_the_opened_timestamp():
    from datetime import datetime, timezone
    opened = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    rows = [
        row(started_at=datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc), cost_effective_usd_micros=100),
        row(started_at=datetime(2026, 9, 5, 9, 0, tzinfo=timezone.utc), cost_effective_usd_micros=40),
    ]
    assert agg.pre_post_open_cost_split(rows, opened) == {
        "pre_open_usd_micros": 100, "post_open_usd_micros": 40}


def test_pre_post_open_cost_split_buckets_unknown_start_as_pre_open():
    from datetime import datetime, timezone
    opened = datetime(2026, 9, 4, 12, 0, tzinfo=timezone.utc)
    rows = [row(started_at=None, cost_effective_usd_micros=30)]
    assert agg.pre_post_open_cost_split(rows, opened) == {
        "pre_open_usd_micros": 30, "post_open_usd_micros": 0}


# ------------------------------------------------------------------------- D3-benchmark

def _feat(fid, usd_micros):
    return {"id": fid, "spend": {"usd_micros": usd_micros, "cost_basis": "cost_effective",
                                 "cost_complete": None}}


def test_feature_benchmark_ranks_subject_among_priced_peers():
    features = [_feat("s", 50_000_000)] + [
        _feat(f"p{i}", c) for i, c in enumerate([10_000_000, 20_000_000, 80_000_000, 90_000_000])
    ]
    b = agg.feature_benchmark(features, "s")
    assert b is not None
    assert b["subject_cost_usd_micros"] == 50_000_000
    assert b["peer_count"] == 4
    assert b["cost_percentile"] == 50          # more expensive than 2 of 4 peers
    assert b["cost_dist"]["min"] == 10          # dollars, not micros
    assert b["cost_dist"]["max"] == 90


def test_feature_benchmark_cold_start_below_min_peers():
    """A repo with too few *other priced* features can't rank a feature honestly -> None."""
    features = [_feat("s", 50_000_000), _feat("p1", 20_000_000), _feat("p2", 30_000_000)]
    assert agg.feature_benchmark(features, "s") is None   # only 2 peers, floor is 3


def test_feature_benchmark_ignores_zero_spend_peers_and_the_subject_itself():
    features = [_feat("s", 40_000_000), _feat("z1", 0), _feat("z2", None),
               _feat("p1", 10_000_000), _feat("p2", 20_000_000), _feat("p3", 60_000_000)]
    b = agg.feature_benchmark(features, "s")
    assert b is not None
    assert b["peer_count"] == 3                 # z1/z2 excluded, subject excluded
    assert b["cost_percentile"] == 67           # more than 2 of 3 priced peers


def test_feature_benchmark_none_when_subject_absent():
    assert agg.feature_benchmark([_feat("p1", 10_000_000)], "missing") is None


# ------------------------------------------------- budget cycles: window budget and seat allowance

from server.api.manager import budget_cycles as _bc  # noqa: E402

_U = timezone.utc
_MONTH = _bc.ActiveCycle("monthly", _bc.DEFAULT_CYCLE)
_QUARTER = _bc.ActiveCycle("quarterly", _bc.validate("quarter"))
_SPRINT = _bc.ActiveCycle("sprint", _bc.validate("sprint", anchor_date="2026-01-05", sprint_weeks=2,
                                                 anchor_number=10))


def _d(y, m, d, h=0, mi=0, tz=_U):
    return datetime(y, m, d, h, mi, tzinfo=tz)


def test_a_whole_period_and_a_period_to_date_get_the_full_budget():
    b = 3_100_000_000
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 2, 1), _MONTH) == b      # whole
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 1, 10), _MONTH) == b     # to-date
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 1, 1, 6), _MONTH) == b   # day one
    assert agg.window_budget_usd_micros(b, _d(2026, 2, 1), _d(2026, 3, 1), "monthly") == b   # 28 days


def test_a_window_that_only_resembles_a_period_is_rolling():
    b = 3_100_000_000
    # starts mid-period, starts at 00:30, or in another timezone: prorated by days
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 2), _d(2026, 1, 17), _MONTH) == b * 15 // 31
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1, 0, 30), _d(2026, 1, 16, 0, 30), _MONTH) == (
        b * 15 // 31)
    ist = timezone(timedelta(hours=5, minutes=30))
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1, tz=ist), _d(2026, 1, 16, tz=ist), _MONTH) == (
        round(b * 15 / 31))
    # runs past the end of the period it starts in: no longer a to-date
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 2, 2), _MONTH) == round(b * 32 / 28)


def test_rolling_windows_use_the_actual_length_of_the_period_containing_the_end():
    b = 2_800_000_000
    assert agg.window_budget_usd_micros(b, _d(2026, 2, 10), _d(2026, 2, 24), _MONTH) == b // 2   # 14/28
    assert agg.window_budget_usd_micros(b, _d(2026, 2, 10), _d(2026, 2, 24), "monthly") == b // 2
    # exclusive end at midnight on the 1st: the window ended in the PREVIOUS period (December, 31)
    assert agg.window_budget_usd_micros(b, _d(2025, 12, 2), _d(2026, 1, 1), _MONTH) == round(b * 30 / 31)
    assert agg.window_budget_usd_micros(b, _d(2026, 3, 2), _d(2026, 4, 1), _MONTH) == round(b * 30 / 31)


def test_quarter_and_sprint_cycles_use_their_own_period():
    b = 9_000_000_000
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 3, 1), _QUARTER) == b        # to-date
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 4, 1), _QUARTER) == b        # whole
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 16), _d(2026, 1, 31), _QUARTER) == round(b * 15 / 90)
    # sprint 10 = 5-19 Jan (14 days); sprint 11 starts on the 19th
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 5), _d(2026, 1, 19), _SPRINT) == b
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 19), _d(2026, 1, 21), _SPRINT) == b
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 6), _d(2026, 1, 13), _SPRINT) == b // 2    # 7/14


def test_a_window_outside_a_one_time_cycle_is_prorated_over_the_cycle_length():
    once = _bc.ActiveCycle("custom", _bc.validate("custom", "one-time", anchor_date="2026-01-01",
                                                  custom_end="2026-01-20"))
    b = 2_000_000_000
    assert agg.window_budget_usd_micros(b, _d(2026, 1, 1), _d(2026, 1, 10), once) == b
    assert agg.window_budget_usd_micros(b, _d(2026, 6, 1), _d(2026, 6, 11), once) == b // 2   # 10/20


def test_window_budget_still_none_without_a_budget_for_any_cycle():
    assert agg.window_budget_usd_micros(None, _d(2026, 1, 1), _d(2026, 1, 10), _SPRINT) is None
    assert agg.window_budget_usd_micros(0, _d(2026, 1, 1), _d(2026, 1, 10), _QUARTER) is None


def test_a_bare_sprint_or_custom_label_cannot_size_a_window():
    for label in ("sprint", "custom", "weekly"):
        with pytest.raises(BadRequestError):
            agg.window_budget_usd_micros(1_000_000, _d(2026, 1, 1), _d(2026, 1, 10), label)


def test_seat_allowance_in_a_full_period_window():
    seat = 310_000_000
    frm, to = _d(2026, 1, 1), _d(2026, 1, 20)
    # predates the period (or no known start): the whole period's seat budget
    assert agg.seat_allowance_usd_micros(seat, _d(2025, 6, 1), frm, to, _MONTH) == seat
    assert agg.seat_allowance_usd_micros(seat, None, frm, to, _MONTH) == seat
    # joined on the 11th: prorated to the period END (21 of 31 days), not to the day the window stops
    assert agg.seat_allowance_usd_micros(seat, _d(2026, 1, 11), frm, to, _MONTH) == 210_000_000
    assert agg.seat_allowance_usd_micros(seat, _d(2026, 1, 11), frm, _d(2026, 2, 1), _MONTH) == 210_000_000
    # joined on or after the window's end: no allowance
    assert agg.seat_allowance_usd_micros(seat, to, frm, to, _MONTH) is None
    assert agg.seat_allowance_usd_micros(seat, _d(2026, 1, 25), frm, to, _MONTH) is None


def test_seat_allowance_in_a_rolling_window_uses_the_period_containing_the_end():
    seat = 310_000_000
    frm, to = _d(2026, 1, 2), _d(2026, 1, 17)                        # 15 rolling days of January
    assert agg.seat_allowance_usd_micros(seat, None, frm, to, _MONTH) == 150_000_000
    assert agg.seat_allowance_usd_micros(seat, _d(2026, 1, 12), frm, to, _MONTH) == 50_000_000   # 5/31
    sprint_seat = 140_000_000
    assert agg.seat_allowance_usd_micros(sprint_seat, _d(2026, 1, 12), _d(2026, 1, 5), _d(2026, 1, 19),
                                         _SPRINT) == 70_000_000          # sprint to its end, 7/14


def test_seat_allowance_none_without_a_seat_budget_for_any_cycle():
    assert agg.seat_allowance_usd_micros(None, None, _d(2026, 1, 1), _d(2026, 1, 10), _SPRINT) is None
