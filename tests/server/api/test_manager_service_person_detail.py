"""A5 GET /v1/people/{id} -- service-level (`server/api/manager/service.py::person_detail`).

Plan Verify: "decomposition reconciles to cost_usd; velocity goods/times match PR fixture."
"""
from datetime import datetime, timezone

from server.api.config import ApiConfig
from server.api.manager.service import person_detail
from server.directory import DirectoryRecord, ExternalKey, TeamMembership, TeamRecord, sync
from server.storage import session_scalars, session_scope
from server.storage.schema import GitArtifact
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionGitLink, SessionInfo

from .conftest import requires_db

#: The fixture repo holds a single priced PR; the default 5-PR floor would leave it unlabeled.
_ONE_PR_REPO = ApiConfig.model_validate({"quality": {"making_cost_min_prs": 1}})

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            display_name="Dev One", teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev2"),), email="dev2@x.com",
                            display_name="Dev Two", teams=(TeamMembership("platform"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform")],
    )
    s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="csv:dev1",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, datasource="both", cost_effective_usd_micros=40_000_000,
                      info={
                          "title": "Fix session cookie expiry",
                          "channel_cost": {"output": {"usd_micros": 20_000_000}, "recache": {"usd_micros": 5_000_000}},
                          "carry": {"carry_usd_micros": 3_000_000},
                      }, otel={}))
    # dev2: cheap peer (1 merged PR at low cost)
    s.add(SessionRow(session_id="s2", producer_id="p", org_id="default", user_id="csv:dev2",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s2", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, datasource="both", cost_effective_usd_micros=5_000_000,
                      info={}, otel={}))
    # A5-peer-task-mix: both PRs carry line stats above the default 200-line floor, so
    # $/KLOC -- not raw spend -- decides who's "cheapest" (dev2: $5/250 lines = $20k/KLOC;
    # dev1: $40/300 lines = ~$133k/KLOC).
    s.add(GitArtifact(artifact_id="pr-dev1", kind="pr", repo_id="github:acme/api", org_id="default",
                      user_id="csv:dev1", is_bot=False, state="merged", created_at=MID,
                      updated_at=MID, merged_at=MID, title="dev1's PR",
                      stats={"additions": 300, "deletions": 0}))
    s.add(GitArtifact(artifact_id="pr-dev2", kind="pr", repo_id="github:acme/api", org_id="default",
                      user_id="csv:dev2", is_bot=False, state="merged", created_at=MID,
                      updated_at=MID, merged_at=MID, stats={"additions": 250, "deletions": 0}))
    s.flush()


@requires_db
def test_decomposition_reconciles_to_cost(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)

    person = result["data"]["person"]
    assert person["spend"]["usd_micros"] == 40_000_000
    reconciled = sum(h["recoverable_usd_micros"] for h in person["habits"])
    assert reconciled == person["recoverable"]["usd_micros"]
    assert reconciled <= person["spend"]["usd_micros"]
    # X2 (Phase 2): dev1's one merged PR has no linked session (test_manager_quality.py covers
    # the classified cases), so it's omitted as unclassifiable -- an empty list is still the
    # honest "X2 ran, nothing classified" answer, distinct from the pre-X2 `None` cold-start.
    assert person["work_quality"] == []


@requires_db
def test_channels_roll_up_this_persons_own_sessions(engine, catalog):
    """G2 (mvp-gap-closure-plan): `aggregate.channel_totals`, already used by D2's
    `PRCost.channels`, generalized to a person's own sessions in scope."""
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)

    channels = result["data"]["person"]["channels"]
    assert channels == {
        "carry_usd_micros": 3_000_000, "recache_usd_micros": 5_000_000,
        "output_usd_micros": 20_000_000, "input_usd_micros": 0, "complete": True,
    }


@requires_db
def test_velocity_matches_the_pr_fixture(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)

    assert sum(v["merged_prs"] for v in result["data"]["velocity"]) == 1
    assert len(result["data"]["recent_prs"]) == 1
    assert result["data"]["recent_prs"][0]["artifact_id"] == "pr-dev1"


@requires_db
def test_work_quality_classifies_a_pr_with_a_linked_session(engine, catalog):
    """X2 (Phase 2), the classified case -- dev1's PR linked to its session is labelled. The repo
    has only this one priced PR, so the min-PR floor is loosened to 1: it is then its own p75
    (making cost high) with 0 review rounds -> "Heavy lift"."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionGitLink(session_id="s1", artifact_id="pr-dev1", method="trailer",
                             confidence=1, signals={}, created_at=MID))
    with session_scope() as s:
        result = person_detail(s, catalog, _ONE_PR_REPO, "csv:dev1", FRM, TO)
    assert result["data"]["person"]["work_quality"] == [{"label": "Heavy lift", "count": 1}]


@requires_db
def test_work_quality_omits_a_pr_its_repo_is_too_thin_to_rank(engine, catalog):
    """Under the default 5-PR floor a one-PR repo can't rank its PR -- omitted, never guessed."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionGitLink(session_id="s1", artifact_id="pr-dev1", method="trailer",
                             confidence=1, signals={}, created_at=MID))
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)
    assert result["data"]["person"]["work_quality"] == []


@requires_db
def test_recent_sessions_carries_the_linked_prs_quality(engine, catalog):
    """G1 (mvp-gap-closure-plan): s1 links to pr-dev1 (merged, "Heavy lift" per the test above)
    -- its row in `recent_sessions` should carry that PR's quality and count it as merged. s2
    has no link at all, so it stays unclassifiable (`quality: None`, `merged_prs: 0`), never
    guessed."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionGitLink(session_id="s1", artifact_id="pr-dev1", method="trailer",
                             confidence=1, signals={}, created_at=MID))
    with session_scope() as s:
        result = person_detail(s, catalog, _ONE_PR_REPO, "csv:dev1", FRM, TO)

    by_id = {r["session_id"]: r for r in result["data"]["recent_sessions"]}
    assert by_id["s1"]["merged_prs"] == 1
    assert by_id["s1"]["quality"] == "Heavy lift"
    assert by_id["s1"]["cost_usd_micros"] == 40_000_000
    assert by_id["s1"]["duration_minutes"] == 0
    # the causal graph fold already lifts the vendor's ai_title into session_info.info.title;
    # this was never read into a session reference before (see PRSessionRef's docstring)
    assert by_id["s1"]["title"] == "Fix session cookie expiry"
    # dev1's own scope never includes s2 (that session belongs to dev2) -- confirms the table
    # is scoped the same way every other person_detail block already is.
    assert "s2" not in by_id


@requires_db
def test_recent_sessions_excludes_subagent_sessions(engine, catalog):
    """A subagent (Task-tool) session is its own row in `sessions`/`session_info` -- its cost
    must still count toward the person's aggregate spend -- but it is not a session this
    person ever started themselves, so it must not show up as its own entry in the "recent
    sessions" list (same reasoning as the A4 dashboard list's `parent_session_id IS NULL`
    filter). Regression for the 2026-09-13 anomaly: two Explore-subagent sessions surfaced in
    PersonScreen's "Recent sessions" table as if they were ordinary top-level sessions."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionRow(session_id="s1-sub", producer_id="p", org_id="default", user_id="csv:dev1",
                         agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                         ended_at=MID, signal_blob={}, info_blob={}, subagents=[],
                         parent_session_id="s1"))
        s.add(SessionInfo(session_id="s1-sub", created_at=MID, updated_at=MID, models={}, tools={},
                          files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                          long_turn_count=0, datasource="jsonl_only", cost_effective_usd_micros=1_000_000,
                          info={}, otel={}))
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)

    session_ids = {r["session_id"] for r in result["data"]["recent_sessions"]}
    assert "s1-sub" not in session_ids
    assert "s1" in session_ids
    # the subagent's spend still counts toward the person's own aggregate total
    assert result["data"]["person"]["spend"]["usd_micros"] == 41_000_000


@requires_db
def test_recent_sessions_carries_reasoning_tokens(engine, catalog):
    """F5: a session's own `info.reasoning_tokens` + `tokens_total` ride along on its
    recent-sessions row -- raw numbers, not a pre-formatted percentage (strip-rule 2, same as
    `duration_minutes`)."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionRow(session_id="s1-reasoning", producer_id="p", org_id="default",
                         user_id="csv:dev1", agent="claude_code", status="ended",
                         started_at=MID, last_event_at=MID, ended_at=MID, signal_blob={},
                         info_blob={}, subagents=[]))
        info = {"reasoning_tokens": 1_500}
        s.add(SessionInfo(session_id="s1-reasoning", created_at=MID, updated_at=MID, models={},
                          tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                          turn_count=0, long_turn_count=0, datasource="both",
                          tokens_total=10_000, info=info, otel={},
                          **session_scalars.fold_scalars(info, {})))
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)

    by_id = {r["session_id"]: r for r in result["data"]["recent_sessions"]}
    assert by_id["s1-reasoning"]["reasoning_tokens"] == 1_500
    assert by_id["s1-reasoning"]["tokens_total"] == 10_000
    # s1 (the shared fixture) was folded before this rollup existed -- None, not 0, is the
    # honest "never estimated" answer (aggregate.reasoning_share's own docstring).
    assert by_id["s1"]["reasoning_tokens"] is None
    assert by_id["s1"]["tokens_total"] == 0


def _seed_linked_prs(s, user: str, session_id: str | None, lines: list[int]) -> None:
    """Extra merged PRs for `user` (`pr-<user>-<i>`), each linked to `session_id` when given --
    `_seed`'s one PR per person is below the default 3-linked-PR peer floor on its own."""
    for i, n in enumerate(lines):
        artifact_id = f"pr-{user}-{i}"
        s.add(GitArtifact(artifact_id=artifact_id, kind="pr", repo_id="github:acme/api", org_id="default",
                          user_id=f"csv:{user}", is_bot=False, state="merged", created_at=MID,
                          updated_at=MID, merged_at=MID, stats={"additions": n, "deletions": 0}))
        if session_id:
            s.add(SessionGitLink(session_id=session_id, artifact_id=artifact_id, method="trailer",
                                 confidence=1, signals={}, created_at=MID))


@requires_db
def test_peer_compare_finds_the_cheapest_teammate_by_linked_cost_per_kloc(engine, catalog):
    """Linked work only: dev1's s1 ($40) made three PRs (300+300+300 lines), dev2's s2 ($5) made
    three (250+250+250). Each session is costed once across its PRs."""
    with session_scope() as s:
        _seed(s, catalog)
        for user, session_id, first_pr in (("dev1", "s1", "pr-dev1"), ("dev2", "s2", "pr-dev2")):
            s.add(SessionGitLink(session_id=session_id, artifact_id=first_pr, method="trailer",
                                 confidence=1, signals={}, created_at=MID))
        _seed_linked_prs(s, "dev1", "s1", [300, 300])
        _seed_linked_prs(s, "dev2", "s2", [250, 250])
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)
    peer = result["data"]["person"]["peer_compare"]
    assert peer["user_id"] == "csv:dev2"
    assert peer["name"] == "Dev Two"
    assert peer["cost_per_kloc_usd_micros"] == round(5_000_000 / 750 * 1_000)


@requires_db
def test_peer_compare_never_names_a_teammate_without_session_telemetry(engine, catalog):
    """The live bug: dev2 merges plenty of PRs but none has a linked session -- no linked work,
    so no rate, rather than $0/KLOC and "cheapest"."""
    with session_scope() as s:
        _seed(s, catalog)
        s.add(SessionGitLink(session_id="s1", artifact_id="pr-dev1", method="trailer",
                             confidence=1, signals={}, created_at=MID))
        _seed_linked_prs(s, "dev1", "s1", [300, 300])
        _seed_linked_prs(s, "dev2", None, [5_000, 5_000, 5_000])
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)
    assert result["data"]["person"]["peer_compare"] is None


@requires_db
def test_peer_compare_is_none_when_subject_below_the_volume_floor(engine, catalog):
    """Neither PR carries line stats -- cold-start, not a guessed zero-volume comparison."""
    with session_scope() as s:
        sync(
            s, catalog,
            [
                DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                                display_name="Dev One", teams=(TeamMembership("platform"),)),
            ],
            teams=[TeamRecord("platform", "default", "Platform")],
        )
        s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="csv:dev1",
                         agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                         ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                          files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                          long_turn_count=0, datasource="both", cost_effective_usd_micros=40_000_000,
                          info={}, otel={}))
        s.add(GitArtifact(artifact_id="pr-dev1", kind="pr", repo_id="github:acme/api", org_id="default",
                          user_id="csv:dev1", is_bot=False, state="merged", created_at=MID,
                          updated_at=MID, merged_at=MID))
        s.flush()
    with session_scope() as s:
        result = person_detail(s, catalog, ApiConfig(), "csv:dev1", FRM, TO)
    assert result["data"]["person"]["peer_compare"] is None
