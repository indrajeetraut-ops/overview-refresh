from datetime import datetime, timezone

import pytest

from server.api.single_user import service
from server.api.errors import BadRequestError, NotFoundError
from server.storage import session_scope
from server.storage.schema import Feedback
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow

from .conftest import requires_db

pytestmark = requires_db

NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed_session(s, session_id="s1"):
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id="u1",
                     agent="claude_code", status="live", started_at=NOW, last_event_at=NOW,
                     signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfoRow(session_id=session_id, created_at=NOW, updated_at=NOW,
                         models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[],
                         turns=[], turn_count=0, long_turn_count=0, info={}, otel={},
                         datasource="both"))


def test_submit_feedback_round_trips(engine, catalog):
    with session_scope() as s:
        _seed_session(s)

    with session_scope() as s:
        result = service.submit_feedback(s, catalog, "s1", "insight:29", "dismissed")

    assert result["session_id"] == "s1"
    assert result["beat_ref"] == "insight:29"
    assert result["verdict"] == "dismissed"
    assert result["id"] is not None
    assert result["created_at"] is not None

    with session_scope() as s:
        row = s.get(Feedback, result["id"])
        assert row.session_id == "s1"
        assert row.beat_ref == "insight:29"
        assert row.verdict == "dismissed"


def test_submit_feedback_unknown_session_raises_not_found(engine, catalog):
    with session_scope() as s:
        with pytest.raises(NotFoundError):
            service.submit_feedback(s, catalog, "does-not-exist", "insight:29", "dismissed")


def test_submit_feedback_rejects_unknown_verdict(engine, catalog):
    with session_scope() as s:
        _seed_session(s)

    with session_scope() as s:
        with pytest.raises(BadRequestError):
            service.submit_feedback(s, catalog, "s1", "insight:29", "not-a-real-verdict")


def test_submit_feedback_rejects_empty_beat_ref(engine, catalog):
    with session_scope() as s:
        _seed_session(s)

    with session_scope() as s:
        with pytest.raises(BadRequestError):
            service.submit_feedback(s, catalog, "s1", "", "dismissed")
