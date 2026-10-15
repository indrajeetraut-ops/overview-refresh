"""A `pr-link` record -- Claude Code's own note of a PR it opened -- must ship as a typed
`pr_link` event (ported from the github-acquisition normalizer into the integ base, combined
sub-plan §1). It is the source of `session_info.pr_list`, and `prRepository` is the top rung of
the repo_id evidence ladder.
"""
from __future__ import annotations

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from fixtures.normalizer import sessions


def _events(source):
    return list(ClaudeCodeNormalizer().normalize(source))[0].events


def test_pr_link_record_maps_to_pr_link_event():
    events = _events(sessions.pr_link_session())
    pr = [e for e in events if e.event_type == "pr_link"]
    assert len(pr) == 1
    e = pr[0]
    assert e.payload["pr_number"] == 42
    assert e.payload["pr_url"] == "https://github.com/acme/repo/pull/42"
    assert e.payload["pr_repository"] == "acme/repo"
    # Not part of the conversation tree: no vendor uuid/parentUuid, so both are null.
    assert e.record_id is None
    assert e.parent_id is None
    assert e.event_ts == "2026-07-09T17:00:05.000Z"
    # Losslessness: the whole vendor record survives in raw.
    assert e.payload["raw"]["type"] == "pr-link"


def test_pr_link_is_not_dropped_to_unknown():
    events = _events(sessions.pr_link_session())
    assert not any(
        e.event_type == "unknown" and e.payload.get("raw", {}).get("type") == "pr-link"
        for e in events
    )
