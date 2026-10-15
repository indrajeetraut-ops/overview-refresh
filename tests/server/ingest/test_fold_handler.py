"""The ingest fold handler (ingest.md §3.3): one arrived session (an Envelope carrying
`payload.events` + `info`) folds inline into `sessions` + `session_info`, ACKs after commit,
and rings the rollup for subagents. Uses a real Postgres; `srv.send_notice` is patched to
capture the outcome (no live srv machinery)."""
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text

from common.msging import srv
from server.ingest.fold_handler import _fold_one
from server.storage.engine import session_scope
from server.storage.schema import QueueJob
from server.storage.schema import Repository as RepositoryRow
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.storage.schema import User as UserRow
from server.worker.graph_fold import GraphFold
from tests.server.ingest.conftest import requires_db


@pytest.fixture
def notices(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(srv, "send_notice", lambda pid, mid, code, **kw: calls.append((mid, code)))
    return calls


def _events(*types):
    return [
        {"event_type": t, "record_id": f"r{i}", "parent_id": None,
         "event_ts": f"2026-07-09T14:00:0{i}.000Z", "payload": {"raw": {"type": t}}}
        for i, t in enumerate(types)
    ]


def _session_start_with_cwd(cwd, git_branch="main", *, event_id="start", event_ts="2026-07-09T14:00:00.000Z"):
    return {
        "event_type": "session_start", "record_id": None, "parent_id": None, "event_ts": event_ts,
        "payload": {"cwd": cwd, "git_branch": git_branch, "repo_id": None,
                   "parent_session_id": None, "raw": {}},
    }


def _envelope(session_id, events, *, parent_session_id=None, msg_id=1):
    return SimpleNamespace(
        producer_id="p1", msg_id=msg_id,
        info={"session_id": session_id, "vendor": "claude_code", "source": "batch",
              "agent_version": "2.0", "parent_session_id": parent_session_id,
              "org_id": "acme", "user_id": "anil"},
        payload={"events": events},
    )


@requires_db
def test_folds_session_into_db_and_acks(engine, catalog, notices):
    ev = _envelope("s1", _events("session_start", "assistant_message", "user_message"))
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        row = s.get(SessionRow, "s1")
        assert row is not None and row.agent == "claude_code" and row.org_id == "acme"
        assert len(row.signal_blob["events"]) == 3
        assert s.get(SessionInfoRow, "s1") is not None
    assert notices == [(1, "ack")]


@requires_db
def test_resend_is_idempotent(engine, catalog, notices):
    ev = _envelope("s1", _events("session_start", "assistant_message"))
    _fold_one(catalog, GraphFold(), ev)
    _fold_one(catalog, GraphFold(), ev)  # resend of the whole session -> positional dedup

    with session_scope() as s:
        assert len(s.get(SessionRow, "s1").signal_blob["events"]) == 2  # not 4
    assert notices == [(1, "ack"), (1, "ack")]


@requires_db
def test_subagent_rings_parent_rollup(engine, catalog, notices):
    _fold_one(catalog, GraphFold(),
              _envelope("child", _events("session_start"), parent_session_id="parent", msg_id=2))
    with session_scope() as s:
        jobs = s.scalars(
            select(QueueJob).where(QueueJob.dedup_key == "parent", QueueJob.topic == "rollup")
        ).all()
        assert len(jobs) == 1  # the subagent rang its parent's rollup (ingest.md §7)


@requires_db
def test_started_at_backfilled_from_earliest_event(engine, catalog, notices):
    # ensure_session (config/queries/ingest.yaml) stamps started_at = now() at row creation;
    # the fold must backfill it to the session's real, historical first event, not leave every
    # uploaded session clustered around ingest time.
    ev = _envelope("s1", _events("session_start", "assistant_message"))
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        row = s.get(SessionRow, "s1")
        assert row.started_at.isoformat().startswith("2026-07-09T14:00:00")


@requires_db
def test_account_uuid_links_to_the_declared_user_id(engine, catalog, notices):
    """`account_uuid` in the envelope info (declared at upload time,
    utils/upload_claude_sessions.sh) links this session's `user_id` to the vendor account
    identity OTel carries -- otel_trueup.py's stub path resolves through it later."""
    ev = _envelope("s1", _events("session_start"))
    ev.info["account_uuid"] = "11111111-1111-4111-8111-111111111111"
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        row = s.get(UserRow, "anil")
        assert row is not None
        assert row.account_uuid == "11111111-1111-4111-8111-111111111111"


@requires_db
def test_no_account_uuid_creates_no_users_row(engine, catalog, notices):
    """An older collector that never declares `account_uuid` must not create an empty `users`
    row on every session fold."""
    ev = _envelope("s1", _events("session_start"))
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        assert s.get(UserRow, "anil") is None


@requires_db
def test_github_user_links_to_the_declared_user_id(engine, catalog, notices):
    """`github_user` in the envelope info (declared at upload time,
    utils/upload_claude_sessions.sh) populates `users.github_user` for this session's
    `user_id`, normalized lower-case."""
    ev = _envelope("s1", _events("session_start"))
    ev.info["github_user"] = "AKhuteta"
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        row = s.get(UserRow, "anil")
        assert row is not None
        assert row.github_user == "akhuteta"


@requires_db
def test_no_github_user_creates_no_users_row(engine, catalog, notices):
    ev = _envelope("s1", _events("session_start"))
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        assert s.get(UserRow, "anil") is None


@requires_db
def test_session_reconciles_to_a_preexisting_github_stub_identity(engine, catalog, notices):
    """§(a) ordering fix: if git-source already auto-created a `github:<login>` identity for
    this person (their PR landed before they ever declared their handle here), a later upload
    declaring that same login must fold under THAT user_id, not fork a second one under the
    raw upload-declared user_id."""
    with session_scope() as s:
        s.execute(text(
            "INSERT INTO users (user_id, github_user, info, created_at) "
            "VALUES ('github:akhuteta', 'akhuteta', '{}'::jsonb, now())"
        ))

    ev = _envelope("s1", _events("session_start"))
    ev.info["github_user"] = "akhuteta"   # declared under info["user_id"] == "anil"
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        session_row = s.get(SessionRow, "s1")
        assert session_row.user_id == "github:akhuteta"   # NOT "anil"
        assert s.get(UserRow, "anil") is None   # no forked second identity
        assert len(s.scalars(select(UserRow)).all()) == 1


@requires_db
def test_fold_upserts_a_repositories_row_from_the_resolved_repo_id(engine, catalog, notices):
    ev = _envelope("s1", [_session_start_with_cwd("/Users/dev/talium/code-econ")])
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        info = s.get(SessionInfoRow, "s1")
        assert info.repo_id == "local:code-econ"
        assert info.repo_id_provenance == "path"

        repo = s.get(RepositoryRow, "local:code-econ")
        assert repo is not None
        assert repo.kind == "local_path"
        assert repo.label == "code-econ"
        assert repo.first_seen_at is not None


@requires_db
def test_second_fold_bumps_last_seen_at_not_first_seen_at(engine, catalog, notices):
    ev1 = _envelope("s1", [_session_start_with_cwd("/Users/dev/talium/code-econ")])
    _fold_one(catalog, GraphFold(), ev1)
    with session_scope() as s:
        first_seen = s.get(RepositoryRow, "local:code-econ").first_seen_at

    ev2 = _envelope("s2", [_session_start_with_cwd("/Users/dev/talium/code-econ")], msg_id=2)
    _fold_one(catalog, GraphFold(), ev2)
    with session_scope() as s:
        repo = s.get(RepositoryRow, "local:code-econ")
        assert repo.first_seen_at == first_seen
        assert repo.last_seen_at >= first_seen


@requires_db
def test_no_cwd_creates_no_repositories_row(engine, catalog, notices):
    """A session with no `cwd` anywhere (metadata-only) resolves no `repo_id` at all -- no
    row should be written, never a guessed/empty one."""
    ev = _envelope("s1", _events("session_start"))  # generic helper: no cwd in payload
    _fold_one(catalog, GraphFold(), ev)

    with session_scope() as s:
        assert s.get(SessionInfoRow, "s1").repo_id is None
        assert s.scalars(select(RepositoryRow)).all() == []


@requires_db
def test_missing_session_id_nacks(engine, catalog, notices):
    bad = SimpleNamespace(producer_id="p", msg_id=9, info={}, payload={"events": []})
    _fold_one(catalog, GraphFold(), bad)
    assert notices == [(9, "nack")]
    with session_scope() as s:
        assert s.scalars(select(SessionRow)).all() == []  # nothing persisted
