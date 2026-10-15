"""S1 rhythm zone, R3: `/overview`'s z0 block (peak hour + favorite model)."""
from datetime import datetime, timedelta, timezone

from server.api.single_user import service
from server.api.config import ApiConfig
from server.storage import session_scope
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.storage.schema import User

from .conftest import requires_db

pytestmark = requires_db

USER = "u1"
NOW = datetime(2026, 8, 1, tzinfo=timezone.utc)


def _seed(s, session_id, datasource, *, started_at=None, otel=None, models=None,
         turns=None, cost_effective_usd_micros=1_000_000):
    started_at = started_at or NOW
    s.add(SessionRow(session_id=session_id, producer_id="p", org_id="org-acme", user_id=USER,
                     agent="claude_code", status="live", started_at=started_at,
                     last_event_at=started_at, signal_blob={}, info_blob={}, subagents=[]))
    s.add(SessionInfoRow(
        session_id=session_id, created_at=NOW, updated_at=NOW,
        models=models or {}, tools={}, files={}, skills=[], subagents={}, pr_list=[],
        turns=turns or [], turn_count=len(turns or []), long_turn_count=0, info={},
        otel=otel or {}, cost_effective_usd_micros=cost_effective_usd_micros,
        datasource=datasource,
    ))


def _fetch_z0(s, catalog, frm=None, to=None):
    frm = frm or (NOW - timedelta(days=1))
    to = to or (NOW + timedelta(days=1))
    return service.overview(s, catalog, ApiConfig(), USER, frm, to)["z0"]


def test_z0_peak_hour_resolves_via_turn_started_in_jsonl_range(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             turns=[{"started": "2026-08-01T14:05:00Z"},
                   {"started": "2026-08-01T14:40:00Z"},
                   {"started": "2026-08-01T09:00:00Z"}])

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["peak_hour"] == {"value": 14, "source": "turn_started"}
    assert z0["peak_hour_histogram"][14] == 2
    assert z0["peak_hour_histogram"][9] == 1
    assert sum(z0["peak_hour_histogram"]) == 3


def test_z0_peak_hour_falls_back_to_session_started_in_otel_only_range(engine, catalog):
    with session_scope() as s:
        _seed(s, "otel-1", "otel_only", started_at=NOW.replace(hour=21),
             otel={"measures": {"tokens_input": "300"},
                  "tokens": {"tokens_input": "300", "tokens_output": "0",
                            "tokens_cache_read": "0", "tokens_cache_creation": "0"}})

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    # A session with no turns at all -- true of every otel_only row, since session_info.turns
    # is JSONL-fold-only -- falls back to one point at its own started_at hour (FEAS §3: no
    # per-request OTel timestamp survives the fold to bucket by instead).
    assert z0["peak_hour"] == {"value": 21, "source": "session_started"}
    assert z0["peak_hour_histogram"][21] == 1
    assert sum(z0["peak_hour_histogram"]) == 1


def test_z0_peak_hour_mixed_range_reports_both_sources(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only", turns=[{"started": "2026-08-01T10:00:00Z"}])
        _seed(s, "otel-1", "otel_only", started_at=NOW.replace(hour=10),
             otel={"measures": {"tokens_input": "1"},
                  "tokens": {"tokens_input": "1", "tokens_output": "0",
                            "tokens_cache_read": "0", "tokens_cache_creation": "0"}})

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["peak_hour"]["value"] == 10
    assert set(z0["peak_hour"]["source"].split(",")) == {"turn_started", "session_started"}
    assert z0["peak_hour_histogram"][10] == 2


def test_z0_peak_hour_tie_breaks_to_lowest_hour(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             turns=[{"started": "2026-08-01T05:00:00Z"}, {"started": "2026-08-01T20:00:00Z"}])

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["peak_hour_histogram"][5] == 1
    assert z0["peak_hour_histogram"][20] == 1
    assert z0["peak_hour"]["value"] == 5   # documented tie-break: lowest hour wins


def test_z0_peak_hour_buckets_by_the_users_own_timezone(engine, catalog):
    """TZ track (mvp-gap-closure-plan): a turn at 23:05 UTC is 16:05 the same day in
    America/Los_Angeles -- with no timezone recorded, this session's own hour-14/09 peers from
    `test_z0_peak_hour_resolves_via_turn_started_in_jsonl_range` would land it in the 23:00
    bucket instead of 16:00."""
    with session_scope() as s:
        s.add(User(user_id=USER, created_at=NOW, timezone="America/Los_Angeles"))
        _seed(s, "jsonl-1", "jsonl_only", turns=[{"started": "2026-08-01T23:05:00Z"}])

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["peak_hour"] == {"value": 16, "source": "turn_started"}
    assert z0["peak_hour_histogram"][16] == 1
    assert z0["peak_hour_histogram"][23] == 0


def test_z0_peak_hour_empty_range_is_sourceless_not_zero_hour(engine, catalog):
    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["peak_hour"] == {"value": None, "source": None}
    assert z0["peak_hour_histogram"] == [0] * 24


def test_z0_favorite_model_excludes_zero_token_synthetic_entries(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             models={"claude-sonnet": {"input": 500, "output": 100,
                                       "cache_read": 0, "cache_write": 0},
                    "<synthetic>": {"input": 0, "output": 0,
                                    "cache_read": 0, "cache_write": 0}})

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["favorite_model"] == {"model": "claude-sonnet", "tokens": 600, "cost_usd_micros": 0}


def test_z0_favorite_model_picks_the_higher_token_model(engine, catalog):
    with session_scope() as s:
        _seed(s, "jsonl-1", "jsonl_only",
             models={"claude-haiku": {"input": 50, "output": 0, "cache_read": 0, "cache_write": 0},
                    "claude-opus-5": {"input": 500, "output": 200,
                                      "cache_read": 0, "cache_write": 0}})

    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["favorite_model"]["model"] == "claude-opus-5"
    assert z0["favorite_model"]["tokens"] == 700


def test_z0_favorite_model_none_when_range_is_empty(engine, catalog):
    with session_scope() as s:
        z0 = _fetch_z0(s, catalog)

    assert z0["favorite_model"] is None
