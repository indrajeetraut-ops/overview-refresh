"""The A/B differential (combined sub-plan §3, plan §7 Layer 3): GraphFold's graph-based
`session_info` must reproduce the frozen Step-0 baseline (`baseline_fixtures.json` = github
normalizer + EconFold = 'today's dashboard behavior') to within the decided allowlist.

Incremental: GraphFold implements §4.A field-by-field. This test asserts each **implemented** field
matches the baseline (allowlist-filtered), and separately prints the **punch-list** of baseline
fields not yet produced -- the remaining §3/§4 work -- without failing on them (they are expected to
be absent until ported). As fields land, move their names from PUNCH_LIST into IMPLEMENTED.
"""
from __future__ import annotations

import json
from pathlib import Path

from analysis.causal_graph.models import Family
from server.worker import graph_fold as gf
from tests.graph_migration.differential import diff, is_allowed
from tests.graph_migration.harness import build_graph_family, serialize_session_info

_GOLDEN = Path(__file__).resolve().parent / "baseline_fixtures.json"
_RECORDED = Path(__file__).resolve().parents[1] / "fixtures" / "normalizer" / "recorded"
_FIXTURES = {
    "session-regular": "session-regular.jsonl",
    "session-subagents": "session-subagents.jsonl",
    "session-turns": "session-turns.jsonl",
}

# All top-level session_info keys are produced by build_session_info; the A/B compares them all.
IMPLEMENTED = {"models", "tokens_total", "tools", "compaction_count", "subagent_count", "pr_list",
               "files", "turns", "turn_count", "long_turn_count",
               "cost_usd", "error_count", "repo_id", "repo_id_provenance", "subagents", "skills", "info"}


def _graph_session_info(family: Family, session_id: str, events: list[dict]) -> dict:
    """The canonical graph-based session_info (the exact code GraphFold.fold/rollup calls),
    serialized for the field-by-field diff."""
    return serialize_session_info(gf.build_session_info(family, session_id, events))


def test_ab_implemented_fields_match_baseline():
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    regressions: list[str] = []
    for name, fn in _FIXTURES.items():
        family, master, events_by_sid = build_graph_family(_RECORDED / fn)
        for sid in golden[name]:
            candidate = _graph_session_info(family, sid, events_by_sid.get(sid, []))
            base = {k: v for k, v in golden[name][sid].items() if k in IMPLEMENTED}
            for p in diff(base, candidate):
                if p.path.split(".")[0].split("[")[0] in IMPLEMENTED and not is_allowed(p):
                    regressions.append(f"{name}/{sid}: {p.describe()}")
    assert not regressions, "GraphFold diverges from baseline on implemented fields:\n  " + "\n  ".join(regressions)


def test_graphfold_fold_and_rollup_match_baseline():
    """Exercise the real GraphFold flow (fold each session -> assemble family -> rollup master),
    mirroring the ingest+rollup handlers but in memory, and diff the result against the baseline."""
    from analysis.causal_graph import GraphContext
    from server.worker.graph_fold import GraphFold
    from tests.graph_migration.harness import _new_session, _to_events, load_family

    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    fold = GraphFold()
    regressions: list[str] = []
    for name, fn in _FIXTURES.items():
        sessions = load_family(_RECORDED / fn)
        master = next(sid for sid, s in sessions.items() if s.parent_session_id is None)
        graphs: dict[str, dict] = {}
        states = {}
        infos: dict[str, dict] = {}
        # fold each session (arrival), collecting the persisted graph + self session_info
        for sid, ns in sessions.items():
            state = _new_session(sid, ns.parent_session_id, agent=ns.vendor)
            result = fold.fold(state, _to_events(ns))
            graphs[sid] = result.graph
            states[sid] = result.session
            infos[sid] = serialize_session_info(result.session_info)
        # rollup the master over the assembled family (worker path)
        ctx = GraphContext()
        ctx.load_self(graphs[master])
        ctx.load_tree(lambda ids: {i: graphs[i] for i in ids if i in graphs})
        delta = fold.rollup_from_family(states[master], ctx.family)
        ctx.unload_tree()
        infos[master] = serialize_session_info(delta.session_info)

        for sid in golden[name]:
            for p in diff(golden[name][sid], infos.get(sid, {})):
                if not is_allowed(p):
                    regressions.append(f"{name}/{sid}: {p.describe()}")
    assert not regressions, "GraphFold fold/rollup diverges from baseline:\n  " + "\n  ".join(regressions)


def test_ab_punch_list_report(capsys):
    """Informational: the baseline fields GraphFold does NOT yet produce (the remaining port)."""
    golden = json.loads(_GOLDEN.read_text(encoding="utf-8"))
    remaining: set[str] = set()
    for name in _FIXTURES:
        for sid in golden[name]:
            remaining |= set(golden[name][sid]) - IMPLEMENTED
    remaining.discard("session_id")
    with capsys.disabled():
        print("\n[A/B punch-list] session_info top-level keys not yet produced by GraphFold:")
        for key in sorted(remaining):
            print(f"    - {key}")
        print(f"[A/B] implemented so far: {sorted(IMPLEMENTED)}")
