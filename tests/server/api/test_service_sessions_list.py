from datetime import datetime, timedelta, timezone

import pytest

from server.api.single_user import service
from server.api.config import ApiConfig
from server.api.errors import BadRequestError
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed(s, session_id, datasource, *, started_at, agent="claude_code",
          cost_effective_usd_micros=0, tokens_total=0, compaction_count=0, subagent_count=0,
          error_count=0, info=None, ended_at=None, parent_session_id=None, status="live",
          repo_id=None, repo_id_provenance=None):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent=agent, status=status, started_at=started_at,
                     last_event_at=started_at, ended_at=ended_at, signal_blob={}, info_blob={},
                     subagents=[], parent_session_id=parent_session_id))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
        tokens_total=tokens_total, compaction_count=compaction_count,
        subagent_count=subagent_count, error_count=error_count, info=info or {}, otel={},
        cost_effective_usd_micros=cost_effective_usd_micros, datasource=datasource,
        repo_id=repo_id, repo_id_provenance=repo_id_provenance,
    ))


def _list(s, catalog, **kwargs):
    return service.sessions_list(s, catalog, ApiConfig(), USER,
                                 NOW - timedelta(days=7), NOW + timedelta(days=1), **kwargs)


def test_unknown_sort_is_rejected(engine, catalog):
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            _list(s, catalog, sort="not_a_real_sort")


def test_sort_by_recent_is_the_default(engine, catalog):
    with session_scope() as s:
        _seed(s, "old", "jsonl_only", started_at=NOW - timedelta(days=3))
        _seed(s, "new", "jsonl_only", started_at=NOW - timedelta(days=1))

    with session_scope() as s:
        result = _list(s, catalog)
    assert [r["session_id"] for r in result["sessions"]] == ["new", "old"]


def test_sort_by_cost(engine, catalog):
    with session_scope() as s:
        _seed(s, "cheap", "both", started_at=NOW - timedelta(days=1), cost_effective_usd_micros=100)
        _seed(s, "pricey", "both", started_at=NOW - timedelta(days=2), cost_effective_usd_micros=9_000_000)

    with session_scope() as s:
        result = _list(s, catalog, sort="cost")
    assert [r["session_id"] for r in result["sessions"]] == ["pricey", "cheap"]


def test_repo_id_filter_is_pushed_into_sql_not_applied_after_limit(engine, catalog):
    with session_scope() as s:
        _seed(s, "in-repo-a", "jsonl_only", started_at=NOW - timedelta(days=1),
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote")
        _seed(s, "in-repo-b", "jsonl_only", started_at=NOW - timedelta(days=2),
             repo_id="local:other-repo", repo_id_provenance="path")

    with session_scope() as s:
        result = _list(s, catalog, repo_id="github:talium-ai/code-econ")
    assert [r["session_id"] for r in result["sessions"]] == ["in-repo-a"]


def test_repo_id_filter_absent_returns_every_repo(engine, catalog):
    with session_scope() as s:
        _seed(s, "in-repo-a", "jsonl_only", started_at=NOW - timedelta(days=1),
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote")
        _seed(s, "in-repo-b", "jsonl_only", started_at=NOW - timedelta(days=2),
             repo_id="local:other-repo", repo_id_provenance="path")

    with session_scope() as s:
        result = _list(s, catalog)
    assert {r["session_id"] for r in result["sessions"]} == {"in-repo-a", "in-repo-b"}


def test_otel_only_row_is_reduced_but_keeps_cost_and_mode(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", started_at=NOW - timedelta(days=1),
             cost_effective_usd_micros=3_000_000, tokens_total=500,
             info={"title": "should never surface", "story": {"curve": [[0, 1, 1]], "shape": "cliff"}})

    with session_scope() as s:
        result = _list(s, catalog)
    row = result["sessions"][0]
    assert row["datasource"] == "otel_only"
    assert row["vendor"] == "claude_code"
    assert row["cost_usd_micros"] == 3_000_000
    assert row["tokens_total"] == 500
    assert row["glyph"] is None
    assert row["title"] is None
    assert row["repo"] is None
    assert row["badges"] == []


def test_jsonl_capable_row_carries_glyph_title_repo_and_badges(engine, catalog):
    with session_scope() as s:
        _seed(s, "both-1", "both", started_at=NOW - timedelta(hours=2),
             cost_effective_usd_micros=6_000_000,   # over the 5.0 default costly floor
             compaction_count=3,                     # over the default of 2
             info={"title": "Fix the payments bug",
                  "story": {"curve": [[0, 10, 10], [60, 100, 90]], "shape": "steady_climb"},
                  "repo": {"repo_key": "payments-svc"}})

    with session_scope() as s:
        result = _list(s, catalog)
    row = result["sessions"][0]
    assert row["glyph"] == {"curve": [[0, 10, 10], [60, 100, 90]], "shape": "steady_climb"}
    assert row["title"] == "Fix the payments bug"
    assert row["repo"] == "payments-svc"
    assert set(row["badges"]) == {"costly", "compaction_heavy"}


def test_flags_filter_narrows_the_page(engine, catalog):
    with session_scope() as s:
        _seed(s, "flagged", "both", started_at=NOW - timedelta(hours=1),
             cost_effective_usd_micros=6_000_000, info={})
        _seed(s, "plain", "both", started_at=NOW - timedelta(hours=2),
             cost_effective_usd_micros=100, info={})

    with session_scope() as s:
        result = _list(s, catalog, flags=["costly"])
    assert [r["session_id"] for r in result["sessions"]] == ["flagged"]


def test_vendor_filter(engine, catalog):
    with session_scope() as s:
        _seed(s, "cc", "both", started_at=NOW - timedelta(hours=1), agent="claude_code")
        _seed(s, "other", "both", started_at=NOW - timedelta(hours=2), agent="codex")

    with session_scope() as s:
        result = _list(s, catalog, vendor="claude_code")
    assert [r["session_id"] for r in result["sessions"]] == ["cc"]


def test_pre_tick_row_is_jsonl_capable_not_reduced(engine, catalog):
    """`datasource is None` (JSONL-folded, true-up hasn't ticked yet) must not be treated like
    `otel_only` -- its glyph/title/repo/badges are real, not structurally absent."""
    with session_scope() as s:
        _seed(s, "pre-tick", None, started_at=NOW - timedelta(hours=1),
             cost_effective_usd_micros=None,
             info={"title": "Real title", "story": {"curve": [[0, 1, 1]], "shape": "short_spark"},
                  "repo": {"repo_key": "myrepo"}})

    with session_scope() as s:
        result = _list(s, catalog)
    row = result["sessions"][0]
    assert row["title"] == "Real title"
    assert row["repo"] == "myrepo"
    assert row["glyph"] == {"curve": [[0, 1, 1]], "shape": "short_spark"}


def test_empty_otel_only_stub_is_excluded_from_the_list(engine, catalog):
    """A stub whose only OTel signal is an unmapped metric (e.g. `claude_code.session.count`)
    never resolves a real cost/tokens and would otherwise render as a permanently empty
    "(untitled session)" row -- filtered out entirely rather than shown reduced."""
    with session_scope() as s:
        _seed(s, "husk", None, started_at=NOW - timedelta(hours=1),
             cost_effective_usd_micros=0, tokens_total=0, status="otel_only")
        _seed(s, "real", "jsonl_only", started_at=NOW - timedelta(hours=2),
             cost_effective_usd_micros=100, tokens_total=10)

    with session_scope() as s:
        result = _list(s, catalog)
    assert [r["session_id"] for r in result["sessions"]] == ["real"]


def test_otel_only_stub_with_real_cost_is_not_excluded(engine, catalog):
    """A metrics-only fleet (`OTEL_LOGS_EXPORTER=none`) has real `otel_only` sessions with
    genuine cost from mapped measures and zero request-ledger rows by design -- must not be
    swept up by the empty-husk filter just because it shares the `otel_only` status."""
    with session_scope() as s:
        _seed(s, "metrics-only", "otel_only", started_at=NOW - timedelta(hours=1),
             cost_effective_usd_micros=500_000, tokens_total=0, status="otel_only")

    with session_scope() as s:
        result = _list(s, catalog)
    assert [r["session_id"] for r in result["sessions"]] == ["metrics-only"]


def test_subagent_sessions_excluded_from_every_sort(engine, catalog):
    """A Task-tool sub-agent is a full `sessions` row sharing its parent's `user_id`
    (fold_handler.py:57) but must not clutter the list a user browses -- only
    `sessions_in_range`/Z1/the heatmap are root+child inclusive, by design."""
    with session_scope() as s:
        _seed(s, "parent", "both", started_at=NOW - timedelta(hours=1),
             cost_effective_usd_micros=100, tokens_total=10, compaction_count=5,
             subagent_count=2, error_count=1)
        _seed(s, "child-1", "both", started_at=NOW - timedelta(hours=1, minutes=30),
             cost_effective_usd_micros=9_000_000, tokens_total=9999, compaction_count=9,
             subagent_count=9, error_count=9, parent_session_id="parent")
        _seed(s, "child-2", "both", started_at=NOW - timedelta(hours=1, minutes=45),
             cost_effective_usd_micros=9_000_000, tokens_total=9999, compaction_count=9,
             subagent_count=9, error_count=9, parent_session_id="parent")

    for sort in ("recent", "cost", "tokens", "duration", "compactions", "subagents", "errors"):
        with session_scope() as s:
            result = _list(s, catalog, sort=sort)
        assert [r["session_id"] for r in result["sessions"]] == ["parent"], (
            f"sort={sort!r} leaked a sub-agent row into the list")


def test_has_more_flag(engine, catalog):
    with session_scope() as s:
        for i in range(3):
            _seed(s, f"s{i}", "both", started_at=NOW - timedelta(hours=i))

    with session_scope() as s:
        result = _list(s, catalog, limit=2)
    assert len(result["sessions"]) == 2
    assert result["has_more"] is True

    with session_scope() as s:
        result = _list(s, catalog, limit=10)
    assert len(result["sessions"]) == 3
    assert result["has_more"] is False
