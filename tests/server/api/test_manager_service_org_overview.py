"""A1 GET /v1/org/overview -- service-level (`server/api/manager/service.py::org_overview`).

Plan Verify: "teams-over-budget uses spend>budget."
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from server.api.config import ApiConfig
from server.api.manager import budget_cycles
from server.api.manager import budgets as budgets_service
from server.api.manager.service import org_overview
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scalars, session_scope
from server.storage.schema import GitArtifact
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev2"),), email="dev2@x.com",
                            teams=(TeamMembership("growth"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("growth", "default", "Growth")],
    )
    mid = datetime(2026, 1, 15, tzinfo=timezone.utc)

    def add_session(session_id, user_id, cost_usd_micros, tokens=1000):
        s.add(SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id=user_id,
                         agent="claude_code", status="ended", started_at=mid, last_event_at=mid,
                         signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id=session_id, created_at=mid, updated_at=mid, models={},
                          tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                          turn_count=0, long_turn_count=0, info={}, otel={}, datasource="both",
                          cost_effective_usd_micros=cost_usd_micros, tokens_total=tokens))

    add_session("s-dev1", "csv:dev1", 20_000_000)  # $20 -- over a $10 platform budget
    add_session("s-dev2", "csv:dev2", 5_000_000)   # $5 -- under a $1000 growth budget

    s.add(GitArtifact(artifact_id="pr-1", kind="pr", repo_id="github:acme/api", org_id="default",
                      user_id="csv:dev1", is_bot=False, state="merged", created_at=mid,
                      updated_at=mid, merged_at=mid))
    s.flush()

    budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("10"))
    budgets_service.put_budget(s, catalog, "team", "growth", "monthly", Decimal("1000"))


@requires_db
def test_teams_over_budget_uses_spend_greater_than_budget(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)

    data = result["data"]
    by_id = {t["team_id"]: t for t in data["teams"]}
    assert by_id["platform"]["over_budget"] is True     # $20 spend > $10 budget
    assert by_id["growth"]["over_budget"] is False       # $5 spend < $1000 budget
    assert data["highlights"]["teams_over_budget"] == 1
    assert data["headline"]["spend"]["usd_micros"] == 25_000_000


@requires_db
def test_headline_budget_pct_is_window_scaled(engine, catalog):
    """Cost-based seats plan §0.4: a ROLLING window's spend is compared with the budget scaled
    to that window by days over the month's actual length -- $25 over 15 days (2-17 Jan) of a
    $60/month org budget is 25 / (60 x 15/31), not 25/60. (A window that starts on the 1st is a
    period-to-date and gets the full budget: see the to-date tests below.)"""
    with session_scope() as s:
        _seed(s, catalog)
        budgets_service.put_budget(s, catalog, "org", "default", "monthly", Decimal("60"))
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM + timedelta(days=1),
                              FRM + timedelta(days=16))
    assert result["data"]["headline"]["budget_pct"] == 25_000_000 / round(60_000_000 * 15 / 31)


@requires_db
def test_the_same_spend_is_over_budget_only_on_the_shorter_window(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("30"))
    with session_scope() as s:
        month = org_overview(s, catalog, ApiConfig(), FRM, TO)    # 1-31 Jan: a period-to-date
        fortnight = org_overview(s, catalog, ApiConfig(), FRM + timedelta(days=1),
                                 FRM + timedelta(days=16))        # 2-17 Jan: rolling, 15 days

    def platform(result):
        return next(t for t in result["data"]["teams"] if t["team_id"] == "platform")

    assert platform(month)["over_budget"] is False       # $20 of the full $30
    assert platform(fortnight)["over_budget"] is True    # $20 of 30 x 15/31 = $14.52
    assert platform(fortnight)["budget_pct"] == 20_000_000 / round(30_000_000 * 15 / 31)


@requires_db
def test_org_overview_merged_prs_and_cost_per_pr(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)

    highlights = result["data"]["highlights"]
    assert highlights["merged_prs"] == 1
    assert highlights["cost_per_pr"]["usd_micros"] == 25_000_000  # blended: total spend / 1 PR


@requires_db
def test_reasoning_share_rolls_up_from_the_fold(engine, catalog):
    """F5: `org_overview()` wires `aggregate.reasoning_share` over the same `rows` its other
    highlights already use -- this just proves the wiring, not the aggregate's own math
    (covered directly in test_manager_aggregate.py)."""
    with session_scope() as s:
        _seed(s, catalog)
        mid = datetime(2026, 1, 15, tzinfo=timezone.utc)
        s.add(SessionRow(session_id="s-dev1-reasoning", producer_id="p", org_id="default",
                         user_id="csv:dev1", agent="claude_code", status="ended",
                         started_at=mid, last_event_at=mid, signal_blob={}, info_blob={},
                         subagents=[]))
        info = {"reasoning_tokens": 100}
        s.add(SessionInfo(session_id="s-dev1-reasoning", created_at=mid, updated_at=mid,
                          models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[],
                          turns=[], turn_count=0, long_turn_count=0,
                          info=info, otel={}, datasource="both", tokens_total=500,
                          **session_scalars.fold_scalars(info, {})))
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)

    assert result["data"]["highlights"]["reasoning_share"] == {
        "reasoning_tokens": 100, "tokens_total": 2_500, "pct": 4.0,
    }


@requires_db
def test_worth_a_look_flags_the_over_budget_team(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)

    findings = result["data"]["worth_a_look"]
    over_budget_findings = [f for f in findings
                            if f["entity"]["id"] == "platform" and f["severity"] == "red"]
    assert len(over_budget_findings) == 1
    assert "waste" not in over_budget_findings[0]["text"].lower()


@requires_db
def test_org_overview_is_empty_scope_safe(engine, catalog):
    """No directory at all -- an empty org still returns a well-shaped envelope, not a 500."""
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)
    assert result["data"]["teams"] == []
    assert result["data"]["headline"]["spend"]["usd_micros"] is None
    assert result["mode"]["modes_present"] == []


def _add_priced_session(s, session_id, user_id, agent, cost_effective, vendor=None, parent=None):
    mid = datetime(2026, 1, 15, tzinfo=timezone.utc)
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id=user_id,
                     agent=agent, status="ended", started_at=mid, last_event_at=mid,
                     parent_session_id=parent, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=session_id, created_at=mid, updated_at=mid, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info={}, otel={}, datasource="both",
                      cost_effective_usd_micros=cost_effective, otel_vendor_cost_usd_micros=vendor,
                      tokens_total=1))


@requires_db
def test_agent_spend_is_on_the_precedence_chain_and_sums_to_the_headline(engine, catalog):
    """B3: the donut's per-agent dollars resolve through the same chain as the headline, so the
    two agree even where the vendor-reported figure differs from `cost_effective_usd_micros`
    (the old raw per-agent sum disagreed there)."""
    with session_scope() as s:
        _seed(s, catalog)  # claude_code: $20 + $5, estimated rung (no vendor figure)
        _add_priced_session(s, "s-cc-vendor", "csv:dev1", "claude_code", 8_000_000, vendor=7_000_000)
        _add_priced_session(s, "s-codex", "csv:dev2", "codex", 3_000_000, vendor=2_500_000)
        _add_priced_session(s, "s-codex-sub", "csv:dev2", "codex", 1_000_000, parent="s-codex")
        _add_priced_session(s, "s-cursor", "csv:dev1", "cursor", 500_000)
    with session_scope() as s:
        data = org_overview(s, catalog, ApiConfig(), FRM, TO)["data"]

    agents = {a["agent"]: a["spend"] for a in data["agent_spend"]}
    assert {k: v["usd_micros"] for k, v in agents.items()} == {
        "claude_code": 32_000_000,   # 20 + 5 + vendor 7 (raw cost_effective would say 33)
        "codex": 3_500_000,          # vendor 2.5 + sub-agent 1 (raw would say 4)
        "cursor": 500_000,
    }
    assert agents["claude_code"]["cost_basis"] == "estimated,otel_vendor"
    assert agents["cursor"]["cost_basis"] == "estimated"
    total = sum(v["usd_micros"] for v in agents.values())
    assert total == data["headline"]["spend"]["usd_micros"] == 36_000_000
    # largest first
    assert [a["agent"] for a in data["agent_spend"]] == ["claude_code", "codex", "cursor"]


@requires_db
def test_agent_spend_is_empty_for_an_empty_scope(engine, catalog):
    with session_scope() as s:
        assert org_overview(s, catalog, ApiConfig(), FRM, TO)["data"]["agent_spend"] == []


@requires_db
def test_an_agent_whose_rows_resolve_to_nothing_is_still_listed_with_no_money(engine, catalog):
    """B3: an agent's rows with neither a vendor nor an effective cost resolve to nothing. The
    agent stays in the donut data with `Money(None)` (absent, not 0) and the others are
    unaffected -- the totals still reconcile."""
    with session_scope() as s:
        _seed(s, catalog)
        _add_priced_session(s, "s-unpriced", "csv:dev1", "cursor", None)
    with session_scope() as s:
        data = org_overview(s, catalog, ApiConfig(), FRM, TO)["data"]

    by_agent = {a["agent"]: a["spend"] for a in data["agent_spend"]}
    assert by_agent["cursor"]["usd_micros"] is None
    assert by_agent["claude_code"]["usd_micros"] == 25_000_000
    assert data["headline"]["spend"]["usd_micros"] == 25_000_000
    assert [a["agent"] for a in data["agent_spend"]] == ["claude_code", "cursor"]  # unpriced last


NOW_AFTER = datetime(2026, 3, 1, tzinfo=timezone.utc)


def _daily(data):
    return {p["day"].isoformat(): p["spend_usd_micros"] for p in data["daily_spend"]}


@requires_db
def test_daily_spend_sums_to_the_headline_on_mixed_cost_sources(engine, catalog):
    """B1: per-row chain resolution bucketed by day, so the series is the headline, exactly --
    including a vendor-priced row (chain differs from raw cost) and a session that began before
    the window."""
    with session_scope() as s:
        _seed(s, catalog)   # $20 + $5 on Jan 15
        _add_priced_session(s, "s-vendor", "csv:dev1", "claude_code", 8_000_000, vendor=7_000_000)
        s.add(SessionRow(session_id="s-early", producer_id="p", org_id="default", user_id="csv:dev2",
                         agent="codex", status="ended", started_at=datetime(2025, 12, 28, tzinfo=timezone.utc),
                         last_event_at=datetime(2026, 1, 2, tzinfo=timezone.utc), signal_blob={},
                         info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id="s-early", created_at=FRM, updated_at=FRM, models={}, tools={},
                          files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                          long_turn_count=0, info={}, otel={}, datasource="both",
                          cost_effective_usd_micros=2_000_000, tokens_total=1))
    with session_scope() as s:
        data = org_overview(s, catalog, ApiConfig(), FRM, TO, now=NOW_AFTER)["data"]

    daily = _daily(data)
    assert len(daily) == 30                                  # Jan 1 .. Jan 30 (to is exclusive)
    assert min(daily) == "2026-01-01" and max(daily) == "2026-01-30"
    assert daily["2026-01-01"] == 2_000_000                  # began Dec 28 -> window's first day
    assert daily["2026-01-15"] == 32_000_000                 # 20 + 5 + vendor 7
    assert sum(daily.values()) == data["headline"]["spend"]["usd_micros"] == 34_000_000
    assert sum(a["spend"]["usd_micros"] for a in data["agent_spend"]) == 34_000_000


@requires_db
def test_daily_spend_stops_at_now_and_still_reconciles(engine, catalog):
    """Only elapsed days are listed; sessions dated after `now` are clamped into the last one."""
    with session_scope() as s:
        _seed(s, catalog)   # both sessions started Jan 15
    with session_scope() as s:
        data = org_overview(s, catalog, ApiConfig(), FRM, TO,
                            now=datetime(2026, 1, 10, 9, tzinfo=timezone.utc))["data"]
    daily = _daily(data)
    assert max(daily) == "2026-01-10" and len(daily) == 10
    assert daily["2026-01-10"] == 25_000_000                 # clamped from the 15th
    assert sum(daily.values()) == data["headline"]["spend"]["usd_micros"]


@requires_db
def test_daily_spend_is_zero_filled_for_an_empty_scope(engine, catalog):
    with session_scope() as s:
        data = org_overview(s, catalog, ApiConfig(), FRM, FRM + timedelta(days=3), now=NOW_AFTER)["data"]
    assert [p["spend_usd_micros"] for p in data["daily_spend"]] == [0, 0, 0]
    assert data["headline"]["spend"]["usd_micros"] is None


def _add_typed_session(s, session_id, user_id, agent, label, parent=None):
    mid = datetime(2026, 1, 15, tzinfo=timezone.utc)
    info = {"segments": [{"label": label, "turns": 3}]} if label else {}
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id=user_id,
                     agent=agent, status="ended", started_at=mid, last_event_at=mid,
                     parent_session_id=parent, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id=session_id, created_at=mid, updated_at=mid, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, info=info, otel={}, datasource="both",
                      cost_effective_usd_micros=1, tokens_total=1,
                      **session_scalars.fold_scalars(info, {})))


@requires_db
def test_agent_task_mix_matches_the_usage_breakdown_per_type(engine, catalog):
    """B2: same rows and rules as `session_types` on Usage -- summing the mix per type is that
    breakdown. Sub-agents and sessions with no type are left out."""
    from server.api.manager.service import org_usage
    with session_scope() as s:
        _seed(s, catalog)   # two untyped claude_code sessions: omitted
        _add_typed_session(s, "t1", "csv:dev1", "claude_code", "IMPLEMENT")
        _add_typed_session(s, "t2", "csv:dev1", "claude_code", "IMPLEMENT")
        _add_typed_session(s, "t3", "csv:dev2", "codex", "IMPLEMENT")
        _add_typed_session(s, "t4", "csv:dev2", "codex", "BUG_FIX")
        _add_typed_session(s, "t5", "csv:dev2", "codex", "BUG_FIX", parent="t4")   # sub-agent
    with session_scope() as s:
        mix = org_overview(s, catalog, ApiConfig(), FRM, TO)["data"]["agent_task_mix"]
        usage_types = org_usage(s, catalog, ApiConfig(), FRM, TO)["data"]["session_types"]

    assert mix == [
        {"agent": "claude_code", "type": "IMPLEMENT", "count": 2},
        {"agent": "codex", "type": "BUG_FIX", "count": 1},
        {"agent": "codex", "type": "IMPLEMENT", "count": 1},
    ]
    per_type: dict[str, int] = {}
    for p in mix:
        per_type[p["type"]] = per_type.get(p["type"], 0) + p["count"]
    assert per_type == {t["type"]: t["count"] for t in usage_types}


@requires_db
def test_agent_task_mix_is_empty_with_no_typed_sessions(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        assert org_overview(s, catalog, ApiConfig(), FRM, TO)["data"]["agent_task_mix"] == []


def _org_budget(s, catalog, label, amount):
    budgets_service.put_budget(s, catalog, "org", "default", label, Decimal(amount))


def _platform(result):
    return next(t for t in result["data"]["teams"] if t["team_id"] == "platform")


@requires_db
def test_whole_period_and_period_to_date_windows_compare_with_the_full_budget(engine, catalog):
    """Plan §0: a window that starts on a period start and ends inside (or at the end of) it is
    measured against the FULL period budget -- early in a period few teams read as over."""
    with session_scope() as s:
        _seed(s, catalog)
        _org_budget(s, catalog, "monthly", "60")
        budgets_service.put_budget(s, catalog, "team", "platform", "monthly", Decimal("30"))
    feb1 = datetime(2026, 2, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        to_date = org_overview(s, catalog, ApiConfig(), FRM, FRM + timedelta(days=15))
        whole = org_overview(s, catalog, ApiConfig(), FRM, feb1)
        rolling = org_overview(s, catalog, ApiConfig(), FRM + timedelta(days=1), FRM + timedelta(days=16))

    for result in (to_date, whole):
        assert result["data"]["headline"]["budget_pct"] == 25 / 60
        assert _platform(result)["budget_pct"] == 20 / 30
        assert _platform(result)["over_budget"] is False
        assert result["data"]["highlights"]["teams_over_budget"] == 0
    assert _platform(rolling)["over_budget"] is True          # same spend, a prorated $14.52
    assert rolling["data"]["highlights"]["teams_over_budget"] == 1


@requires_db
def test_a_window_ending_at_midnight_on_the_first_is_measured_in_the_month_it_ended_in(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        _org_budget(s, catalog, "monthly", "60")
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM + timedelta(days=1),
                              datetime(2026, 2, 1, tzinfo=timezone.utc))      # 2 Jan - 31 Jan
    assert result["data"]["headline"]["budget_pct"] == 25_000_000 / round(60_000_000 * 30 / 31)


@requires_db
def test_no_budget_cycle_row_is_the_monthly_default(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        _org_budget(s, catalog, "monthly", "60")
        assert budget_cycles.load_cycle(s, catalog, "default") is None
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)
    assert result["data"]["headline"]["budget_pct"] == 25 / 60


@requires_db
def test_a_quarterly_cycle_reads_quarterly_budgets_and_ignores_monthly_ones(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        _org_budget(s, catalog, "monthly", "60")                  # stored, but not the cadence now
        budget_cycles.put_cycle(s, catalog, "default", budget_cycles.validate("quarter"))
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)
    assert result["data"]["headline"]["budget_pct"] is None       # "no budget" until entered
    assert _platform(result)["budget_pct"] is None
    with session_scope() as s:
        _org_budget(s, catalog, "quarterly", "180")
        budgets_service.put_budget(s, catalog, "team", "platform", "quarterly", Decimal("30"))
    with session_scope() as s:
        to_date = org_overview(s, catalog, ApiConfig(), FRM, TO)                       # 1-31 Jan of Q1
        rolling = org_overview(s, catalog, ApiConfig(), FRM + timedelta(days=1),
                               FRM + timedelta(days=16))                               # 15 of 90 days
    assert to_date["data"]["headline"]["budget_pct"] == 25 / 180
    assert _platform(to_date)["budget_pct"] == 20 / 30
    assert rolling["data"]["headline"]["budget_pct"] == 25_000_000 / round(180_000_000 * 15 / 90)


@requires_db
def test_a_sprint_cycle_measures_against_the_sprint_budget(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        budget_cycles.put_cycle(s, catalog, "default", budget_cycles.validate(
            "sprint", sprint_weeks=2, anchor_date="2026-01-05", anchor_number=10))   # 5-19 Jan
        _org_budget(s, catalog, "sprint", "50")
    with session_scope() as s:
        to_date = org_overview(s, catalog, ApiConfig(), datetime(2026, 1, 5, tzinfo=timezone.utc),
                               datetime(2026, 1, 16, tzinfo=timezone.utc))
        rolling = org_overview(s, catalog, ApiConfig(), datetime(2026, 1, 6, tzinfo=timezone.utc),
                               datetime(2026, 1, 16, tzinfo=timezone.utc))             # 10 of 14 days
    assert to_date["data"]["headline"]["budget_pct"] == 25 / 50
    assert rolling["data"]["headline"]["budget_pct"] == 25_000_000 / round(50_000_000 * 10 / 14)


@requires_db
def test_a_corrupt_saved_cycle_falls_back_to_the_monthly_default(engine, catalog):
    from sqlalchemy import text
    with session_scope() as s:
        _seed(s, catalog)
        _org_budget(s, catalog, "monthly", "60")
        s.execute(text("INSERT INTO budget_cycle (org_id, kind, recurrence, updated_at) "
                       "VALUES ('default', 'sprint', 'recurring', now())"))   # sprint, no anchor
    with session_scope() as s:
        result = org_overview(s, catalog, ApiConfig(), FRM, TO)
    assert result["data"]["headline"]["budget_pct"] == 25 / 60
