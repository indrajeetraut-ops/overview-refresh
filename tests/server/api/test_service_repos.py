from datetime import datetime, timedelta, timezone

from server.api.single_user import service
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed(s, session_id, datasource, *, started_at=None, cost_effective_usd_micros=0,
          tokens_total=0, files=None, repo_id=None, repo_id_provenance=None,
          parent_session_id=None):
    started_at = started_at or NOW
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent="claude_code", status="live", started_at=started_at,
                     last_event_at=started_at, signal_blob={}, info_blob={}, subagents=[],
                     parent_session_id=parent_session_id))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models={}, tools={}, files=files or {}, skills=[], subagents={}, pr_list=[], turns=[],
        tokens_total=tokens_total, info={}, otel={},
        cost_effective_usd_micros=cost_effective_usd_micros, datasource=datasource,
        repo_id=repo_id, repo_id_provenance=repo_id_provenance,
    ))


def test_repos_grouped_by_repo_id_with_cost_tokens_files_lines(engine, catalog):
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", cost_effective_usd_micros=1_000_000, tokens_total=100,
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote",
             files={"a.py": {"created": True, "lines_added": 10, "lines_removed": 0},
                   "b.py": {"created": False, "lines_added": 2, "lines_removed": 1}})
        _seed(s, "s2", "jsonl_only", cost_effective_usd_micros=500_000, tokens_total=50,
             repo_id="github:talium-ai/code-econ", repo_id_provenance="remote",
             files={"c.py": {"created": True, "lines_added": 5, "lines_removed": 0}})

    with session_scope() as s:
        result = service.repos(s, catalog, USER, NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["repos"] == [{
        "repo_id": "github:talium-ai/code-econ", "kind": "git_remote", "label": "talium-ai/code-econ",
        "sessions": 2, "cost_usd_micros": 1_500_000, "tokens_total": 150,
        "files_created": 2, "files_edited": 1, "lines_added": 17, "lines_removed": 1,
    }]


def test_repos_ranked_by_cost_descending(engine, catalog):
    with session_scope() as s:
        _seed(s, "cheap", "jsonl_only", cost_effective_usd_micros=100,
             repo_id="local:cheap-repo", repo_id_provenance="path")
        _seed(s, "pricey", "jsonl_only", cost_effective_usd_micros=9_000_000,
             repo_id="local:pricey-repo", repo_id_provenance="path")

    with session_scope() as s:
        result = service.repos(s, catalog, USER, NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert [r["repo_id"] for r in result["repos"]] == ["local:pricey-repo", "local:cheap-repo"]


def test_repos_sessions_count_excludes_subagent_fanout(engine, catalog):
    """FEAS §4/R0: a fan-out session's sub-agents share its repo and count as one session,
    same asymmetry as _z1_headline -- but their cost still contributes to the total."""
    with session_scope() as s:
        _seed(s, "parent", "jsonl_only", cost_effective_usd_micros=100_000,
             repo_id="local:myrepo", repo_id_provenance="path")
        _seed(s, "child", "jsonl_only", cost_effective_usd_micros=50_000,
             repo_id="local:myrepo", repo_id_provenance="path", parent_session_id="parent")

    with session_scope() as s:
        result = service.repos(s, catalog, USER, NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["repos"] == [{
        "repo_id": "local:myrepo", "kind": "local_path", "label": "myrepo",
        "sessions": 1, "cost_usd_micros": 150_000, "tokens_total": 0,
        "files_created": 0, "files_edited": 0, "lines_added": 0, "lines_removed": 0,
    }]


def test_repos_excludes_sessions_with_no_resolved_repo_id(engine, catalog):
    with session_scope() as s:
        _seed(s, "s1", "jsonl_only", cost_effective_usd_micros=100_000)  # repo_id left None

    with session_scope() as s:
        result = service.repos(s, catalog, USER, NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["repos"] == []


def test_repos_unavailable_for_an_otel_only_only_range(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=100_000)

    with session_scope() as s:
        result = service.repos(s, catalog, USER, NOW - timedelta(days=1), NOW + timedelta(days=1))

    assert result["repos"] == {"unavailable": {"block": "repos", "requires": "jsonl"}}
