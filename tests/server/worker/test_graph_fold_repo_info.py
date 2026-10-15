"""repo_info's evidence ladder, focused on the `remote` tier (interim fix for the `local:`
repo_id that never bridges to a connected GitHub repo -- 2026-09-13 gap-closure).

`session_start.git_remote` is backfilled by the collector engine from the upload script's real
`git remote get-url origin`, matched onto a session by cwd -- distinct from, and preferred
over, anything a `tool_call` happens to have scraped from the transcript itself.
"""
from server.worker.graph_fold import repo_info


def _session_start(cwd="/Users/dev/widgets", git_branch="main", git_remote=None):
    return {"event_type": "session_start", "payload": {"cwd": cwd, "git_branch": git_branch, "git_remote": git_remote}}


def test_session_start_remote_resolves_past_the_local_path_floor():
    events = [_session_start(git_remote="git@github.com:acme/widgets.git")]

    info = repo_info(events)

    assert info["repo_id"] == "github:acme/widgets"
    assert info["repo_id_provenance"] == "remote"


def test_session_start_remote_is_preferred_over_a_tool_call_scraped_remote():
    events = [
        _session_start(git_remote="git@github.com:acme/widgets.git"),
        {"event_type": "tool_call", "payload": {"git_remote": "git@github.com:someone-else/fork.git"}},
    ]

    info = repo_info(events)

    assert info["repo_id"] == "github:acme/widgets"
    assert info["repo_id_provenance"] == "remote"


def test_falls_back_to_tool_call_scraped_remote_when_session_start_has_none():
    events = [
        _session_start(git_remote=None),
        {"event_type": "tool_call", "payload": {"git_remote": "git@github.com:acme/widgets.git"}},
    ]

    info = repo_info(events)

    assert info["repo_id"] == "github:acme/widgets"
    assert info["repo_id_provenance"] == "remote"


def test_falls_back_to_path_tier_when_no_remote_evidence_at_all():
    events = [_session_start(cwd="/Users/dev/widgets", git_remote=None)]

    info = repo_info(events)

    assert info["repo_id"] == "local:widgets"
    assert info["repo_id_provenance"] == "path"


def test_pr_link_still_wins_over_a_session_start_remote():
    events = [
        _session_start(git_remote="git@github.com:acme/widgets.git"),
        {"event_type": "pr_link", "payload": {"pr_repository": "acme/the-real-repo"}},
    ]

    info = repo_info(events)

    assert info["repo_id"] == "github:acme/the-real-repo"
    assert info["repo_id_provenance"] == "pr_link"
