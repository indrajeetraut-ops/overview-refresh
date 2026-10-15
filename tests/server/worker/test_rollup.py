"""The rollup handler (ingest.md §5, §7), graph-based: materialize the family tree from the
session_graph rows the arrival folds persisted, load it via GraphService, and recompute the parent's
subagent economics -- then propagate up the tree. Requires a real Postgres (TALIUM_TEST_DSN)."""
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.local_loader import load_session_source
from server.storage.engine import session_scope
from server.storage.schema import QueueJob
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.worker.graph_build import build_and_persist
from server.worker.graph_fold import GraphFold
from server.worker.rollup import make_rollup_handler
from tests.server.worker.conftest import requires_db

REC = Path("tests/fixtures/normalizer/recorded")


def _session(sid, parent=None):
    now = datetime.now(timezone.utc)
    return SessionRow(
        session_id=sid, producer_id="p", org_id=None, user_id=None, agent="claude_code",
        agent_version=None, status="live", started_at=now, last_event_at=now, ended_at=None,
        signal_blob={"events": []}, info_blob={}, parent_session_id=parent, subagents=[],
    )


def _session_dict(ns):
    return {
        "vendor": ns.vendor, "session_id": ns.session_id, "source": "jsonl",
        "parent_session_id": ns.parent_session_id,
        "events": [
            {"event_type": e.event_type, "record_id": e.record_id, "parent_id": e.parent_id,
             "event_ts": e.event_ts, "payload": e.payload}
            for e in ns.events
        ],
    }


def _seed_family(s, fixture: str):
    """Seed the sessions rows + persist each session's graph (session_graph rows) for a real fixture,
    the way the arrival fold would. Returns (master_id, [child_ids])."""
    sessions = list(ClaudeCodeNormalizer().normalize(load_session_source(REC / fixture)))
    master = next(ns for ns in sessions if ns.parent_session_id is None)
    for ns in sessions:
        s.add(_session(ns.session_id, ns.parent_session_id))
        build_and_persist(s, _session_dict(ns))
    return master.session_id, [ns.session_id for ns in sessions if ns.parent_session_id is not None]


@requires_db
def test_rollup_builds_subagents_map_and_inclusive(engine, catalog):
    with session_scope() as s:
        master, children = _seed_family(s, "session-subagents.jsonl")
    with session_scope() as s:
        make_rollup_handler(GraphFold())(s, catalog, {"session_id": master})

    with session_scope() as s:
        row = s.get(SessionInfoRow, master)
        assert set(row.subagents) == set(children)          # the subagents map from the graph
        assert all(sa["origin"] == "human" for sa in row.subagents.values())  # master is a root
        own = row.info["tokens_total"]
        incl = row.info["inclusive_tokens"]
        assert incl == own + sum(sa["inclusive_tokens"] for sa in row.subagents.values())
        assert incl > own                                    # children contributed tokens


@requires_db
def test_rollup_parent_not_yet_folded_is_noop(engine, catalog):
    with session_scope() as s:
        make_rollup_handler(GraphFold())(s, catalog, {"session_id": "ghost"})  # no row -> no error
    with session_scope() as s:
        assert s.get(SessionInfoRow, "ghost") is None


@requires_db
def test_rollup_propagates_up_to_grandparent(engine, catalog):
    with session_scope() as s:
        s.add_all([_session("G"), _session("P", parent="G")])
    with session_scope() as s:
        make_rollup_handler(GraphFold())(s, catalog, {"session_id": "P"})  # P's parent is G
    with session_scope() as s:
        jobs = s.scalars(
            select(QueueJob).where(QueueJob.dedup_key == "G", QueueJob.topic == "rollup", QueueJob.status == "pending")
        ).all()
        assert len(jobs) == 1  # P's inclusive may have changed -> ring G
