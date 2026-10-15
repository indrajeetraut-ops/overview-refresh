"""PR<->issue linkage resolver + tracker-agnostic ref (ISSUE §3.1/§3.2, plan H3/H4). Pure --
no DB, no network."""
from __future__ import annotations

from server.ingest.git.issue_ref import (
    IssueRef,
    github_ref,
    is_sha,
    parse_github_refs,
    parse_jira_refs,
)
from server.ingest.git.linkage import resolve_links

REPO = 41213765
PK = str(REPO)


def _only(links):
    assert len(links) == 1, links
    return links[0]


# ------------------------------------------------------------------------ the §3.2 ladder

def test_graphql_closing_reference_wins_at_confidence_1_over_a_body_closes():
    links = resolve_links(
        repo_num=REPO, project_key=PK,
        closing_issue_numbers=[318],
        body="Closes #999.",   # a competing, weaker signal -- must be ignored
        branch="318-async-auth", title="#777 async auth",
    )
    lk = _only(links)
    assert lk.method == "graphql" and lk.confidence == 1.0
    assert lk.ref == IssueRef("github", PK, 318, f"github:{REPO}:issue:318")


def test_graphql_and_body_pointing_at_the_same_issue_still_resolve_to_that_issue_at_1():
    links = resolve_links(repo_num=REPO, project_key=PK,
                          closing_issue_numbers=[318], body="Closes #318")
    lk = _only(links)
    assert lk.ref.number == 318 and lk.confidence == 1.0


def test_multiple_closing_references_yield_multiple_graphql_links():
    links = resolve_links(repo_num=REPO, project_key=PK, closing_issue_numbers=[10, 12])
    assert {lk.ref.number for lk in links} == {10, 12}
    assert all(lk.method == "graphql" and lk.confidence == 1.0 for lk in links)


def test_commit_trailer_closes_resolves_at_0_9_when_no_graphql_reference():
    links = resolve_links(
        repo_num=REPO, project_key=PK,
        commit_messages=["wip", "auth middleware\n\nCloses #318"],
        branch="feature/whatever", body="see #999",
    )
    lk = _only(links)
    assert lk.method == "trailer" and lk.confidence == 0.9 and lk.ref.number == 318


def test_branch_only_link_resolves_at_0_7():
    lk = _only(resolve_links(repo_num=REPO, project_key=PK, branch="feature/318-async-auth"))
    assert lk.method == "branch" and lk.confidence == 0.7 and lk.ref.number == 318


def test_bare_number_branch_and_gh_prefixed_branch_both_resolve():
    assert _only(resolve_links(repo_num=REPO, project_key=PK, branch="318-x")).ref.number == 318
    assert _only(resolve_links(repo_num=REPO, project_key=PK, branch="gh-318-x")).ref.number == 318
    assert _only(resolve_links(repo_num=REPO, project_key=PK, branch="issue/318")).ref.number == 318


def test_title_hash_and_leading_number_resolve_at_0_6():
    assert _only(resolve_links(repo_num=REPO, project_key=PK, title="Fix auth (#318)")).confidence == 0.6
    assert _only(resolve_links(repo_num=REPO, project_key=PK, title="318: fix auth")).ref.number == 318


def test_body_closes_is_the_last_rung_at_0_5():
    lk = _only(resolve_links(repo_num=REPO, project_key=PK, body="Some description. Fixes #318."))
    assert lk.method == "body" and lk.confidence == 0.5 and lk.ref.number == 318


def test_no_signal_anywhere_resolves_to_no_links():
    assert resolve_links(repo_num=REPO, project_key=PK, title="chore: bump deps",
                         branch="deps-bump", body="routine") == []


# ------------------------------------------------------------------------- validation (§3.2)

def test_sha_looking_branch_segment_is_not_read_as_an_issue_number():
    assert resolve_links(repo_num=REPO, project_key=PK,
                         branch="fix/deadbeefdeadbeefdeadbeefdeadbeefdeadbeef") == []


def test_utf_8_and_iso_8601_in_a_title_do_not_manufacture_jira_tickets():
    # No known project keys configured => every PROJ-N-shaped token is rejected.
    assert resolve_links(repo_num=REPO, project_key=PK,
                         title="Encode as UTF-8 per ISO-8601", branch="x", body="y") == []


def test_a_known_jira_key_parses_to_the_common_shape_with_no_artifact_id():
    lk = _only(resolve_links(repo_num=REPO, project_key=PK, branch="PLAT-123-async-auth",
                             known_project_keys={"PLAT"}))
    assert lk.method == "branch" and lk.confidence == 0.7
    assert lk.ref == IssueRef("jira", "PLAT", 123, None)
    assert lk.ref.key == ("jira", "PLAT", 123)


def test_standards_tokens_stay_rejected_even_if_mis_configured_as_a_key():
    assert resolve_links(repo_num=REPO, project_key=PK, title="UTF-8 everywhere",
                         known_project_keys={"UTF"}) == []


# --------------------------------------------------------------------------- issue_ref units

def test_is_sha():
    assert is_sha("a" * 40) and is_sha("deadbeef")
    assert not is_sha("318") and not is_sha("") and not is_sha("g" * 40)


def test_parse_github_refs_dedups_and_preserves_order():
    refs = parse_github_refs("closes #7, also #3, and again #7", REPO, PK)
    assert [r.number for r in refs] == [7, 3]
    assert refs[0].artifact_id == f"github:{REPO}:issue:7"


def test_parse_jira_refs_needs_known_keys():
    assert parse_jira_refs("PLAT-1 and OPS-2", None) == []
    refs = parse_jira_refs("PLAT-1 and OPS-2", {"PLAT"})
    assert [(r.project_key, r.number) for r in refs] == [("PLAT", 1)]


def test_github_ref_builder():
    assert github_ref(REPO, PK, 9).as_dict() == {
        "tracker": "github", "project_key": PK, "number": 9,
        "artifact_id": f"github:{REPO}:issue:9",
    }
