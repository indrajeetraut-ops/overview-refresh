"""_wrap_up / commit-boundary detection (segments/beats.py). Regression for the wrap-up-sha-path
fix (2026-09-15): a successful commit's `gitOperation.commit.sha` rides the tool RESULT
(`payload.raw.toolUseResult.gitOperation.commit.sha`), NOT the tool_call the pre-fix code read --
so wrap_up beats must read that path, and a commit (a strong/boundary beat, R4.2) must open a new
segment.
"""
from server.worker.segments.beats import (
    _commit_sha_from_tool_result,
    _wrap_up,
    build_segments,
    detect_beats,
    segment_boundaries,
)
from server.worker.segments.config import BeatsConfig


def _commit_result(ts: str, sha: str, kind: str = "committed") -> dict:
    return {
        "event_type": "tool_result", "event_ts": ts,
        "payload": {"raw": {"toolUseResult": {
            "gitOperation": {"commit": {"sha": sha, "kind": kind}},
        }}},
    }


def test_commit_sha_extracted_from_the_real_tool_result_path():
    assert _commit_sha_from_tool_result(_commit_result("2026-01-01T00:00:00Z", "abc1234")) == "abc1234"


def test_the_old_tool_call_shape_yields_no_sha():
    """The pre-fix code read `payload.commit` on a `tool_call` -- a shape the normalizer never
    emits. It must produce nothing (not a spurious beat), or wrap_up beats would fire on a field
    that is always absent (the original bug) or, worse, a fabricated one."""
    old_shape = {"event_type": "tool_call", "event_ts": "t",
                 "payload": {"tool_name": "Bash", "commit": {"sha": "abc1234"}}}
    assert _commit_sha_from_tool_result(old_shape) is None
    assert _wrap_up([old_shape]) == []


def test_string_tool_result_does_not_crash():
    """`toolUseResult` is a plain string for some tools -- the descent must guard, not raise."""
    ev = {"event_type": "tool_result", "event_ts": "t", "payload": {"raw": {"toolUseResult": "done"}}}
    assert _commit_sha_from_tool_result(ev) is None


def test_a_non_commit_git_operation_yields_no_sha():
    ev = {"event_type": "tool_result", "event_ts": "t",
          "payload": {"raw": {"toolUseResult": {"gitOperation": {"status": {"clean": True}}}}}}
    assert _commit_sha_from_tool_result(ev) is None


def test_wrap_up_fires_one_beat_per_commit_with_the_sha_as_evidence():
    events = [_commit_result("2026-01-01T00:00:00Z", "aaa"),
              _commit_result("2026-01-01T01:00:00Z", "bbb", kind="cherry-picked")]
    beats = _wrap_up(events)
    assert [b["kind"] for b in beats] == ["wrap_up", "wrap_up"]
    assert [b["evidence"]["sha"] for b in beats] == ["aaa", "bbb"]
    assert all(b["confidence"] == "fact" for b in beats)


def test_a_mid_session_commit_opens_a_new_segment_boundary():
    """The whole point of the fix: `wrap_up` is a strong beat (R4.2), so a commit mid-session is a
    phase boundary. Edit -> commit -> more work must split into two segments, where before the fix
    the commit was invisible and the session stayed one blocky phase."""
    cfg = BeatsConfig()
    events = [
        {"event_type": "file_change", "event_ts": "2026-01-01T00:00:00Z", "payload": {"path": "a.py"}},
        _commit_result("2026-01-01T00:10:00Z", "aaa"),
        {"event_type": "file_change", "event_ts": "2026-01-01T00:20:00Z", "payload": {"path": "b.py"}},
    ]
    beats = detect_beats(events, [], cfg, [])
    assert any(b["kind"] == "wrap_up" for b in beats)
    assert "2026-01-01T00:10:00Z" in segment_boundaries(beats, cfg.segment_pause_threshold_s)
    assert len(build_segments(events, [], beats, cfg, None)) == 2
