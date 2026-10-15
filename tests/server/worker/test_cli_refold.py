"""cli_refold: re-fold an already-ingested session from `sessions.signal_blob` -- no re-ingest --
rewriting only the fold-owned columns."""
from types import SimpleNamespace

import pytest

from common.msging import srv
from server.ingest.fold_handler import _fold_one
from server.storage.engine import session_scope
from server.storage.schema import SessionInfo as SessionInfoRow
from server.worker.cli_refold import refold_session
from server.worker.graph_fold import GraphFold

from .conftest import requires_db


@pytest.fixture(autouse=True)
def _no_notices(monkeypatch):
    monkeypatch.setattr(srv, "send_notice", lambda *a, **kw: None)


def _ingest(catalog, session_id: str, events: list[dict], parent_session_id=None) -> None:
    _fold_one(catalog, GraphFold(), SimpleNamespace(
        producer_id="p1", msg_id=1,
        info={"session_id": session_id, "vendor": "claude_code", "source": "batch", "agent_version": "2.0",
              "parent_session_id": parent_session_id, "org_id": "acme", "user_id": "anil"},
        payload={"events": events},
    ))


def _events() -> list[dict]:
    return [
        {"event_type": "session_start", "record_id": "r0", "parent_id": None, "event_ts": "2026-07-09T14:00:00.000Z",
         "payload": {"cwd": "/w/app", "parent_session_id": None, "raw": {}}},
        {"event_type": "user_message", "record_id": "r1", "parent_id": None, "event_ts": "2026-07-09T14:00:01.000Z",
         "payload": {"text": "hi", "raw": {}}},
        {"event_type": "assistant_message", "record_id": "r2", "parent_id": "r1",
         "event_ts": "2026-07-09T14:00:02.000Z",
         "payload": {"message_id": "msg_1", "first_block_of_message": True, "model": "claude-opus-5",
                     "input_tokens": 10, "output_tokens": 20, "cache_read_tokens": 0, "cache_write_tokens": 0,
                     "raw": {}}},
    ]


@requires_db
def test_refold_rebuilds_session_info_and_keeps_the_true_up_cost_columns(engine, catalog):
    _ingest(catalog, "s1", _events())
    with session_scope() as s:
        row = s.get(SessionInfoRow, "s1")
        # simulate a session folded by an older fold, plus the true-up's own columns
        row.info = {"stale": True}
        row.cost_effective_usd_micros = 123_456
        row.datasource = "jsonl_only"

    assert refold_session(catalog, GraphFold(), "s1") is True

    with session_scope() as s:
        row = s.get(SessionInfoRow, "s1")
        assert "stale" not in row.info
        assert "cache_busts" in row.info and "segments" in row.info
        assert row.cost_effective_usd_micros == 123_456
        assert row.datasource == "jsonl_only"


@requires_db
def test_refold_is_a_no_op_for_a_session_with_no_stored_events(engine, catalog):
    _ingest(catalog, "s2", [])
    assert refold_session(catalog, GraphFold(), "s2") is False
