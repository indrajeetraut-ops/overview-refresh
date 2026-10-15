"""Live-DB coverage for §2b: build_and_persist -> materialize_tree -> GraphService.load_family,
against a real Postgres. Gated on TALIUM_TEST_DSN (same discipline as the ingest e2e).

Asserts the plan's storage gates: the session_tree one-fetch load equals the session_graph
row-assembly, and root_master_of walks a subagent up to its master.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from collector.normalizer.claude_code import ClaudeCodeNormalizer
from collector.normalizer.local_loader import load_session_source
from server.storage.config import StorageConfig
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from server.worker.graph_build import build_and_persist, load_family_from_rows, materialize_tree
from server.worker.graph_service import GraphService

DSN = os.environ.get("TALIUM_TEST_DSN")
pytestmark = pytest.mark.skipif(DSN is None, reason="set TALIUM_TEST_DSN to run the graph-persistence DB test")

REC = Path("tests/fixtures/normalizer/recorded")


def _sessions(jsonl_path):
    src = load_session_source(jsonl_path)
    out = {}
    for ns in ClaudeCodeNormalizer().normalize(src):
        out[ns.session_id] = {
            "vendor": ns.vendor, "session_id": ns.session_id, "source": ns.source,
            "parent_session_id": ns.parent_session_id,
            "events": [
                {"event_type": e.event_type, "record_id": e.record_id, "parent_id": e.parent_id,
                 "event_ts": e.event_ts, "payload": e.payload}
                for e in ns.events
            ],
        }
    return out


def _family_fingerprint(family):
    return {
        "master": family.master.session_id,
        "children": sorted(family.children),
        "nodes": {sid: len(family.resolve(sid).nodes) for sid in [family.master.session_id, *family.children]},
    }


@pytest.mark.slow
def test_persist_materialize_load_equivalence(tmp_path):
    storage_cfg = StorageConfig(dsn=DSN, queries_dir="config/queries")
    engine = get_engine(storage_cfg)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load("config/queries")
    service = GraphService(catalog)
    try:
        sessions = _sessions(REC / "session-subagents.jsonl")
        master = next(sid for sid, s in sessions.items() if s["parent_session_id"] is None)
        children = [sid for sid in sessions if sid != master]

        with session_scope() as s:
            for session in sessions.values():
                build_and_persist(s, session)

        # Row-assembly (before any tree materialization) -- the source of truth. Unload each context
        # before the next load: the engine registers session_ids process-globally, so the same
        # family can't be live in two contexts at once.
        with session_scope() as s:
            ctx_rows, fam_rows = load_family_from_rows(s, catalog, master)
            rows_fp = _family_fingerprint(fam_rows)
            ctx_rows.unload_tree()
            # GraphService with no session_tree yet falls back to row-assembly.
            fam_fallback = service.load_family(s, master)
            assert _family_fingerprint(fam_fallback) == rows_fp
            # root_master_of resolves a subagent up to the master.
            assert service.root_master_of(s, children[0]) == master

        # Materialize the tree, then the one-fetch fast path must equal the row-assembly.
        with session_scope() as s:
            tree = materialize_tree(s, catalog, master)
            assert tree["master_session_id"] == master
            assert set(tree["graphs"]) == {master, *children}

        with session_scope() as s:
            fam_blob = service.load_family(s, master)
            assert _family_fingerprint(fam_blob) == rows_fp
            # Loading via any member id lands on the same family.
            assert _family_fingerprint(service.load_family(s, children[0])) == rows_fp
    finally:
        dispose_engine()
