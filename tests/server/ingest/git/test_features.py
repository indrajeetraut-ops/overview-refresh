"""Feature grouping helper (ISSUE §3, plan H5): an issue + its linked PRs + the sessions that
fed them compose to a feature whose cost = Σ de-duplicated member-session spend; an issue with
no PR is still a present, empty feature. DB-backed; skips without `TALIUM_TEST_DSN`."""
from __future__ import annotations

from sqlalchemy import text

from server.ingest.git.apply import apply_map_result
from server.ingest.git.features import feature_of, single_pr_feature
from server.ingest.git.issue_ref import IssueRef
from server.ingest.git.map_github import map_delivery
from server.storage.engine import session_scope
from server.storage.schema import Base
from tests.server.storage.conftest import requires_db

ORG_ID = "org_test"
REPO_NUM = 41213765
PK = str(REPO_NUM)


def _issue_payload(number, state="open", closed_at=None):
    return {"issue": {
        "number": number, "title": f"Feature {number}", "state": state,
        "user": {"login": "akhuteta"}, "labels": [{"name": "auth"}], "milestone": {"title": "S1"},
        "assignees": [], "comments": 0,
        "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z",
        "closed_at": closed_at,
    }, "repository": {"id": REPO_NUM}}


def _pr_payload(number, *, body, state="open", merged=False, merged_at=None):
    return {"pull_request": {
        "number": number, "title": f"PR {number}", "body": body, "state": state, "merged": merged,
        "user": {"login": "akhuteta"}, "head": {"ref": f"b{number}", "sha": f"{number:040x}"},
        "base": {"ref": "main"},
        "created_at": "2026-09-02T00:00:00Z", "updated_at": "2026-09-03T00:00:00Z",
        "closed_at": merged_at, "merged_at": merged_at, "labels": [],
    }, "repository": {"id": REPO_NUM}}


def _link(s, pr_number, issue_number, method="graphql", confidence=1.0):
    s.execute(text(
        "INSERT INTO pr_issue_links (pr_artifact_id, tracker, project_key, number, "
        "issue_artifact_id, method, confidence, created_at) VALUES "
        "(:pr, 'github', :pk, :num, :ia, :m, :c, now()) ON CONFLICT DO NOTHING"
    ), {"pr": f"github:{REPO_NUM}:pr:{pr_number}", "pk": PK, "num": issue_number,
        "ia": f"github:{REPO_NUM}:issue:{issue_number}", "m": method, "c": confidence})


def _session_cost(s, session_id, usd):
    s.execute(text(
        "INSERT INTO session_info (session_id, cost_effective_usd_micros, datasource, models, "
        "tools, files, skills, subagents, pr_list, turns, turn_count, long_turn_count, "
        "tokens_total, compaction_count, subagent_count, error_count, info, otel, "
        "created_at, updated_at) VALUES "
        "(:sid, :micros, 'jsonl_only', '{}', '{}', '{}', '[]', '{}', '[]', '[]', 0, 0, 0, 0, 0, "
        "0, '{}', '{}', now(), now())"
    ), {"sid": session_id, "micros": round(usd * 1_000_000)})


def _session_git_link(s, session_id, pr_number):
    s.execute(text(
        "INSERT INTO session_git_links (session_id, artifact_id, method, confidence, signals, "
        "created_at) VALUES (:sid, :aid, 'sha', 1.0, '{}', now()) ON CONFLICT DO NOTHING"
    ), {"sid": session_id, "aid": f"github:{REPO_NUM}:pr:{pr_number}"})


@requires_db
def test_issue_with_three_prs_composes_to_a_feature_with_deduped_session_cost(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery(
            "issues", "closed", _issue_payload(400, "closed", "2026-09-05T00:00:00Z"), ORG_ID))
        for n in (501, 502, 503):
            apply_map_result(s, catalog, map_delivery("pull_request", "opened",
                _pr_payload(n, body="", state="merged", merged=True,
                            merged_at="2026-09-04T00:00:00Z"), ORG_ID))
            _link(s, n, 400)
        # s1 feeds 501; s2 feeds BOTH 502 and 503 -> must count once. costs: s1=10, s2=20.
        _session_cost(s, "s1", 10.0)
        _session_cost(s, "s2", 20.0)
        _session_git_link(s, "s1", 501)
        _session_git_link(s, "s2", 502)
        _session_git_link(s, "s2", 503)

    with session_scope() as s:
        feat = feature_of(s, catalog, IssueRef("github", PK, 400, f"github:{REPO_NUM}:issue:400"))

    assert feat.issue["title"] == "Feature 400"
    assert feat.status == "shipped"
    assert feat.member_pr_ids == [f"github:{REPO_NUM}:pr:{n}" for n in (501, 502, 503)]
    assert feat.session_ids == ["s1", "s2"]
    assert feat.min_confidence == 1.0
    assert feat.cost_usd_micros == 30_000_000   # s2 counted once, not twice


@requires_db
def test_issue_with_no_pr_is_an_empty_but_present_feature(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("issues", "opened", _issue_payload(401), ORG_ID))

    with session_scope() as s:
        feat = feature_of(s, catalog, IssueRef("github", PK, 401, f"github:{REPO_NUM}:issue:401"))

    assert feat.issue["number"] == 401
    assert feat.status == "not_started"
    assert feat.member_pr_ids == [] and feat.session_ids == []
    assert feat.cost_usd_micros == 0
    assert feat.min_confidence is None


@requires_db
def test_referenced_but_not_pulled_issue_still_yields_a_feature_keyed_by_ref(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("pull_request", "opened",
            _pr_payload(600, body="Closes #402"), ORG_ID))   # issue #402 never ingested

    with session_scope() as s:
        feat = feature_of(s, catalog, IssueRef("github", PK, 402, f"github:{REPO_NUM}:issue:402"))

    assert feat.issue is None                       # not pulled
    assert feat.member_pr_ids == [f"github:{REPO_NUM}:pr:600"]
    assert feat.status == "in_progress"
    assert feat.min_confidence == 0.5               # body rung, carried not flattened


@requires_db
def test_single_pr_feature_fallback_for_a_pr_with_no_issue_link(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        apply_map_result(s, catalog, map_delivery("pull_request", "opened",
            _pr_payload(700, body="no refs here", state="merged", merged=True,
                        merged_at="2026-09-04T00:00:00Z"), ORG_ID))
        _session_cost(s, "s9", 4.0)
        _session_git_link(s, "s9", 700)

    with session_scope() as s:
        feat = single_pr_feature(s, catalog, f"github:{REPO_NUM}:pr:700")

    assert feat.member_pr_ids == [f"github:{REPO_NUM}:pr:700"]
    assert feat.issue is None and feat.links == []
    assert feat.status == "shipped"
    assert feat.cost_usd_micros == 4_000_000
