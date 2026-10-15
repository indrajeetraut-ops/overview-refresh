from datetime import datetime, timedelta, timezone

import pytest

from server.api.single_user import service
from server.api.errors import BadRequestError, NotFoundError
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitArtifactEvent, SessionGitLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conformance import assert_cold_start, assert_sourced, assert_unavailable
from .conftest import requires_db

pytestmark = requires_db

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed(s, session_id, datasource, *, cost_effective_usd_micros=None, otel=None,
          models=None, tools=None, skills=None, subagents=None, files=None, turn_count=0,
          info=None, status="live", turns=None, last_event_at=NOW):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id="u1",
                     agent="claude_code", agent_version="2.1.219", status=status,
                     started_at=NOW, last_event_at=last_event_at, signal_blob={}, info_blob={},
                     subagents=[]))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models=models or {}, tools=tools or {}, files=files or {}, skills=skills or [],
        subagents=subagents or {}, pr_list=[], turns=turns or [], turn_count=turn_count,
        long_turn_count=0, info=info or {}, otel=otel or {},
        cost_effective_usd_micros=cost_effective_usd_micros, datasource=datasource,
    ))


def test_unknown_session_raises_not_found(engine, catalog, api_config):
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            service.session_detail(s, catalog, api_config, "does-not-exist")


def test_calibration_never_reaches_the_response_even_when_present_in_otel(engine, catalog, api_config):
    """MODES §3.1: calibration is internal and must not reach a response body at all --
    asserted against a block that deliberately carries the key, not just an absent one."""
    with session_scope() as s:
        _seed(s, "both-1", "both", cost_effective_usd_micros=5_000_000,
             otel={"cost_usd_micros": 5_000_000, "cost_est_usd_micros": 5_000_000,
                  "cost_basis": {"vendor": 1, "estimated": 0, "unknown": 0},
                  "cost_complete": True, "by_query_source": {"sdk": {"requests": 1}},
                  "calibration": {"claude-opus-5": {"n": 8, "max_abs_micros": 1}}})

    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "both-1")

    assert "calibration" not in result["cost"]
    assert result["cost"]["cost_usd_micros"] == 5_000_000
    assert result["cost"]["cost_complete"] is True
    assert result["cost"]["by_query_source"] == {"sdk": {"requests": 1}}


def test_both_mode_session_has_a_full_metrics_panel(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "both-1", "both", cost_effective_usd_micros=1_000_000,
             models={"claude-opus-5": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0}},
             tools={"Bash": {"count": 2, "failed": 0}}, turn_count=3,
             info={"title": "Fix the bug", "repo": {"repo_key": "myrepo"},
                  "compactions": [{"trigger": "manual"}], "cache": {"hit_ratio": 0.5},
                  "errors": {"retry_count": 1}})

    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "both-1")

    assert result["title"] == "Fix the bug"
    assert result["repo"] == "myrepo"
    assert result["metrics"]["models"] == {"claude-opus-5": {"input": 10, "output": 5,
                                                              "cache_read": 0, "cache_write": 0}}
    assert result["metrics"]["turn_count"] == 3
    assert result["metrics"]["compactions"] == [{"trigger": "manual"}]
    assert result["metrics"]["cache"] == {"hit_ratio": 0.5}
    assert result["provenance"] is None       # cold-start, parked this pass


def test_otel_only_session_has_unavailable_metrics_but_a_real_cost_block(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", cost_effective_usd_micros=2_000_000, status="otel_only",
             otel={"cost_usd_micros": None, "cost_est_usd_micros": 2_000_000,
                  "cost_basis": {"vendor": 0, "estimated": 1, "unknown": 0},
                  "cost_complete": True})

    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "otel-1")

    assert result["datasource"] == "otel_only"
    assert result["title"] is None
    assert result["repo"] is None
    for field in ("models", "tools", "skills", "subagents", "files", "turn_count",
                  "long_turn_count", "compactions", "cache", "errors"):
        assert result["metrics"][field] == {"unavailable": {"block": "metrics", "requires": "jsonl"}}
    assert result["cost"]["cost_effective_usd_micros"] == 2_000_000
    assert result["cost"]["by_query_source"] == {
        "unavailable": {"block": "by_query_source", "requires": "otel"}}
    # R7.5: all four new blocks are `unavailable`, not cold-start, in otel_only.
    assert_unavailable(result["cost"]["by_channel"], "jsonl")
    assert_unavailable(result["repos"], "jsonl")
    assert_unavailable(result["segments"], "jsonl")
    assert_unavailable(result["findings"], "jsonl")


def test_jsonl_only_session_has_no_by_query_source(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", cost_effective_usd_micros=500_000)

    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "jsonl-1")

    assert result["cost"]["by_query_source"] == {
        "unavailable": {"block": "by_query_source", "requires": "otel"}}


def test_pre_tick_session_has_real_metrics_not_unavailable(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "pre-tick", None, cost_effective_usd_micros=None,
             tools={"Bash": {"count": 1, "failed": 0}}, turn_count=2,
             info={"title": "Untouched by the true-up yet"})

    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "pre-tick")

    assert result["title"] == "Untouched by the true-up yet"
    assert result["metrics"]["tools"] == {"Bash": {"count": 1, "failed": 0}}
    assert result["cost"]["cost_effective_usd_micros"] is None


# ---------------------------------------------------------------- R7.1: repos

def test_repos_cold_start_before_r1_fold(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "pre-r1-1", "both", info={})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "pre-r1-1")
    assert_cold_start(result["repos"])


def test_repos_empty_list_with_no_file_activity(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "no-repo-1", "both", info={"repo": {"repos": []}})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "no-repo-1")
    assert result["repos"] == []


def test_repos_ranked_list_with_share(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "multi-repo-1", "both", info={"repo": {"repos": [
            {"repo_id": "local:a", "repo_key": "a", "refs": 6, "primary": True, "repo_id_provenance": "path"},
            {"repo_id": "local:b", "repo_key": "b", "refs": 2, "primary": False, "repo_id_provenance": "path"},
        ]}})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "multi-repo-1")
    assert result["repos"] == [
        {"repo_id": "local:a", "repo_key": "a", "display": "a", "refs": 6, "share": 0.75, "primary": True,
         "cost_usd_micros": None},
        {"repo_id": "local:b", "repo_key": "b", "display": "b", "refs": 2, "share": 0.25, "primary": False,
         "cost_usd_micros": None},
    ]


def test_repos_cost_split_reads_real_segment_cost_per_repo_key(engine, catalog, api_config):
    """The per-session cost split (segment-level-pricing plan): each repo's `cost_usd_micros`
    is the sum of its own segments' `cost_usd_micros` (Phase 1), keyed by the *same* `repo_key`
    `info.repo.repos[]` and a segment's own `repo_key` both use -- not the `refs`-based `share`
    proxy. A repo with no segment at all (or an unpriced one) stays `None`, never a guessed $0."""
    with session_scope() as s:
        _seed(s, "multi-repo-2", "both", info={
            "repo": {"repos": [
                {"repo_id": "local:a", "repo_key": "a", "refs": 6, "primary": True, "repo_id_provenance": "path"},
                {"repo_id": "local:b", "repo_key": "b", "refs": 2, "primary": False, "repo_id_provenance": "path"},
                {"repo_id": "local:c", "repo_key": "c", "refs": 1, "primary": False, "repo_id_provenance": "path"},
            ]},
            "segments": [
                {"index": 0, "repo_key": "a", "cost_usd_micros": 300_000, "beats": {}},
                {"index": 1, "repo_key": "a", "cost_usd_micros": 200_000, "beats": {}},
                {"index": 2, "repo_key": "b", "cost_usd_micros": None, "beats": {}},  # unpriced request
            ],
        })
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "multi-repo-2")
    by_key = {r["repo_key"]: r["cost_usd_micros"] for r in result["repos"]}
    assert by_key == {"a": 500_000, "b": None, "c": None}  # a: summed; b: all-or-nothing unpriced; c: no segment


# ---------------------------------------------------------------- R7.2: cost.by_channel

def test_cost_by_channel_cold_start_before_engine_lands(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "pre-r2-2", "both", info={})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "pre-r2-2")
    assert_cold_start(result["cost"]["by_channel"])


def test_cost_by_channel_reports_all_four_with_sources_and_shares(engine, catalog, api_config):
    info = {
        "carry": {"cache_read_tokens": 100_000, "carry_usd_micros": 1_000_000},
        "channel_cost": {
            "recache": {"tokens": 20_000, "usd_micros": 200_000},
            "output": {"tokens": 5_000, "usd_micros": 500_000},
            "input": {"tokens": 1_000, "usd_micros": 100_000},
        },
    }
    with session_scope() as s:
        _seed(s, "chan-1", "both", info=info)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "chan-1")

    by_channel = result["cost"]["by_channel"]
    assert_sourced(by_channel["carry"], "carry_rollup")
    assert by_channel["carry"]["value"] == {
        "tokens": 100_000, "usd_micros": 1_000_000, "share": 1_000_000 / 1_800_000}
    assert_sourced(by_channel["recache"], "channel_cost_rollup")
    assert by_channel["recache"]["value"]["usd_micros"] == 200_000
    assert by_channel["input"]["value"]["tokens"] == 1_000


def test_cost_by_channel_share_omitted_when_any_channel_is_unpriced(engine, catalog, api_config):
    info = {
        "carry": {"cache_read_tokens": 100_000, "carry_usd_micros": None},
        "channel_cost": {
            "recache": {"tokens": 20_000, "usd_micros": 200_000},
            "output": {"tokens": 5_000, "usd_micros": 500_000},
            "input": {"tokens": 1_000, "usd_micros": 100_000},
        },
    }
    with session_scope() as s:
        _seed(s, "chan-2", "both", info=info)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "chan-2")

    by_channel = result["cost"]["by_channel"]
    assert by_channel["carry"]["value"]["usd_micros"] is None
    assert by_channel["carry"]["value"]["share"] is None
    assert by_channel["recache"]["value"]["share"] is None


# ---------------------------------------------------------------- R7.3: segments

def test_segments_cold_start_before_r4_r5(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "pre-r4-1", "both", info={})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "pre-r4-1")
    assert_cold_start(result["segments"])


def test_segments_reshaped_from_info_and_drops_unknown_keys(engine, catalog, api_config):
    segments = [{
        "index": 0, "label": "IMPLEMENT", "confidence": 0.7, "t0": "t1", "t1": "t2",
        "turns": 3, "files": 2, "repo_key": "myrepo", "beats": {"wrap_up": 1},
        "carry_usd_micros": 10_000, "cost_usd_micros": 50_000, "some_future_field": "drop me",
    }]
    with session_scope() as s:
        _seed(s, "seg-1", "both", info={"segments": segments})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "seg-1")
    assert result["segments"] == [{
        "index": 0, "label": "IMPLEMENT", "confidence": 0.7, "t0": "t1", "t1": "t2",
        "turns": 3, "files": 2, "repo_key": "myrepo", "beats": {"wrap_up": 1},
        "carry_usd_micros": 10_000, "cost_usd_micros": 50_000,
    }]


def test_segments_empty_list_is_real_not_cold_start(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "seg-2", "both", info={"segments": []})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "seg-2")
    assert result["segments"] == []


# ---------------------------------------------------------------- R7.4: findings

def test_findings_cold_start_before_r2_r3_fold(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "pre-r2-1", "both", info={})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "pre-r2-1")
    assert_cold_start(result["findings"])


def test_findings_empty_when_nothing_fires(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "clean-1", "both", info={"carry": {}, "resume_cost": {}})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "clean-1")
    assert result["findings"] == []


def test_findings_resume_fires_once_idle_past_the_threshold(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "idle-1", "both", last_event_at=NOW, info={
            "carry": {}, "resume_cost": {"projected": {
                "context_tokens": 50_000, "ttl_s": 3600, "usd_micros": 400_000}},
        })

    later = NOW + timedelta(hours=2)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "idle-1", now=later)

    finding = next(f for f in result["findings"] if f["finding_id"] == "resume")
    assert finding["usd_micros"] == 400_000
    assert finding["basis"] == "recoverable"


def test_findings_resume_absent_before_the_session_has_gone_idle(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "idle-2", "both", last_event_at=NOW, info={
            "carry": {}, "resume_cost": {"projected": {
                "context_tokens": 50_000, "ttl_s": 3600, "usd_micros": 400_000}},
        })

    soon = NOW + timedelta(minutes=10)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "idle-2", now=soon)

    assert all(f["finding_id"] != "resume" for f in result["findings"])


def test_findings_read_then_discard_is_recoverable_but_unpriced(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "rtd-1", "both", info={
            "carry": {}, "resume_cost": {}, "read_then_discard": {"total_est_tokens": 12_000},
        })
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "rtd-1")

    finding = next(f for f in result["findings"] if f["finding_id"] == "read_then_discard")
    assert finding["usd_micros"] is None
    assert finding["basis"] == "recoverable"
    assert finding["action"] is not None


def test_findings_long_tail_fires_from_the_sessions_own_turns(engine, catalog, api_config):
    turns = [
        {"index": 0, "tokens": 1000, "files_changed": 1},
        {"index": 1, "tokens": 500, "files_changed": 0},
        {"index": 2, "tokens": 500, "files_changed": 0},
        {"index": 3, "tokens": 500, "files_changed": 0},
    ]
    with session_scope() as s:
        _seed(s, "tail-1", "both", turns=turns, info={"carry": {}, "resume_cost": {}})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "tail-1")

    finding = next(f for f in result["findings"] if f["finding_id"] == "long_tail")
    assert finding["usd_micros"] is None
    assert finding["basis"] == "recoverable"


def test_findings_interrupted_is_recoverable_at_the_folds_priced_total(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "int-1", "both", info={
            "carry": {}, "resume_cost": {},
            "interrupted": {"count": 2, "total_tokens": 3000, "total_usd_micros": 42_000},
        })
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "int-1")

    finding = next(f for f in result["findings"] if f["finding_id"] == "interrupted")
    assert finding["basis"] == "recoverable"
    assert finding["usd_micros"] == 42_000
    assert finding["action"]


def test_findings_interrupted_is_unpriced_when_the_fold_has_no_total(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "int-2", "both", info={
            "carry": {}, "resume_cost": {}, "interrupted": {"count": 1, "total_tokens": 500},
        })
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "int-2")

    finding = next(f for f in result["findings"] if f["finding_id"] == "interrupted")
    assert finding["usd_micros"] is None


def test_findings_ranked_by_usd_micros_with_unpriced_ones_last(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "ranked-1", "both", last_event_at=NOW, info={
            "carry": {}, "resume_cost": {"projected": {
                "context_tokens": 50_000, "ttl_s": 3600, "usd_micros": 100_000}},
            "interrupted": {"count": 1, "total_tokens": 500},
            "read_then_discard": {"total_est_tokens": 1_000},
        })

    later = NOW + timedelta(hours=2)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "ranked-1", now=later)

    ids = [f["finding_id"] for f in result["findings"]]
    assert ids[0] == "resume"                      # the only priced (100,000 micros) finding
    assert set(ids[1:]) == {"interrupted", "read_then_discard"}


# ---------------------------------------------------------------- D1: view=economics

def test_economics_block_absent_without_the_view_param(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "eco-base-1", "both", info={"segments": []})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-base-1")
    assert "economics" not in result


def test_economics_unknown_view_raises_bad_request(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "eco-bad-1", "both", info={})
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            service.session_detail(s, catalog, api_config, "eco-bad-1", view="bogus")


def test_economics_otel_only_is_unavailable_not_cold_start(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "eco-otel-1", "otel_only", status="otel_only")
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-otel-1", view="economics")
    for field in ("model_fit", "segments", "channels", "practices", "lifecycle", "context_curve"):
        assert_unavailable(result["economics"][field], "jsonl")


def test_economics_cold_start_before_r4_r5_segments_land(engine, catalog, api_config):
    with session_scope() as s:
        _seed(s, "eco-cold-1", "both", info={})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-cold-1", view="economics")
    assert result["economics"]["model_fit"] is None
    assert result["economics"]["segments"] is None       # cold-start, not []
    assert result["economics"]["lifecycle"] is None
    assert result["economics"]["context_curve"] is None


def test_economics_context_curve_promoted_from_info_when_folded(engine, catalog, api_config):
    """D1-context-curve: once `graph_fold.context_curve_of` has written the per-turn series into
    `info`, the economics block surfaces it verbatim (same promote-what-the-fold-stored pattern
    as `segments`/`practices`). Absent from `info` stays honestly `None` (the cold-start test
    above); present flows straight through."""
    curve = [
        {"index": 1, "ts": "2026-08-01T00:00:00Z", "context_tokens": 30_000, "compaction": False},
        {"index": 2, "ts": "2026-08-01T00:05:00Z", "context_tokens": 52_000, "compaction": True},
    ]
    with session_scope() as s:
        _seed(s, "eco-curve-1", "both", info={"context_curve": curve})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-curve-1", view="economics")
    assert result["economics"]["context_curve"] == curve


def test_economics_lifecycle_from_the_sessions_linked_pr(engine, catalog, api_config):
    """D1-lifecycle: when the correlator has linked the session to a PR, the economics block
    assembles build->review->rework->ship milestones from the session's own start plus that PR's
    open/first-review/merge timestamps and its reopen transitions (`git_artifact_events`). An
    unlinked session stays `None` (the cold-start test above already covers that)."""
    opened = datetime(2026, 8, 2, tzinfo=timezone.utc)
    reviewed = datetime(2026, 8, 3, tzinfo=timezone.utc)
    reopened = datetime(2026, 8, 4, tzinfo=timezone.utc)
    merged = datetime(2026, 8, 5, tzinfo=timezone.utc)
    pr_id = "github:7:pr:42"
    with session_scope() as s:
        _seed(s, "eco-lc-1", "both")   # started_at == NOW (2026-08-01)
        s.add(GitArtifact(artifact_id=pr_id, kind="pr", repo_id="github:7", org_id="org-acme",
                          number=42, is_bot=False, state="merged", title="ship it",
                          created_at=opened, updated_at=merged, merged_at=merged, stats={}))
        s.add(GitArtifact(artifact_id="github:7:review:9", kind="review", repo_id="github:7",
                          org_id="org-acme", parent_artifact_id=pr_id, is_bot=False,
                          state="approved", created_at=reviewed, updated_at=reviewed))
        s.add(GitArtifactEvent(artifact_id=pr_id, action="reopened", to_state="open",
                               occurred_at=reopened, source_key=f"{pr_id}:reopened:1",
                               recorded_at=reopened))
        s.add(SessionGitLink(session_id="eco-lc-1", artifact_id=pr_id, method="sha",
                             confidence=1, signals={}, created_at=opened))
        s.flush()
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-lc-1", view="economics")
    lc = result["economics"]["lifecycle"]
    assert lc is not None
    assert lc["session_started_at"] == NOW.isoformat()
    assert lc["first_pr_opened_at"] == opened.isoformat()
    assert lc["first_review_at"] == reviewed.isoformat()
    assert lc["reopened_at"] == [reopened.isoformat()]   # rework span anchor from the event log
    assert lc["last_merged_at"] == merged.isoformat()


def test_economics_lifecycle_reopened_empty_when_no_transition_events(engine, catalog, api_config):
    """A linked PR that never reopened (or predates the event log) reports an empty rework list,
    not a guessed one -- honest absence, same discipline as the rest of this block."""
    opened = datetime(2026, 8, 2, tzinfo=timezone.utc)
    pr_id = "github:8:pr:7"
    with session_scope() as s:
        _seed(s, "eco-lc-2", "both")
        s.add(GitArtifact(artifact_id=pr_id, kind="pr", repo_id="github:8", org_id="org-acme",
                          number=7, is_bot=False, state="open", title="wip",
                          created_at=opened, updated_at=opened, stats={}))
        s.add(SessionGitLink(session_id="eco-lc-2", artifact_id=pr_id, method="sha",
                             confidence=1, signals={}, created_at=opened))
        s.flush()
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-lc-2", view="economics")
    assert result["economics"]["lifecycle"]["reopened_at"] == []


def test_economics_golden_session_s1_over_reproduces_verdict_and_segment_fit(
        engine, catalog, api_config, pricer):
    """D1 Verify: the `s1` golden (mock.ts's `sessionDetail`) reproduces its `over` verdict
    and per-segment `fit`, end to end through `session_detail(view="economics")`.

    Segment 0 is genuinely mechanical (MISC, no beats) -- `over`. Segment 1 is an IMPLEMENT
    that itself needed a rework loop -- a high-demand signal (model-fit-reasoning-demand-
    rework plan §3.1), so the session's `high` tier is a match, not oversized -- `good`. (The
    pre-rework golden used a bare COMPREHEND/IMPLEMENT pair; COMPREHEND is now a high-demand
    label by itself and would never flag `over`, which is exactly the bug this plan fixes.)
    """
    segments = [
        {"index": 0, "label": "MISC", "beats": {}, "cost_usd_micros": 6_000_000},
        {"index": 1, "label": "IMPLEMENT", "beats": {"revision_loop": 1}, "cost_usd_micros": 24_000_000},
    ]
    with session_scope() as s:
        _seed(s, "s1", "both", models={"claude-opus-5": {"input": 100_000, "output": 20_000}},
             info={"segments": segments})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "s1", pricing=pricer,
                                        view="economics")

    economics = result["economics"]
    assert economics["model_fit"]["verdict"] == "over"
    assert economics["model_fit"]["savings_usd_micros"] > 0
    assert [seg["fit"] for seg in economics["segments"]] == ["over", "good"]
    # the reshaped segment view still carries the base allowlisted fields
    assert economics["segments"][0]["cost_usd_micros"] == 6_000_000


def test_economics_golden_session_s2_under_reproduces_verdict_and_segment_fit(engine, catalog, api_config):
    """D1 Verify: the `s2` golden (mock.ts's `sessionReworkDetail`) reproduces its `under`
    verdict and per-segment `fit`."""
    segments = [
        {"index": 0, "label": "IMPLEMENT", "beats": {"revision_loop": 1}, "cost_usd_micros": 28_000_000},
        {"index": 1, "label": "BUG_FIX", "beats": {}, "cost_usd_micros": 18_000_000},
    ]
    with session_scope() as s:
        _seed(s, "s2", "both", models={"claude-haiku-4-5-20251001": {"input": 50_000, "output": 8_000}},
             info={"segments": segments})
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "s2", view="economics")

    economics = result["economics"]
    assert economics["model_fit"]["verdict"] == "under"
    assert economics["model_fit"]["savings_usd_micros"] == 0
    assert [seg["fit"] for seg in economics["segments"]] == ["under", "good"]


def test_economics_channels_reuses_the_same_by_channel_block_as_cost(engine, catalog, api_config):
    info = {
        "segments": [],
        "carry": {"cache_read_tokens": 1_000, "carry_usd_micros": 10_000},
        "channel_cost": {
            "recache": {"tokens": 200, "usd_micros": 2_000},
            "output": {"tokens": 500, "usd_micros": 5_000},
            "input": {"tokens": 100, "usd_micros": 1_000},
        },
    }
    with session_scope() as s:
        _seed(s, "eco-chan-1", "both", info=info)
    with session_scope() as s:
        result = service.session_detail(s, catalog, api_config, "eco-chan-1", view="economics")
    assert result["economics"]["channels"] == result["cost"]["by_channel"]
