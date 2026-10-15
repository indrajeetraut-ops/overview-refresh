"""The SHA correlator (docs/modules/pr-work-classification.md §4, §6.4, §11's test bar): a
sha link resolves; the feature→sprint→main owning-PR dedup counts cost once; the trailer
precedence path links despite what would otherwise be an unresolvable (rewritten) sha;
out-of-scope PRs are skipped; unlinked sessions are reported, not silently dropped."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlalchemy import text

from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base, Session as SessionRow
from server.worker.pr_class import correlate as correlate_mod
from server.worker.pr_class.config import PrClassConfig
from server.worker.pr_class.correlate import correlate_session, git_correlate
from tests.server.storage.conftest import requires_db

REPO_ID = "github:1"


def _config(base_branches=("main",), exclude_head=()):
    return PrClassConfig.model_validate({
        "eval_scope": {"base_branches": list(base_branches), "exclude_head_branches": list(exclude_head)},
    })


def _session_with_commit_shas(session_id: str, shas: list[str]) -> SessionRow:
    now = datetime.now(timezone.utc)
    # The REAL normalized shape: the vendor attaches `gitOperation.commit.sha` to the tool
    # RESULT (a `tool_result` event whose `payload.raw` is the verbatim vendor record), never to
    # the tool_call. An earlier version of this helper built a `tool_call`/`payload.commit`
    # shape the normalizer never emits, so the correlator query and these tests shared the same
    # wrong assumption and the mismatch went unseen until it produced zero links on real data.
    events = [
        {
            "event_type": "tool_result", "event_ts": now.isoformat(),
            "payload": {"raw": {"toolUseResult": {
                "gitOperation": {"commit": {"sha": sha, "kind": "committed"}}
            }}},
        }
        for sha in shas
    ]
    return SessionRow(
        session_id=session_id, producer_id="p", org_id=None, user_id=None, agent="claude_code",
        agent_version=None, status="ended", started_at=now, last_event_at=now, ended_at=now,
        signal_blob={"events": events}, info_blob={}, parent_session_id=None, subagents=[],
    )


def _pr_artifact(number: int, sha_list: list[str], merged_at: str, base_branch="main",
                 branch="feature", repo_id=REPO_ID, created_at: str | None = None):
    now = datetime.now(timezone.utc).isoformat()
    created_at = created_at or now
    return {
        "artifact_id": f"{repo_id}:pr:{number}", "kind": "pr", "repo_id": repo_id, "org_id": "org",
        "number": number, "sha": sha_list[-1] if sha_list else None, "author_login": None,
        "author_email": None, "user_id": None, "is_bot": False, "state": "merged", "title": "t",
        "branch": branch, "base_branch": base_branch, "created_at": created_at, "updated_at": now,
        "closed_at": merged_at, "merged_at": merged_at, "stats": None, "files": None,
        "parent_artifact_id": None, "labels": json.dumps([]), "milestone": None,
        "linked_issue": None, "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None,
        "commit_shas": json.dumps(sha_list), "info": json.dumps({}),
    }


def _commit_artifact(sha: str, repo_id=REPO_ID):
    now = datetime.now(timezone.utc).isoformat()
    return {
        "artifact_id": f"{repo_id}:commit:{sha}", "kind": "commit", "repo_id": repo_id,
        "org_id": "org", "number": None, "sha": sha, "author_login": None, "author_email": None,
        "user_id": None, "is_bot": False, "state": "pushed", "title": None, "branch": "main",
        "base_branch": None, "created_at": now, "updated_at": now, "closed_at": None,
        "merged_at": None, "stats": None, "files": None, "parent_artifact_id": None,
        "labels": json.dumps([]), "milestone": None, "linked_issue": None,
        "merge_commit_sha": None, "before_sha": None, "after_sha": None,
        "integration_method": None, "reverts_sha": None, "commit_shas": None, "info": json.dumps({}),
    }


@requires_db
def test_sha_link_resolves(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "a" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha))
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(1, [sha], "2026-01-02T00:00:00Z"))
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        row = s.execute(text(
            "SELECT artifact_id, method, confidence FROM session_git_links WHERE session_id = 's1'"
        )).one()
    assert row.artifact_id == f"{REPO_ID}:pr:1"
    assert row.method == "sha"
    assert float(row.confidence) == 1.0


@requires_db
def test_short_sha_prefix_resolves_against_the_full_commit_sha(engine, catalog):
    """Claude Code's own telemetry only ever records git's abbreviated (~7 char) hash -- in
    BOTH the vendor-provided `gitOperation.commit.sha` field and the stdout-parse fallback
    (measured against real session data, not a hypothetical) -- while `git_artifacts.sha` from
    GitHub's API is always the full 40 chars. A session's short recording must still resolve."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    full_sha = "a1b2c3d4" + "e" * 32
    short_sha = full_sha[:7]
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [short_sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(full_sha))
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(1, [full_sha], "2026-01-02T00:00:00Z"))
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        row = s.execute(text(
            "SELECT artifact_id, method, confidence, signals FROM session_git_links WHERE session_id = 's1'"
        )).one()
    assert row.artifact_id == f"{REPO_ID}:pr:1"
    assert row.method == "sha"
    assert float(row.confidence) == 1.0
    assert row.signals["sha"] == short_sha   # the session's own (short) value, not the full one


@requires_db
def test_short_sha_resolves_via_a_backfilled_pr_with_no_commit_artifact_at_all(engine, catalog):
    """The real-world case that broke correlation entirely: backfill (unlike a live webhook
    push) never creates a standalone `commit` artifact, only a `pr` artifact carrying
    `commit_shas` -- so a repo that has ONLY ever been backfilled must still resolve a sha
    through PR membership alone, with zero `kind='commit'` rows anywhere."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    full_sha = "b" * 40
    short_sha = full_sha[:7]
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [short_sha]))
        # No _commit_artifact upsert at all -- deliberately, matching a backfill-only repo.
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(1, [full_sha], "2026-01-02T00:00:00Z"))
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM git_artifacts WHERE kind = 'commit'")).scalar()
        row = s.execute(text(
            "SELECT artifact_id, method FROM session_git_links WHERE session_id = 's1'"
        )).one()
    assert count == 0   # confirms this really is the no-commit-artifact case, not an oversight
    assert row.artifact_id == f"{REPO_ID}:pr:1"
    assert row.method == "sha"   # NOT "heuristic" -- PR membership alone is a strong enough signal


@requires_db
def test_feature_sprint_main_counts_cost_once_via_owning_pr(engine, catalog):
    """§6.4: the same commit rides a feature→sprint PR AND the later sprint→main aggregate.
    The session must link to the INNERMOST (earliest-merged) PR only."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "b" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha))
        # Innermost: feature -> sprint, merges first.
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, [sha], "2026-01-01T00:00:00Z", base_branch="sprint", branch="feature-x",
        ))
        # Aggregate: sprint -> main, merges later, re-includes the same commit.
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            2, [sha], "2026-01-05T00:00:00Z", base_branch="main", branch="sprint",
        ))
    config = _config(base_branches=("sprint", "main"))
    with session_scope() as s:
        correlate_session(s, catalog, config, "s1")
    with session_scope() as s:
        links = list(s.execute(text("SELECT artifact_id FROM session_git_links WHERE session_id = 's1'")))
    assert len(links) == 1   # counted once, not once per PR the commit rides
    assert links[0].artifact_id == f"{REPO_ID}:pr:1"   # the innermost (earliest-merged) PR owns it


@requires_db
@pytest.mark.parametrize("child_merges_first", [True, False])
def test_stacked_pr_ownership_is_stable_under_merge_order(engine, catalog, child_merges_first):
    """Regression (issue #49): a stacked child PR (#2, opened on top of #1's branch so it
    carries #1's commit X) merged BEFORE its parent must NOT steal ownership of X. Ownership
    keys on earliest-OPENED (`created_at`), not earliest-`merged_at`, so the result is
    identical in both merge orders: X owns to #1, Y owns to #2."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha_x, sha_y = "a" * 40, "b" * 40
    # #1 is opened first; #2 is opened later, stacked on #1's branch (commit_shas = [X, Y]).
    pr1_created, pr2_created = "2026-01-01T00:00:00Z", "2026-01-02T00:00:00Z"
    if child_merges_first:
        pr1_merged, pr2_merged = "2026-01-09T00:00:00Z", "2026-01-05T00:00:00Z"
    else:
        pr1_merged, pr2_merged = "2026-01-05T00:00:00Z", "2026-01-09T00:00:00Z"

    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha_x]))   # the session that produced X
        s.add(_session_with_commit_shas("s2", [sha_y]))   # the session that produced Y
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha_x))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha_y))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, [sha_x], pr1_merged, branch="feature-a", created_at=pr1_created,
        ))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            2, [sha_x, sha_y], pr2_merged, branch="feature-b", created_at=pr2_created,
        ))
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
        correlate_session(s, catalog, _config(), "s2")
    with session_scope() as s:
        s1_links = [r.artifact_id for r in s.execute(text(
            "SELECT artifact_id FROM session_git_links WHERE session_id = 's1'"))]
        s2_links = [r.artifact_id for r in s.execute(text(
            "SELECT artifact_id FROM session_git_links WHERE session_id = 's2'"))]
    # X owns to #1 (opened first) regardless of which PR merged first; Y owns to #2.
    assert s1_links == [f"{REPO_ID}:pr:1"]
    assert s2_links == [f"{REPO_ID}:pr:2"]


@requires_db
def test_out_of_scope_prs_skipped(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "c" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha))
        # Only an out-of-scope base branch contains this sha.
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, [sha], "2026-01-01T00:00:00Z", base_branch="release/1.0",
        ))
    with session_scope() as s:
        correlate_session(s, catalog, _config(base_branches=("main",)), "s1")
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM session_git_links WHERE session_id = 's1'")).scalar()
    assert count == 0   # commit known, PR known, but not in scope -- correctly no link


@requires_db
def test_excluded_head_branch_skipped_even_when_base_matches(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "d" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha))
        s.execute(catalog.get("git", "upsert_artifact"), _pr_artifact(
            1, [sha], "2026-01-01T00:00:00Z", base_branch="main", branch="staging",
        ))
    config = _config(base_branches=("main",), exclude_head=("staging",))
    with session_scope() as s:
        correlate_session(s, catalog, config, "s1")
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM session_git_links WHERE session_id = 's1'")).scalar()
    assert count == 0


@requires_db
def test_trailer_precedence_links_despite_a_rewritten_sha(engine, catalog, monkeypatch):
    """The trailer path is reserved/not-live in v1 (module docstring), but the PRECEDENCE
    itself -- checked before any sha lookup -- must work the moment a real implementation is
    plugged in. Simulated here by monkeypatching the stub, with a sha that matches NO commit
    artifact at all (as a local rebase would produce) to prove sha-lookup failure doesn't
    block the trailer path."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    rewritten_sha = "e" * 40   # never lands as a commit artifact -- simulates a local rewrite
    trailer_target = f"{REPO_ID}:commit:" + "f" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [rewritten_sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact("f" * 40))

    monkeypatch.setattr(correlate_mod, "_trailer_artifact_for_session", lambda s, sid: trailer_target)
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        row = s.execute(text(
            "SELECT artifact_id, method, confidence FROM session_git_links WHERE session_id = 's1'"
        )).one()
    assert row.artifact_id == trailer_target
    assert row.method == "trailer"
    assert float(row.confidence) == 1.0


@requires_db
def test_unresolvable_sha_reports_unlinked_not_a_silent_drop(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", ["f" * 40]))   # never lands as a commit artifact
        s.add(_session_with_commit_shas("s2", []))            # no commits at all -- not a candidate
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        cov = s.execute(catalog.get("pr_class", "correlation_coverage")).one()
    assert cov.candidate_sessions == 1   # s2 never counted -- it made no commits (§4.4)
    assert cov.linked_sessions == 0
    assert cov.unlinked_sessions == 1


@requires_db
def test_git_correlate_topic_handler_finds_and_links_sessions_for_a_merged_pr(engine, catalog):
    """The queue_jobs entry point: `payload={"artifact_id": <merged pr>}` must fan out to every
    session whose recorded sha is in that PR's commit_shas."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "9" * 40
    with session_scope() as s:
        s.add(_session_with_commit_shas("s1", [sha]))
        s.execute(catalog.get("git", "upsert_artifact"), _commit_artifact(sha))
        s.execute(catalog.get("git", "upsert_artifact"),
                 _pr_artifact(1, [sha], "2026-01-01T00:00:00Z"))
    with session_scope() as s:
        git_correlate(s, catalog, _config(), {"artifact_id": f"{REPO_ID}:pr:1"})
    with session_scope() as s:
        row = s.execute(text("SELECT artifact_id FROM session_git_links WHERE session_id = 's1'")).one()
    assert row.artifact_id == f"{REPO_ID}:pr:1"


@requires_db
def test_heuristic_fallback_matches_on_file_overlap_when_no_commit_artifact_exists(engine, catalog):
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        sess = _session_with_commit_shas("s1", ["a" * 40])   # sha unknown to git-source
        s.add(sess)
        s.execute(text(
            "INSERT INTO session_info (session_id, cost_usd, models, tools, files, skills, "
            "subagents, pr_list, turns, turn_count, long_turn_count, tokens_total, "
            "compaction_count, subagent_count, error_count, info, otel, repo_id, "
            "repo_id_provenance, created_at, updated_at) VALUES "
            "('s1', NULL, '{}', '{}', :files, '[]', '{}', '[]', '[]', 0, 0, 0, 0, 0, 0, '{}', "
            "'{}', :repo_id, 'remote', now(), now())"
        ), {"files": json.dumps({"src/x.py": {}, "src/y.py": {}}), "repo_id": REPO_ID})
        # A backfilled PR (has a real file list) sharing most files with the session.
        pr = _pr_artifact(1, [], "2026-01-01T00:00:00Z")
        pr["files"] = json.dumps([{"path": "src/x.py", "op": "modified"},
                                  {"path": "src/y.py", "op": "modified"}])
        s.execute(catalog.get("git", "upsert_artifact"), pr)
    with session_scope() as s:
        correlate_session(s, catalog, _config(), "s1")
    with session_scope() as s:
        row = s.execute(text(
            "SELECT artifact_id, method FROM session_git_links WHERE session_id = 's1'"
        )).one()
    assert row.artifact_id == f"{REPO_ID}:pr:1"
    assert row.method == "heuristic"


@requires_db
def test_query_sites_read_the_real_gitoperation_tool_result_shape(engine, catalog):
    """Regression guard for the 2026-09-14 wrong-path bug: the three sha-extraction query sites
    (`session_commit_shas`, `sessions_with_commit_sha`, `correlation_coverage`) must read the
    vendor's actual shape -- a `tool_result` event's
    `payload.raw.toolUseResult.gitOperation.commit.sha` -- not the `tool_call`/`payload.commit`
    shape the normalizer never emits. Built from the exact nested structure observed in a real
    stack `signal_blob`, so a revert to the old path fails here instead of silently linking
    zero sessions."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    short_sha, full_sha = "f1817e7", "f1817e7" + "b" * 33
    now = datetime.now(timezone.utc)
    real_event = {
        "event_type": "tool_result", "event_ts": now.isoformat(),
        "payload": {"raw": {"toolUseResult": {
            "gitOperation": {"commit": {"sha": short_sha, "kind": "committed"}},
        }}},
    }
    with session_scope() as s:
        s.add(SessionRow(
            session_id="s1", producer_id="p", org_id=None, user_id=None, agent="claude_code",
            agent_version=None, status="ended", started_at=now, last_event_at=now, ended_at=now,
            signal_blob={"events": [real_event]}, info_blob={}, parent_session_id=None,
            subagents=[],
        ))
    with session_scope() as s:
        # 1. session_commit_shas extracts the abbreviated sha from the tool_result gitOperation.
        shas = [r.sha for r in s.execute(
            catalog.get("pr_class", "session_commit_shas"), {"session_id": "s1"})]
        assert shas == [short_sha]
        # 2. sessions_with_commit_sha finds the session by the FULL landed sha (prefix match).
        found = [r.session_id for r in s.execute(
            catalog.get("pr_class", "sessions_with_commit_sha"), {"shas": [full_sha]})]
        assert found == ["s1"]
        # 3. correlation_coverage counts it as a candidate (a session that committed).
        cov = s.execute(catalog.get("pr_class", "correlation_coverage")).one()
        assert cov.candidate_sessions == 1
        assert cov.unlinked_sessions == 1   # no link written in this test


def _bash_commit_result(stdout: str) -> dict:
    """A `git commit` run through Bash: a plain Bash tool result, `gitOperation` absent
    entirely. This is the shape the vendor emits for the Bash commit path."""
    now = datetime.now(timezone.utc)
    return {
        "event_type": "tool_result", "event_ts": now.isoformat(),
        "payload": {"raw": {"toolUseResult": {
            "stdout": stdout, "stderr": "", "interrupted": False, "isImage": False,
        }}},
    }


@requires_db
def test_query_sites_also_read_commit_shas_from_bash_stdout(engine, catalog):
    """A commit made by running `git commit` through Bash carries NO
    `gitOperation.commit.sha` -- the sha exists only in git's own `[<branch> <sha>] <subject>`
    stdout line. Structured-only extraction lost every such commit: measured on a real machine,
    9 of 35 committing sessions had their shas ONLY here, and PR #58 matched 37 of them while
    the UI showed "0 session(s)". All three sha-extraction sites must read both sources."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    short_sha, full_sha = "eba9057", "eba9057" + "c" * 33
    stdout = (f"[feat/uploader-collector-integration-#61 {short_sha}] refactor(common): move it\n"
              " 3 files changed, 169 insertions(+), 41 deletions(-)\n")
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add(SessionRow(
            session_id="s1", producer_id="p", org_id=None, user_id=None, agent="claude_code",
            agent_version=None, status="ended", started_at=now, last_event_at=now, ended_at=now,
            signal_blob={"events": [_bash_commit_result(stdout)]}, info_blob={},
            parent_session_id=None, subagents=[],
        ))
    with session_scope() as s:
        shas = [r.sha for r in s.execute(
            catalog.get("pr_class", "session_commit_shas"), {"session_id": "s1"})]
        assert shas == [short_sha]
        found = [r.session_id for r in s.execute(
            catalog.get("pr_class", "sessions_with_commit_sha"), {"shas": [full_sha]})]
        assert found == ["s1"]
        cov = s.execute(catalog.get("pr_class", "correlation_coverage")).one()
        assert cov.candidate_sessions == 1


@requires_db
def test_stdout_sha_extraction_handles_real_git_output_variants(engine, catalog):
    """The greedy `[^]]+` must walk to the LAST space-separated token before `]`, so
    `(root-commit)` and `detached HEAD` resolve to the sha and not to a branch-name fragment.
    Output with no `[<branch> <sha>]` line must yield nothing rather than a bogus sha."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    now = datetime.now(timezone.utc)
    cases = {
        "plain":     ("[main 1111111] subject\n",                     "1111111"),
        "rootcommit": ("[main (root-commit) 2222222] subject\n",       "2222222"),
        "detached":  ("[detached HEAD 3333333] subject\n",             "3333333"),
        "slashhash": ("[feat/x-#9 4444444] subject\n",                 "4444444"),
        "afterhook": ("pre-commit passed\n[main 5555555] subject\n",   "5555555"),
    }
    with session_scope() as s:
        for sid, (stdout, _) in cases.items():
            s.add(SessionRow(
                session_id=sid, producer_id="p", org_id=None, user_id=None, agent="claude_code",
                agent_version=None, status="ended", started_at=now, last_event_at=now,
                ended_at=now, signal_blob={"events": [_bash_commit_result(stdout)]},
                info_blob={}, parent_session_id=None, subagents=[],
            ))
        s.add(SessionRow(
            session_id="nocommit", producer_id="p", org_id=None, user_id=None,
            agent="claude_code", agent_version=None, status="ended", started_at=now,
            last_event_at=now, ended_at=now,
            signal_blob={"events": [_bash_commit_result("nothing to commit, working tree clean\n")]},
            info_blob={}, parent_session_id=None, subagents=[],
        ))
    with session_scope() as s:
        for sid, (_, want) in cases.items():
            got = [r.sha for r in s.execute(
                catalog.get("pr_class", "session_commit_shas"), {"session_id": sid})]
            assert got == [want], f"{sid}: got {got}, want [{want}]"
        assert [r.sha for r in s.execute(
            catalog.get("pr_class", "session_commit_shas"), {"session_id": "nocommit"})] == []


@requires_db
def test_a_commit_seen_in_both_sources_is_deduplicated(engine, catalog):
    """Some commits surface structured AND in stdout. The union must yield one row, not two --
    a duplicate would double-count the sha in the correlator's per-sha loop."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    sha = "abc1234"
    now = datetime.now(timezone.utc)
    structured = {
        "event_type": "tool_result", "event_ts": now.isoformat(),
        "payload": {"raw": {"toolUseResult": {
            "gitOperation": {"commit": {"sha": sha, "kind": "committed"}},
        }}},
    }
    with session_scope() as s:
        s.add(SessionRow(
            session_id="s1", producer_id="p", org_id=None, user_id=None, agent="claude_code",
            agent_version=None, status="ended", started_at=now, last_event_at=now, ended_at=now,
            signal_blob={"events": [structured, _bash_commit_result(f"[main {sha}] subject\n")]},
            info_blob={}, parent_session_id=None, subagents=[],
        ))
    with session_scope() as s:
        assert [r.sha for r in s.execute(
            catalog.get("pr_class", "session_commit_shas"), {"session_id": "s1"})] == [sha]
