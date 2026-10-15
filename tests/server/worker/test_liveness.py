from datetime import datetime, timedelta, timezone

from server.storage.engine import session_scope
from server.storage.schema import Session as SessionRow
from server.worker.liveness import close_idle_sessions
from tests.server.worker.conftest import requires_db


def _session(sid, status, last_event_at, parent=None):
    now = datetime.now(timezone.utc)
    return SessionRow(
        session_id=sid, producer_id="p", org_id=None, user_id=None, agent="claude_code",
        agent_version=None, status=status, started_at=now, last_event_at=last_event_at,
        ended_at=None, signal_blob={"events": []}, info_blob={}, parent_session_id=parent,
        subagents=[],
    )


@requires_db
def test_close_idle_sessions_flips_stale_live_sessions(engine, catalog):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add_all([
            _session("stale", "live", now - timedelta(hours=2)),
            _session("fresh", "live", now - timedelta(minutes=5)),
        ])

    closed = close_idle_sessions(catalog, idle_after_s=3600.0, now=now)

    assert closed == 1
    with session_scope() as s:
        assert s.get(SessionRow, "stale").status == "idle_closed"
        assert s.get(SessionRow, "fresh").status == "live"


@requires_db
def test_close_idle_sessions_never_touches_non_live_status(engine, catalog):
    now = datetime.now(timezone.utc)
    with session_scope() as s:
        s.add_all([
            _session("stub", "otel_only", now - timedelta(hours=2)),
            _session("already", "idle_closed", now - timedelta(hours=2)),
        ])

    closed = close_idle_sessions(catalog, idle_after_s=3600.0, now=now)

    assert closed == 0
    with session_scope() as s:
        assert s.get(SessionRow, "stub").status == "otel_only"
        assert s.get(SessionRow, "already").status == "idle_closed"
