"""§2b storage equivalence, without a DB: the flat `session_tree` blob round-trips to the same
`Family` -- and the same `session_info` -- as assembling from the per-session `session_graph` rows.

This is the plan's "session_tree one-fetch load == session_graph row-assembly" gate, exercised purely
in memory (Graph.to_dict/from_dict + the in-memory resolver GraphService uses). The live-DB path is
covered separately, gated on TALIUM_TEST_DSN.
"""
from __future__ import annotations

from pathlib import Path

from analysis.causal_graph import GraphContext, build_graph
from server.worker import graph_fold as gf
from tests.graph_migration.harness import build_graph_family, load_family, _to_events

_RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "normalizer" / "recorded"
_FIXTURES = ["session-regular.jsonl", "session-subagents.jsonl", "session-turns.jsonl"]


def _tree_blob(jsonl_path):
    """Build every session's graph and serialize the whole family to the flat session_tree shape."""
    sessions = load_family(jsonl_path)
    master = next(sid for sid, s in sessions.items() if s.parent_session_id is None)
    graphs = {
        sid: build_graph({
            "vendor": s.vendor, "session_id": sid, "source": s.source,
            "parent_session_id": s.parent_session_id, "events": _to_events(s),
        })
        for sid, s in sessions.items()
    }
    return {"master_session_id": master, "graphs": graphs}, master


def _family_from_blob(blob):
    """Reconstruct a Family from a tree blob using the same load_self + in-memory resolver
    GraphService.load_family uses on its fast path."""
    graphs = blob["graphs"]
    master = blob["master_session_id"]
    ctx = GraphContext()
    ctx.load_self(graphs[master])
    ctx.load_tree(lambda ids: {i: graphs[i] for i in ids if i in graphs})
    family = ctx.family
    ctx.unload_tree()
    return family


def _session_info(family, master, events_by_sid):
    """A comparable session_info-ish snapshot over a family (self + subagents rollup)."""
    out = {}
    for sid in [master, *family.children]:
        out[sid] = {
            "models": gf._models_for(family, sid),
            "tools": gf.tools_of(family, sid),
            "files": gf.files_of(family, sid),
            "compaction_count": len(gf.compactions_of(family, sid)),
        }
    out["_subagents"] = gf.subagents_of(family, master)
    return out


def test_tree_blob_roundtrips_to_same_family_and_session_info():
    for fn in _FIXTURES:
        path = _RECORDED / fn
        # Path A: the in-memory family assembled the way the harness / row-assembly does.
        fam_rows, master, events_by_sid = build_graph_family(path)
        # Path B: serialize to the flat tree blob, then reconstruct (the session_tree fast path).
        blob, master_b = _tree_blob(path)
        assert master_b == master
        fam_blob = _family_from_blob(blob)

        # Same topology.
        assert fam_blob.master.session_id == fam_rows.master.session_id
        assert set(fam_blob.children) == set(fam_rows.children)
        assert {sid: len(fam_blob.resolve(sid).nodes) for sid in [master, *fam_blob.children]} == \
               {sid: len(fam_rows.resolve(sid).nodes) for sid in [master, *fam_rows.children]}

        # Same consumer output.
        assert _session_info(fam_blob, master, events_by_sid) == _session_info(fam_rows, master, events_by_sid)


def test_tree_blob_is_flat_map_any_depth():
    """The blob is a flat {session_id: graph_dict} of master + ALL descendants -- no nesting."""
    blob, master = _tree_blob(_RECORDED / "session-subagents.jsonl")
    graphs = blob["graphs"]
    assert master in graphs
    # every non-master is a descendant with a parent_session_id; the map is flat (values are graphs).
    for sid, g in graphs.items():
        assert g["session_id"] == sid
        assert isinstance(g["nodes"], list)
        if sid != master:
            assert g["parent_session_id"] is not None
