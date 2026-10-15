"""P6: GitHub artifacts, events, links, rework (plan §5 P6)."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from forecast.synthetic.org.git import _ARTIFACT_KEYS, _LINK_CONFIDENCE, compute_git

FIXTURES = Path("tests/fixtures/github")
ORG_ID = "org_test"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture
def git_result(resolved, usage_records):
    from forecast.synthetic.org.sessions import plan_sessions
    from forecast.synthetic.org.work import compute_work

    plans = plan_sessions(resolved, usage_records)
    work = compute_work(resolved, plans)
    return resolved, plans, work, compute_git(resolved, plans, work)


# --------------------------------------------------------------------------------------------
# Key-set parity with the real mapper (D12)
# --------------------------------------------------------------------------------------------

def test_artifact_keyset_matches_mapper():
    from server.ingest.git.map_github import map_delivery

    cases = [
        ("pull_request", "pull_request_closed_merged"),
        ("pull_request_review", "pull_request_review_submitted"),
        ("push", "push_squash_merge"),
        ("issues", "issues_opened"),
    ]
    for event, fixture_name in cases:
        fixture = _load_fixture(fixture_name)
        result = map_delivery(event, fixture.get("action"), fixture["payload"], ORG_ID)
        assert result.mapped and result.artifacts, fixture_name
        mapper_keys = set(result.artifacts[0].keys())
        assert mapper_keys == set(_ARTIFACT_KEYS), (fixture_name, mapper_keys ^ set(_ARTIFACT_KEYS))


def test_link_confidence_parity():
    from server.ingest.git.linkage import _CONFIDENCE

    for method, confidence in _LINK_CONFIDENCE.items():
        assert _CONFIDENCE[method] == confidence


# --------------------------------------------------------------------------------------------
# Numbering
# --------------------------------------------------------------------------------------------

def test_numbering_unique_per_repo_chronological(git_result):
    resolved, plans, work, result = git_result
    by_repo: dict[str, list[dict]] = {}
    for a in result.artifacts:
        if a["kind"] in ("pr", "issue"):
            by_repo.setdefault(a["repo_id"], []).append(a)
    for repo_id, artifacts in by_repo.items():
        numbers = sorted(a["number"] for a in artifacts)
        assert numbers == list(range(1, len(artifacts) + 1)), repo_id
        ordered = sorted(artifacts, key=lambda a: a["number"])
        created = [a["created_at"] for a in ordered]
        assert created == sorted(created), repo_id


# --------------------------------------------------------------------------------------------
# Temporal order
# --------------------------------------------------------------------------------------------

def test_temporal_order_per_pr(git_result):
    resolved, plans, work, result = git_result
    plans_by_id = {p.session_id: p for p in plans}
    prs = {a["artifact_id"]: a for a in result.artifacts
          if a["kind"] == "pr" and not a["info"].get("github.is_revert")}
    reviews_by_pr: dict[str, list[dict]] = {}
    for a in result.artifacts:
        if a["kind"] == "review":
            reviews_by_pr.setdefault(a["parent_artifact_id"], []).append(a)
    commits_by_pr: dict[str, list[dict]] = {}
    for a in result.artifacts:
        if a["kind"] == "commit" and a["parent_artifact_id"] in prs:
            commits_by_pr.setdefault(a["parent_artifact_id"], []).append(a)

    for item in work.items.values():
        if not item.has_pr:
            continue
        pr = next((a for a in prs.values()
                  if a["artifact_id"].endswith(f":pr:{item.item_id}")), None)
        if pr is None:
            continue
        created = datetime.fromisoformat(pr["created_at"])
        reviews = sorted(reviews_by_pr.get(pr["artifact_id"], []), key=lambda r: r["created_at"])
        commits = commits_by_pr.get(pr["artifact_id"], [])
        if commits and reviews:
            first_commit = min(datetime.fromisoformat(c["created_at"]) for c in commits)
            assert first_commit <= datetime.fromisoformat(reviews[0]["created_at"])
        for review in reviews:
            rt = datetime.fromisoformat(review["created_at"])
            assert created <= rt
            end = pr.get("merged_at") or pr.get("closed_at")
            if end:
                assert rt <= datetime.fromisoformat(end)
        first_session = plans_by_id[item.sessions[0]]
        assert first_session.started_at < created


def test_events_source_key_unique(git_result):
    resolved, plans, work, result = git_result
    keys = [e["source_key"] for e in result.events]
    assert len(keys) == len(set(keys))
    for event in result.events:
        assert event["occurred_at"] is not None


def test_events_monotonic_per_artifact(git_result):
    resolved, plans, work, result = git_result
    by_artifact: dict[str, list[dict]] = {}
    for e in result.events:
        by_artifact.setdefault(e["artifact_id"], []).append(e)
    for artifact_id, events in by_artifact.items():
        times = [e["occurred_at"] for e in events]
        assert times == sorted(times), artifact_id


# --------------------------------------------------------------------------------------------
# Reviewers
# --------------------------------------------------------------------------------------------

def test_reviewers_not_author(git_result):
    resolved, plans, work, result = git_result
    prs_by_id = {a["artifact_id"]: a for a in result.artifacts if a["kind"] == "pr"}
    checked = 0
    for review in result.artifacts:
        if review["kind"] != "review":
            continue
        pr = prs_by_id.get(review["parent_artifact_id"])
        if pr is None:
            continue
        assert review["author_login"] != pr["author_login"], review
        checked += 1
    assert checked > 0


# --------------------------------------------------------------------------------------------
# Reverts
# --------------------------------------------------------------------------------------------

def test_revert_references_merge_sha(four_team_git):
    resolved, plans, work, result = four_team_git
    by_id = {a["artifact_id"]: a for a in result.artifacts if a["kind"] == "pr"}
    reverts = [a for a in result.artifacts
              if a["kind"] == "pr" and a["info"].get("github.is_revert")]
    assert reverts, "expected at least one revert in the 4-team/90-day scenario"
    for revert in reverts:
        assert revert["reverts_sha"] is not None
        orig = next((a for a in by_id.values() if a["merge_commit_sha"] == revert["reverts_sha"]), None)
        assert orig is not None
        assert orig["state"] == "merged"


# --------------------------------------------------------------------------------------------
# Links
# --------------------------------------------------------------------------------------------

def test_links_only_to_existing_prs(git_result):
    resolved, plans, work, result = git_result
    pr_ids = {a["artifact_id"] for a in result.artifacts if a["kind"] == "pr"}
    issue_ids = {a["artifact_id"] for a in result.artifacts if a["kind"] == "issue"}
    assert result.pr_issue_links
    for link in result.pr_issue_links:
        assert link["pr_artifact_id"] in pr_ids
        assert link["issue_artifact_id"] in issue_ids
        assert link["method"] in _LINK_CONFIDENCE
        assert link["confidence"] == _LINK_CONFIDENCE[link["method"]]


# --------------------------------------------------------------------------------------------
# session_git_links
# --------------------------------------------------------------------------------------------

def test_children_linked_like_parent(four_team_git):
    resolved, plans, work, result = four_team_git
    children_by_parent: dict[str, list] = {}
    for p in plans:
        if p.parent_session_id:
            children_by_parent.setdefault(p.parent_session_id, []).append(p)
    by_session: dict[str, list] = {}
    for link in result.session_links:
        by_session.setdefault(link["session_id"], []).append(link)

    checked = 0
    for parent_id, children in children_by_parent.items():
        parent_links = by_session.get(parent_id)
        if not parent_links:
            continue
        for child in children:
            child_links = by_session.get(child.session_id)
            if not child_links:
                continue
            assert child_links[0]["method"] == parent_links[0]["method"]
            assert child_links[0]["confidence"] == parent_links[0]["confidence"]
            assert child_links[0]["signals"] == parent_links[0]["signals"]
            checked += 1
    assert checked > 0


def test_session_links_sha_method_has_real_commit(four_team_git):
    resolved, plans, work, result = four_team_git
    commit_shas = {a["sha"] for a in result.artifacts if a["kind"] == "commit"}
    for link in result.session_links:
        if link["method"] == "sha":
            assert link["signals"]["sha"] in commit_shas
        else:
            assert link["method"] == "heuristic"
            assert 0.55 <= link["confidence"] <= 0.9


# --------------------------------------------------------------------------------------------
# Open PRs at series end
# --------------------------------------------------------------------------------------------

def test_open_prs_at_series_end(four_team_git):
    resolved, plans, work, result = four_team_git
    open_prs = [a for a in result.artifacts if a["kind"] == "pr" and a["state"] == "open"]
    assert open_prs, "expected at least one still-open PR in the 90-day scenario"
    for pr in open_prs:
        assert pr["merged_at"] is None
        assert pr["closed_at"] is None
