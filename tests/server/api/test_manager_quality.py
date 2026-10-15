"""Phase 2: F4 (feature boundary key) / X1 (acceptance-cost) / X2 (quality 2x2)
(`server/api/manager/quality.py`).

`list_features_for_repo` takes git-source's own numeric repo_id scheme
(`github:<repo_num>`, `map_github.py`) -- the same one `git_artifacts`/`pr_issue_links` are
actually keyed on -- not the session-side `github:owner/name` scheme F3/A3 group on. Callers
resolve the bridge first (`aggregate.resolve_git_repo_id`); this module's own tests use the
numeric scheme directly so a fixture bug here can't hide the same repo-identity mismatch that
`test_manager_aggregate.py::test_resolve_git_repo_id*` covers on the bridge itself.
"""
from datetime import datetime, timedelta, timezone

from server.api.config import ApiConfig
from server.api.manager import quality
from server.storage import session_scope
from server.storage.schema import GitArtifact, GitRepo, PrIssueLink
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionGitLink, SessionInfo

from .conftest import requires_db

REPO_NUM = 999
REPO = f"github:{REPO_NUM}"          # git-source's own scheme -- what git_artifacts is keyed on
PROJECT_KEY = str(REPO_NUM)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)


def _artifact_id(kind: str, key) -> str:
    return f"github:{REPO_NUM}:{kind}:{key}"


def _pr(number, state="merged", created=MID, merged=MID, user_id="csv:dev1",
       branch=None, labels=None):
    artifact_id = _artifact_id("pr", number)
    return GitArtifact(artifact_id=artifact_id, kind="pr", repo_id=REPO, org_id="default",
                       number=number, user_id=user_id, is_bot=False, state=state,
                       created_at=created, updated_at=created, merged_at=merged,
                       stats={"comment_count": 3}, branch=branch, labels=labels or [])


def _issue(number, title="Fix the thing", state="closed"):
    artifact_id = _artifact_id("issue", number)
    return GitArtifact(artifact_id=artifact_id, kind="issue", repo_id=REPO, org_id="default",
                       number=number, user_id=None, is_bot=False, state=state,
                       title=title, created_at=MID, updated_at=MID)


def _review(number, pr_number, state, ts):
    return GitArtifact(artifact_id=_artifact_id("review", number), kind="review", repo_id=REPO,
                       org_id="default", user_id=None, is_bot=False, state=state,
                       created_at=ts, updated_at=ts, parent_artifact_id=_artifact_id("pr", pr_number))


def _session(session_id, cost, info=None):
    return (
        SessionRow(session_id=session_id, producer_id="p", org_id="default", user_id="csv:dev1",
                  agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                  ended_at=MID, signal_blob={}, info_blob={}, subagents=[]),
        SessionInfo(session_id=session_id, created_at=MID, updated_at=MID, models={}, tools={},
                   files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                   long_turn_count=0, info=info or {}, otel={}, datasource="both",
                   cost_effective_usd_micros=cost),
    )


# ------------------------------------------------------------------------------- F4

@requires_db
def test_list_features_for_repo_issue_with_merged_pr(engine, catalog):
    with session_scope() as s:
        s.add(_issue(10, title="Add rate limiting"))
        s.add(_pr(20))
        s.add(PrIssueLink(pr_artifact_id=_artifact_id("pr", 20), tracker="github",
                          project_key=PROJECT_KEY, number=10,
                          issue_artifact_id=_artifact_id("issue", 10), method="graphql",
                          confidence=1, created_at=MID))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0] == {
        "id": _artifact_id("issue", 10),
        "issue_key": "#10", "title": "Add rate limiting",
        "spend": {"usd_micros": 0, "cost_basis": "cost_effective", "cost_complete": None},
        "merged_prs": 1, "status": "shipped",
    }


@requires_db
def test_list_features_for_repo_issue_with_no_pr_is_still_a_feature(engine, catalog):
    with session_scope() as s:
        s.add(_issue(11, title="Unstarted work", state="open"))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == _artifact_id("issue", 11)
    assert features[0]["issue_key"] == "#11"
    assert features[0]["merged_prs"] == 0
    assert features[0]["status"] == "in-progress"  # not_started collapses to in-progress


@requires_db
def test_list_features_for_repo_orphan_pr_falls_back_to_single_pr_feature(engine, catalog):
    with session_scope() as s:
        s.add(_pr(21))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"pr:{_artifact_id('pr', 21)}"
    assert features[0]["issue_key"] == "#21"
    assert features[0]["status"] == "shipped"


@requires_db
def test_list_features_for_repo_is_empty_for_a_non_github_repo(engine, catalog):
    with session_scope() as s:
        assert quality.list_features_for_repo(s, catalog, "local:some-repo") == []


# --------------------------------------------------------------------- H5: branch/label clustering

@requires_db
def test_orphan_prs_on_the_same_branch_cluster_into_one_feature(engine, catalog):
    with session_scope() as s:
        s.add(_pr(22, branch="fix-thing", labels=[]))
        s.add(_pr(23, branch="fix-thing", labels=[]))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"branch:{REPO_NUM}:fix-thing"
    assert features[0]["issue_key"] == "fix-thing"
    assert features[0]["merged_prs"] == 2


@requires_db
def test_orphan_prs_sharing_a_label_with_no_branch_cluster_into_one_feature(engine, catalog):
    with session_scope() as s:
        s.add(_pr(24, branch=None, labels=["bug"]))
        s.add(_pr(25, branch=None, labels=["bug"]))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"label:{REPO_NUM}:bug"
    assert features[0]["issue_key"] == "bug"
    assert features[0]["merged_prs"] == 2


@requires_db
def test_orphan_pr_on_the_default_branch_falls_back_to_single_pr_feature(engine, catalog):
    """Every direct-to-main PR shares the default branch -- clustering on it would collapse
    the whole repo's orphan PRs into one bogus feature, so it's excluded (H5's own rule)."""
    with session_scope() as s:
        s.add(GitRepo(repo_id=REPO, repo_num=REPO_NUM, full_name="acme/api", owner="acme",
                      default_branch="main", private=False, installation_id=1, org_id="default",
                      backfill_state="done", first_seen_at=MID, updated_at=MID))
        s.add(_pr(26, branch="main", labels=[]))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"pr:{_artifact_id('pr', 26)}"


@requires_db
def test_orphan_pr_on_an_unknown_default_falls_back_to_the_known_defaults_set(engine, catalog):
    """No `git_repos` row at all (no GitHub App installation on record) -- falls back to the
    literal `main`/`master` rather than clustering on a branch that's almost certainly the
    repo's own default."""
    with session_scope() as s:
        s.add(_pr(27, branch="master", labels=[]))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"pr:{_artifact_id('pr', 27)}"


@requires_db
def test_orphan_pr_with_neither_branch_nor_label_falls_back_to_single_pr_feature(engine, catalog):
    with session_scope() as s:
        s.add(_pr(28, branch=None, labels=[]))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"pr:{_artifact_id('pr', 28)}"


@requires_db
def test_clustered_feature_cost_sums_its_member_prs_sessions(engine, catalog):
    with session_scope() as s:
        s.add(_pr(29, branch="feat-x", labels=[]))
        s.add(_pr(31, branch="feat-x", labels=[]))
        s1, i1 = _session("s-h5-1", 5_000_000)
        s2, i2 = _session("s-h5-2", 7_000_000)
        s.add_all([s1, i1, s2, i2])
        s.add(SessionGitLink(session_id="s-h5-1", artifact_id=_artifact_id("pr", 29),
                             method="sha", confidence=1.0, signals={}, created_at=MID))
        s.add(SessionGitLink(session_id="s-h5-2", artifact_id=_artifact_id("pr", 31),
                             method="sha", confidence=1.0, signals={}, created_at=MID))
    with session_scope() as s:
        features = quality.list_features_for_repo(s, catalog, REPO)
    assert len(features) == 1
    assert features[0]["id"] == f"branch:{REPO_NUM}:feat-x"
    assert features[0]["spend"]["usd_micros"] == 12_000_000
    assert features[0]["status"] == "shipped"   # both member PRs merged (the `_pr` default)


@requires_db
def test_feature_by_id_round_trips_a_branch_cluster(engine, catalog):
    with session_scope() as s:
        s.add(_pr(32, branch="fix-thing", labels=[]))
        s.add(_pr(33, branch="fix-thing", labels=[]))
    with session_scope() as s:
        f = quality.feature_by_id(s, catalog, f"branch:{REPO_NUM}:fix-thing")
    assert f is not None
    assert sorted(f.member_pr_ids) == sorted([_artifact_id("pr", 32), _artifact_id("pr", 33)])
    assert f.min_confidence == 0.5


@requires_db
def test_feature_by_id_round_trips_a_label_cluster(engine, catalog):
    with session_scope() as s:
        s.add(_pr(34, branch=None, labels=["bug"]))
    with session_scope() as s:
        f = quality.feature_by_id(s, catalog, f"label:{REPO_NUM}:bug")
    assert f is not None
    assert f.member_pr_ids == [_artifact_id("pr", 34)]


@requires_db
def test_feature_by_id_is_none_for_a_cluster_that_no_longer_forms(engine, catalog):
    """The PR that used to form this cluster has since been issue-linked (or moved branch) --
    the id fails closed rather than resolving to a stale grouping."""
    with session_scope() as s:
        s.add(_pr(35, branch=None, labels=[]))
    with session_scope() as s:
        assert quality.feature_by_id(s, catalog, f"branch:{REPO_NUM}:fix-thing") is None


# ------------------------------------------------------------------------------- X1

@requires_db
def test_pr_acceptance_metrics(engine, catalog):
    pr_id = _artifact_id("pr", 30)
    with session_scope() as s:
        s.add(_pr(30, merged=MID + timedelta(days=2)))
        s.add(_review(1, 30, "CHANGES_REQUESTED", MID + timedelta(hours=5)))
        s.add(_review(2, 30, "CHANGES_REQUESTED", MID + timedelta(hours=20)))
        s.add(_review(3, 30, "APPROVED", MID + timedelta(hours=30)))
    with session_scope() as s:
        metrics = quality.pr_acceptance_metrics(s, catalog, [pr_id])
    m = metrics[pr_id]
    assert m["review_rounds"] == 2
    assert m["time_to_first_review_hours"] == 5
    assert m["time_to_merge_days"] == 2
    assert m["comment_count"] == 3


@requires_db
def test_pr_acceptance_metrics_no_reviews(engine, catalog):
    pr_id = _artifact_id("pr", 31)
    with session_scope() as s:
        s.add(_pr(31))
    with session_scope() as s:
        metrics = quality.pr_acceptance_metrics(s, catalog, [pr_id])
    m = metrics[pr_id]
    assert m["review_rounds"] == 0
    assert m["time_to_first_review_hours"] is None


# ------------------------------------------------------------------------------- X2

CFG = ApiConfig()   # default X2 thresholds: making p75/90d/min 5 PRs, review_rounds 1, reopens 1, rework 0.5


def _acc(review_rounds=0, reopens=None, rework_ratio=None):
    return {"review_rounds": review_rounds, "reopens": reopens, "rework_ratio": rework_ratio}


def test_quality_label_matrix():
    # review_rounds is the acceptance signal here; the other two absent (None).
    assert quality.quality_label(False, _acc(review_rounds=0), CFG) == "Clean"
    assert quality.quality_label(True, _acc(review_rounds=0), CFG) == "Heavy lift"
    assert quality.quality_label(False, _acc(review_rounds=1), CFG) == "Quick & dirty"
    assert quality.quality_label(True, _acc(review_rounds=1), CFG) == "Costly rework"
    # making-cost axis unmeasurable -> None (never a guessed quadrant).
    assert quality.quality_label(None, _acc(review_rounds=1), CFG) is None


def test_quality_label_composite_reopens_and_rework_trip_acceptance():
    # Any one acceptance signal at/above its cutoff marks messy acceptance, review rounds aside.
    assert quality.quality_label(False, _acc(reopens=1), CFG) == "Quick & dirty"        # reopen alone
    assert quality.quality_label(False, _acc(rework_ratio=0.5), CFG) == "Quick & dirty" # rework alone
    assert quality.quality_label(True, _acc(reopens=2), CFG) == "Costly rework"


def test_quality_label_uncaptured_signals_never_flip_a_clean_pr():
    # reopens/rework None (not captured) contribute nothing -- a clean, low-making PR stays Clean.
    assert quality.quality_label(False, _acc(review_rounds=0, reopens=None, rework_ratio=None), CFG) == "Clean"
    # a captured-but-zero reopen and a below-cutoff rework also stay Clean.
    assert quality.quality_label(False, _acc(reopens=0, rework_ratio=0.1), CFG) == "Clean"


def test_quality_label_thresholds_are_config_driven():
    strict = ApiConfig.model_validate({"quality": {"rework_ratio_high": 0.9, "reopens_high": 3}})
    # rework 0.5 no longer trips under a 0.9 cutoff; a single reopen no longer trips under 3.
    assert quality.quality_label(False, _acc(rework_ratio=0.5), strict) == "Clean"
    assert quality.quality_label(False, _acc(reopens=1), strict) == "Clean"
    assert quality.quality_label(False, _acc(rework_ratio=0.95), strict) == "Quick & dirty"


def _priced_pr(s, number, cost_micros, merged, created=None):
    """A PR linked to one session of the given cost (`cost_micros=None`: no linked session).
    `merged=None` makes it an open PR created at `created`."""
    pr_id = _artifact_id("pr", number)
    created = created or merged
    s.add(_pr(number, state="merged" if merged else "open", created=created, merged=merged))
    if cost_micros is not None:
        session, info = _session(f"s-{number}", cost_micros)
        s.add(session)
        s.add(info)
        s.add(SessionGitLink(session_id=f"s-{number}", artifact_id=pr_id, method="trailer",
                             confidence=1, signals={}, created_at=created))
    return pr_id


@requires_db
def test_making_cost_is_high_at_or_above_the_repos_75th_percentile(engine, catalog):
    """Five priced PRs merged a day apart. Anchored at each PR's own merge, only the last one sees all
    five: p75 of [$1, $2, $3, $4, $10] is $4 (linear), and its $10 is above it. Nothing about how much
    of that spend was recoverable enters into it."""
    with session_scope() as s:
        ids = {usd: _priced_pr(s, 40 + i, usd * 1_000_000, MID + timedelta(days=i))
               for i, usd in enumerate([1, 2, 3, 4, 10])}
    with session_scope() as s:
        result = quality.making_cost_high(s, catalog, CFG, list(ids.values()))
    assert result[ids[10]] is True
    assert all(result[ids[usd]] is None for usd in (1, 2, 3, 4))  # < 5 priced PRs up to their merge


@requires_db
def test_making_cost_ranks_an_open_pr_against_every_merged_pr_so_far(engine, catalog):
    with session_scope() as s:
        for i, usd in enumerate([1, 2, 3, 4, 10]):
            _priced_pr(s, 50 + i, usd * 1_000_000, MID + timedelta(days=i))
        cheap = _priced_pr(s, 60, 3_000_000, None, created=MID + timedelta(days=10))
        pricey = _priced_pr(s, 61, 9_000_000, None, created=MID + timedelta(days=10))
    now = MID + timedelta(days=20)
    with session_scope() as s:
        result = quality.making_cost_high(s, catalog, CFG, [cheap, pricey], now=now)
    # open PRs anchor at `now` and rank against the merged ones: p75 of [1, 2, 3, 4, 10] = $4
    assert result == {cheap: False, pricey: True}


@requires_db
def test_making_cost_window_excludes_prs_merged_before_the_trailing_days(engine, catalog):
    with session_scope() as s:
        for i, usd in enumerate([1, 2, 3, 4]):
            _priced_pr(s, 70 + i, usd * 1_000_000, MID - timedelta(days=200 + i))  # out of window
        subject = _priced_pr(s, 75, 10_000_000, MID)
    with session_scope() as s:
        assert quality.making_cost_high(s, catalog, CFG, [subject])[subject] is None


@requires_db
def test_making_cost_is_none_with_no_linked_sessions(engine, catalog):
    with session_scope() as s:
        pr_id = _priced_pr(s, 32, None, MID)
    with session_scope() as s:
        assert quality.making_cost_high(s, catalog, CFG, [pr_id])[pr_id] is None


@requires_db
def test_making_cost_min_prs_is_config_driven(engine, catalog):
    loose = ApiConfig.model_validate({"quality": {"making_cost_min_prs": 1}})
    with session_scope() as s:
        pr_id = _priced_pr(s, 33, 5_000_000, MID)
    with session_scope() as s:
        assert quality.making_cost_high(s, catalog, CFG, [pr_id])[pr_id] is None
        assert quality.making_cost_high(s, catalog, loose, [pr_id])[pr_id] is True  # it is its own p75


@requires_db
def test_quality_counts_for_prs_omits_unclassifiable_prs(engine, catalog):
    with session_scope() as s:
        pr_unclassified = _priced_pr(s, 34, None, MID)  # no linked sessions -- unclassifiable
        peers = [_priced_pr(s, 80 + i, 10_000_000, MID - timedelta(days=i + 1)) for i in range(4)]
        pr_clean = _priced_pr(s, 35, 1_000_000, MID)
    with session_scope() as s:
        counts = quality.quality_counts_for_prs(s, catalog, ApiConfig(), [pr_unclassified, pr_clean])
    assert counts == [{"label": "Clean", "count": 1}]  # pr_unclassified omitted; pr_clean is the cheapest


# ------------------------------------------------------------------------------- D3: feature_by_id

@requires_db
def test_feature_by_id_resolves_an_issue_keyed_feature(engine, catalog):
    with session_scope() as s:
        s.add(_issue(40, title="Ship the widget"))
    with session_scope() as s:
        f = quality.feature_by_id(s, catalog, _artifact_id("issue", 40))
    assert f is not None
    assert f.ref.number == 40
    assert f.issue["title"] == "Ship the widget"


@requires_db
def test_feature_by_id_resolves_a_single_pr_fallback_feature(engine, catalog):
    with session_scope() as s:
        s.add(_pr(41))
    with session_scope() as s:
        f = quality.feature_by_id(s, catalog, f"pr:{_artifact_id('pr', 41)}")
    assert f is not None
    assert f.member_pr_ids == [_artifact_id("pr", 41)]


@requires_db
def test_feature_by_id_is_none_for_a_pr_id_that_does_not_exist(engine, catalog):
    with session_scope() as s:
        assert quality.feature_by_id(s, catalog, "pr:github:999:pr:9999") is None


@requires_db
def test_feature_by_id_is_none_for_an_issue_number_with_no_evidence_at_all(engine, catalog):
    """A caller-supplied issue number that was never pulled and has no PR ever linked to it
    isn't a real feature -- fail closed (404), don't fabricate one from a bare number."""
    with session_scope() as s:
        assert quality.feature_by_id(s, catalog, _artifact_id("issue", 99999)) is None


def test_feature_by_id_is_none_for_an_unrecognized_shape():
    assert quality.feature_by_id(None, None, "PLAT-214") is None
    assert quality.feature_by_id(None, None, "bogus") is None


def test_feature_id_round_trips_through_the_view():
    """The `_feature_id`/`feature_by_id` pair is symmetric by construction -- lock that in
    directly, since D3's whole route depends on the id `list_features_for_repo` hands out
    being the same one `feature_by_id` accepts back."""
    from server.ingest.git.features import Feature as GitFeature
    from server.ingest.git.issue_ref import IssueRef

    issue_feature = GitFeature(ref=IssueRef("github", "999", 40, "github:999:issue:40"),
                              issue=None, status="in_progress")
    assert quality._feature_id(issue_feature) == "github:999:issue:40"

    pr_feature = GitFeature(ref=IssueRef("github", "999", 41, None), issue=None,
                            status="shipped", member_pr_ids=["github:999:pr:41"])
    assert quality._feature_id(pr_feature) == "pr:github:999:pr:41"
