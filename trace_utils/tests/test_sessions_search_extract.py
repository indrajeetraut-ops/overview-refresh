"""Integration over local fixtures: sessions, search, show, extract (incl. idempotency)."""
import json

from trace_utils import extract as extractmod
from trace_utils.search import search
from trace_utils.sessions import list_sessions, session_events
from tests.conftest import SESSION_ID


def test_list_sessions_folds_orphan_snapshot(engine, jsonl_source):
    sessions = list_sessions(engine, jsonl_source)
    assert len(sessions) == 1
    s = sessions[0]
    assert s.session_id == SESSION_ID
    assert s.n_events == 4  # 3 with sessionId + 1 snapshot folded by filename stem
    assert "claude-opus-4-6" in s.models


def test_session_events_returns_full_transcript(engine, jsonl_source):
    cols, rows = session_events(engine, jsonl_source, SESSION_ID)
    assert len(rows) == 4  # includes the snapshot with no sessionId


def test_search_fulltext_scope(engine, jsonl_source):
    hits = search(engine, jsonl_source, in_field="message.content", in_term="sound")
    assert len(hits) == 1 and hits[0].session_id == SESSION_ID
    # a term that doesn't occur returns nothing
    assert search(engine, jsonl_source, in_field="message.content", in_term="zzzz") == []


def test_search_structured_where(engine, jsonl_source):
    hits = search(engine, jsonl_source, where="message.model=claude-opus-4-6")
    assert len(hits) == 1
    assert hits[0].n_events == 1  # one event carries that model


def test_parquet_flat_sessions(engine, parquet_source):
    sessions = list_sessions(engine, parquet_source)
    assert {s.session_id for s in sessions} == {"s1", "s2"}
    hits = search(engine, parquet_source, in_field="user_prompt", in_term="refactor")
    assert [h.session_id for h in hits] == ["s2"]


def test_extract_is_idempotent(engine, jsonl_source, tmp_path):
    out = tmp_path / "out"
    p1, n1 = extractmod.extract(engine, jsonl_source, str(out), fmt="jsonl")
    p2, n2 = extractmod.extract(engine, jsonl_source, str(out), fmt="jsonl")
    assert n1 == n2 == 4
    assert p1 == p2
    lines = [json.loads(l) for l in open(p1) if l.strip()]
    assert len(lines) == 4  # rerun overwrites, does not append


def test_extract_where_subset(engine, jsonl_source, tmp_path):
    from trace_utils.search import build_where_sql
    where_sql = build_where_sql(where="message.model=claude-opus-4-6")
    path, n = extractmod.extract(engine, jsonl_source, str(tmp_path / "o"), fmt="jsonl", where_sql=where_sql)
    assert n == 1
