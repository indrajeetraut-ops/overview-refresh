"""A7 GET /v1/users/{id}/tips -- service-level (`server/api/manager/service.py::my_tips`).

Plan Verify: "numbers match old S1; lexicon passes."
"""
from datetime import datetime, timezone

from server.api.config import ApiConfig
from server.api.manager.service import my_tips
from server.storage import session_scalars, session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
TO = datetime(2026, 1, 31, tzinfo=timezone.utc)
MID = datetime(2026, 1, 15, tzinfo=timezone.utc)

#: The tone lexicon (AGENTS.md binding conventions / plan section 1) -- banned words a
#: surfaced string must never contain.
_BANNED = ("waste", "wasted", "wasteful")


def _seed(s):
    info = {
        "channel_cost": {"output": {"usd_micros": 20_000_000}, "recache": {"usd_micros": 10_000_000}},
        "carry": {"carry_usd_micros": 5_000_000},  # never recoverable
        "resume_cost": {"total_recoverable_usd_micros": 15_000_000, "events": []},
    }
    s.add(SessionRow(session_id="s1", producer_id="p", org_id="default", user_id="u1",
                     agent="claude_code", status="ended", started_at=MID, last_event_at=MID,
                     ended_at=MID, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfo(session_id="s1", created_at=MID, updated_at=MID, models={}, tools={},
                      files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
                      long_turn_count=0, datasource="both", cost_effective_usd_micros=50_000_000,
                      info=info, otel={}, **session_scalars.fold_scalars(info, {})))


@requires_db
def test_tips_reconcile_to_the_recoverable_buckets(engine, catalog):
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        result = my_tips(s, catalog, ApiConfig(), "u1", FRM, TO)

    tips = result["data"]["tips"]
    assert len(tips) == 1  # only "resume" is recoverable in this fixture (carry/recache never are)
    assert tips[0]["category"] == "resume"
    assert tips[0]["could_save"]["usd_micros"] == 15_000_000


@requires_db
def test_tips_pass_the_tone_lexicon(engine, catalog):
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        result = my_tips(s, catalog, ApiConfig(), "u1", FRM, TO)

    for tip in result["data"]["tips"]:
        lowered = tip["text"].lower()
        assert not any(word in lowered for word in _BANNED)


@requires_db
def test_tips_empty_when_nothing_recoverable(engine, catalog):
    with session_scope() as s:
        result = my_tips(s, catalog, ApiConfig(), "ghost", FRM, TO)
    assert result["data"]["tips"] == []
