"""Pure mapper tests (git-source.md §5.3/§5.4/§5.6/§5.7, §11's golden-artifact-JSON bar).

Fixtures + goldens live in `tests/fixtures/github/` (see its README: synthetic, not yet
recorded from a live delivery). Golden files hold every `MapResult` field, generated once from
this module and hand-verified against the fixtures at generation time -- a snapshot regression
test, not an independent oracle, which is why the correlation/truncation *mechanisms* below are
additionally asserted directly rather than only through the goldens.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.ingest.git.map_github import PUSH_TRUNCATE_AT, map_delivery

FIXTURES = Path("tests/fixtures/github")
GOLDEN = FIXTURES / "golden"
ORG_ID = "org_test"

FIXTURE_NAMES = [
    "pull_request_opened",
    "pull_request_closed_merged",
    "pull_request_review_submitted",
    "push_squash_merge",
    "push_two_commits_with_revert",
    "installation_created",
    "issues_opened",
    "issues_closed",
    "issue_comment_created_on_plain_issue",
    "ping",
]


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _golden(name: str) -> dict:
    return json.loads((GOLDEN / f"{name}.json").read_text())


def _result_to_dict(result) -> dict:
    return {
        "mapped": result.mapped, "artifacts": result.artifacts,
        "bump_counters": result.bump_counters, "repos": result.repos,
        "installation": result.installation, "merge_commit_sha": result.merge_commit_sha,
        "push_before": result.push_before, "push_after": result.push_after,
        "push_integration_method": result.push_integration_method,
        "push_commit_shas": result.push_commit_shas,
        "issue_links": result.issue_links,
        "pr_state_event": result.pr_state_event,
    }


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_golden_artifact_json_per_fixture(name):
    fixture = _load(name)
    result = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    assert json.loads(json.dumps(_result_to_dict(result), sort_keys=True)) == _golden(name)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_mapping_is_deterministic_same_payload_twice_byte_identical(name):
    fixture = _load(name)
    r1 = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    r2 = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    assert json.dumps(_result_to_dict(r1), sort_keys=True) == json.dumps(_result_to_dict(r2), sort_keys=True)


def test_unmapped_event_is_not_an_error():
    fixture = _load("ping")
    result = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    assert result.mapped is False
    assert result.artifacts == []


# ------------------------------------------------------------------------- issues (ISSUE H1)

def test_issues_opened_maps_a_titled_statused_issue_artifact():
    fixture = _load("issues_opened")
    result = map_delivery("issues", "opened", fixture["payload"], ORG_ID)
    assert result.mapped is True
    (artifact,) = result.artifacts
    assert artifact["kind"] == "issue"
    assert artifact["artifact_id"] == "github:41213765:issue:318"
    assert artifact["state"] == "open"
    assert artifact["title"] == "Async auth middleware"
    assert artifact["labels"] == ["auth", "perf"]
    assert artifact["milestone"] == "Sprint 21"
    assert artifact["info"]["github.assignees"] == ["akhuteta"]
    assert artifact["stats"]["comment_count"] == 0
    # Every PR-only column stays null on an issue row.
    for null_field in ("sha", "branch", "base_branch", "files", "merge_commit_sha",
                       "before_sha", "after_sha", "integration_method", "commit_shas",
                       "merged_at", "parent_artifact_id"):
        assert artifact[null_field] is None, null_field


def test_issues_closed_carries_closed_at_and_state():
    fixture = _load("issues_closed")
    result = map_delivery("issues", "closed", fixture["payload"], ORG_ID)
    (artifact,) = result.artifacts
    assert artifact["state"] == "closed"
    assert artifact["closed_at"] == "2026-09-04T16:30:00Z"


def test_issues_deleted_action_is_landed_not_mapped():
    # `deleted`/`transferred` retain the row as history (§5.4) -- nothing to upsert.
    fixture = _load("issues_opened")
    result = map_delivery("issues", "deleted", fixture["payload"], ORG_ID)
    assert result.mapped is False


def test_issues_event_for_a_pull_request_shaped_object_is_skipped():
    # GitHub never fires `issues` for a PR; if one somehow arrives it is the `pull_request`
    # event's to map, not this one's.
    payload = {
        "action": "opened",
        "issue": {"number": 9, "title": "x", "state": "open", "user": {"login": "u"},
                  "pull_request": {"url": "..."}, "labels": [], "comments": 0,
                  "created_at": "2026-09-01T10:00:00Z", "updated_at": "2026-09-01T10:00:00Z"},
        "repository": {"id": 41213765},
    }
    assert map_delivery("issues", "opened", payload, ORG_ID).mapped is False


def test_issue_comment_on_a_plain_issue_bumps_the_issue_comment_count():
    payload = {
        "action": "created",
        "issue": {"number": 55},   # no `pull_request` key => it is a plain issue
        "repository": {"id": 41213765}, "comment": {"id": 990101},
    }
    result = map_delivery("issue_comment", "created", payload, ORG_ID)
    assert result.mapped is True
    assert result.artifacts == []
    assert result.bump_counters == [
        {"artifact_id": "github:41213765:issue:55", "field": "comment_count", "delta": 1,
         "dedup_key": "990101:created"}
    ]
    deleted = map_delivery("issue_comment", "deleted", {**payload, "action": "deleted"}, ORG_ID)
    assert deleted.bump_counters == [
        {"artifact_id": "github:41213765:issue:55", "field": "comment_count", "delta": -1,
         "dedup_key": "990101:deleted"}
    ]


def test_pull_request_review_never_bumps_via_a_delta_counter():
    # review_count is recomputed from actual review rows (apply.py's `recompute_review_count`),
    # not accumulated -- a delta bump here would double-count under a mapper-defect replay
    # (§10). True for both actions: "submitted" and "dismissed" alike produce zero bumps.
    fixture = _load("pull_request_review_submitted")
    submitted = map_delivery("pull_request_review", "submitted", fixture["payload"], ORG_ID)
    assert submitted.bump_counters == []

    dismissed_payload = json.loads(json.dumps(fixture["payload"]))
    dismissed_payload["review"]["state"] = "dismissed"
    dismissed = map_delivery("pull_request_review", "dismissed", dismissed_payload, ORG_ID)
    assert dismissed.mapped is True
    assert dismissed.bump_counters == []


def test_pull_request_review_comment_bumps_comment_count_signed():
    payload = {
        "action": "created", "pull_request": {"number": 1184},
        "repository": {"id": 41213765}, "comment": {"id": 555},
    }
    created = map_delivery("pull_request_review_comment", "created", payload, ORG_ID)
    assert created.bump_counters == [
        {"artifact_id": "github:41213765:pr:1184", "field": "comment_count", "delta": 1,
         "dedup_key": "555:created"}
    ]
    deleted = map_delivery("pull_request_review_comment", "deleted",
                           {**payload, "action": "deleted"}, ORG_ID)
    assert deleted.bump_counters == [
        {"artifact_id": "github:41213765:pr:1184", "field": "comment_count", "delta": -1,
         "dedup_key": "555:deleted"}
    ]


def test_issue_comment_on_a_pr_bumps_comment_count():
    payload = {
        "action": "created",
        "issue": {"number": 1184, "pull_request": {"url": "..."}},  # presence of key = it's a PR
        "repository": {"id": 41213765}, "comment": {"id": 777},
    }
    result = map_delivery("issue_comment", "created", payload, ORG_ID)
    assert result.mapped is True
    assert result.bump_counters == [
        {"artifact_id": "github:41213765:pr:1184", "field": "comment_count", "delta": 1,
         "dedup_key": "777:created"}
    ]


def test_push_over_truncate_threshold_records_range_not_per_commit():
    commits = [
        {"id": f"{i:040x}", "message": f"commit {i}", "timestamp": "2026-08-01T00:00:00Z",
         "author": {"username": "bot", "email": "bot@example.com"}, "added": [], "removed": [], "modified": []}
        for i in range(PUSH_TRUNCATE_AT)
    ]
    payload = {
        "ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40,
        "repository": {"id": 41213765},
        "commits": commits,
        "head_commit": {"timestamp": "2026-08-01T00:00:00Z"},
    }
    result = map_delivery("push", None, payload, ORG_ID)
    assert result.mapped is True
    assert len(result.artifacts) == 1
    artifact = result.artifacts[0]
    assert artifact["kind"] == "commit"
    assert artifact["before_sha"] == "b" * 40
    assert artifact["after_sha"] == "a" * 40
    assert artifact["info"]["github.truncated_push"] is True
    assert artifact["info"]["github.commit_count_inlined"] == PUSH_TRUNCATE_AT
    # A truncated push must never claim a commit list -- the compare API in gap-fill (A5)
    # completes it (§5.4, §8.2).
    assert result.push_commit_shas is None


def test_push_under_truncate_threshold_maps_one_commit_artifact_per_commit():
    payload = {
        "ref": "refs/heads/main", "before": "b" * 40, "after": "a" * 40,
        "repository": {"id": 41213765},
        "commits": [
            {"id": "a" * 40, "message": "one", "timestamp": "2026-08-01T00:00:00Z",
             "author": {"username": "u", "email": "u@example.com"}, "added": [], "removed": [], "modified": []},
        ],
    }
    result = map_delivery("push", None, payload, ORG_ID)
    assert len(result.artifacts) == 1
    assert "github.truncated_push" not in result.artifacts[0]["info"]


def test_merge_transition_fields_present_on_the_correlating_push():
    fixture = _load("push_squash_merge")
    result = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    assert result.push_before is not None
    assert result.push_after is not None
    assert result.push_integration_method == "squash"
    assert result.push_commit_shas == [result.push_after]
    # Stashed on the head commit's info so the OTHER arrival order can still find it (§5.7).
    assert result.artifacts[-1]["info"]["github.push_meta"]["before_sha"] == result.push_before


def test_merged_pr_carries_merge_commit_sha_for_worker_side_lookup():
    fixture = _load("pull_request_closed_merged")
    result = map_delivery(fixture["event"], fixture["action"], fixture["payload"], ORG_ID)
    assert result.merge_commit_sha == fixture["payload"]["pull_request"]["merge_commit_sha"]


def test_revert_pr_title_sets_flag_and_parses_reverts_sha():
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 5, "title": 'Revert "Add flaky retry"',
            "body": "This reverts commit " + "c" * 40 + ".",
            "state": "open", "merged": False, "user": {"login": "u"},
            "head": {"ref": "revert-branch", "sha": "d" * 40}, "base": {"ref": "main"},
            "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        },
        "repository": {"id": 41213765},
    }
    result = map_delivery("pull_request", "opened", payload, ORG_ID)
    artifact = result.artifacts[0]
    assert artifact["info"]["github.is_revert"] is True
    assert artifact["reverts_sha"] == "c" * 40


def test_bot_author_flagged():
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 6, "title": "chore: bump deps", "state": "open", "merged": False,
            "user": {"login": "dependabot[bot]"},
            "head": {"ref": "deps", "sha": "e" * 40}, "base": {"ref": "main"},
            "created_at": "2026-08-01T00:00:00Z", "updated_at": "2026-08-01T00:00:00Z",
        },
        "repository": {"id": 41213765},
    }
    result = map_delivery("pull_request", "opened", payload, ORG_ID)
    assert result.artifacts[0]["is_bot"] is True


# ------------------------------------------------------------------------- PR transitions (churn/reopen)

def _pr_payload(action: str, *, merged: bool = False, state: str = "open",
                updated_at: str = "2026-08-02T10:00:00Z") -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 7, "title": "feat: thing", "state": state, "merged": merged,
            "user": {"login": "u"}, "head": {"ref": "f", "sha": "a" * 40}, "base": {"ref": "main"},
            "created_at": "2026-08-01T00:00:00Z", "updated_at": updated_at,
        },
        "repository": {"id": 41213765},
    }


def test_reopened_action_emits_a_reopened_state_event():
    result = map_delivery("pull_request", "reopened",
                          _pr_payload("reopened", updated_at="2026-08-03T12:00:00Z"), ORG_ID)
    assert result.pr_state_event == {
        "artifact_id": "github:41213765:pr:7", "action": "reopened", "to_state": "open",
        "occurred_at": "2026-08-03T12:00:00Z",
        "source_key": "github:41213765:pr:7:reopened:2026-08-03T12:00:00Z",
    }


def test_closed_merged_action_is_logged_as_a_merged_transition_not_closed():
    # A merge arrives as action="closed" with merged=true; the event action normalizes to
    # "merged" so it matches the artifact `state` vocabulary a reopen/merge count reads.
    result = map_delivery("pull_request", "closed",
                          _pr_payload("closed", merged=True, state="closed"), ORG_ID)
    assert result.pr_state_event["action"] == "merged"
    assert result.pr_state_event["to_state"] == "merged"


def test_synchronize_action_records_no_state_event():
    # A push to an open PR changes no state -> no transition row (would otherwise inflate any
    # transition count with non-transitions).
    result = map_delivery("pull_request", "synchronize", _pr_payload("synchronize"), ORG_ID)
    assert result.pr_state_event is None
