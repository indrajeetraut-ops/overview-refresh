"""The otel_* tables and org_tokens (otel-intake design §4.3, §4.5).

The point of these tests is the **idempotency contract**: the dedup keys are what make an
OTel SDK retry harmless. `insert(...).on_conflict_do_nothing()` must insert zero rows on a
replay, for every one of the three tables, or at-least-once delivery turns into inflated
spend.
"""
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from server.storage import session_scope
from server.storage.dbm import operations
from server.storage.schema import (
    Base,
    OrgToken,
    OtelApiRequest,
    OtelMetricPoint,
    OtelSeries,
)
from tests.server.storage.conftest import requires_db

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)

SERIES = {
    "series_id": "a" * 64,
    "vendor": "claude_code",
    "metric_name": "claude_code.token.usage",
    "metric_kind": "sum",
    "temporality": "cumulative",
    "org_id": "org-1",
    "session_id": "sess-1",
    "user_key": "dev1@example.test",
    "attrs": {"type": "output", "model": "claude-opus-5", "query_source": "main"},
    "first_seen_at": NOW,
}
POINT = {
    "series_id": SERIES["series_id"],
    "time_unix_nano": 1785606317710000000,
    "start_unix_nano": 1785606315468000000,
    "value": Decimal("7433"),
    "received_at": NOW,
}
REQUEST = {
    "vendor": "claude_code",
    "request_id": "req_011CdcQDE4tHq6GxSEsdHpXa",
    "session_id": "sess-1",
    "org_id": "org-1",
    "user_key": "dev1@example.test",
    "model": "claude-opus-5",
    "input_tokens": 559,
    "output_tokens": 14,
    "cache_read_tokens": 0,
    "cache_creation_tokens": 0,
    "cost_usd_micros": 629,
    "duration_ms": 915,
    "query_source": "prompt_suggestion",
    "prompt_id": "88322026-ee1a-4570-9506-c0a8622b7413",
    "event_ts": NOW,
    "attrs": {"speed": "normal", "effort": "high", "organization.id": "payload-org"},
    "received_at": NOW,
}


def _insert_if_absent(s, model, row: dict) -> int:
    """The receiver's write shape. Returns rows actually inserted.

    Counted via RETURNING rather than `rowcount`: a conflicting row is not "affected", and
    the driver reports -1 for the statement either way, so RETURNING is the only honest
    count of what landed.
    """
    stmt = (insert(model.__table__).values(**row).on_conflict_do_nothing()
            .returning(*model.__table__.primary_key.columns))
    return len(s.execute(stmt).all())


@requires_db
def test_migration_from_empty_builds_the_otel_tables(cfg):
    """A fresh database gets all four tables (§4.3 storage contract)."""
    operations.init(cfg, force=True)
    from sqlalchemy import inspect

    from server.storage.engine import get_engine

    present = set(inspect(get_engine(cfg)).get_table_names())
    assert {"otel_series", "otel_metric_points", "otel_api_requests", "org_tokens"} <= present


@requires_db
def test_series_and_point_roundtrip_with_jsonb_attrs(cfg):
    operations.init(cfg, force=True)
    with session_scope() as s:
        s.add(OtelSeries(**SERIES))
        s.add(OtelMetricPoint(**POINT))
    with session_scope() as s:
        series = s.get(OtelSeries, SERIES["series_id"])
        # The full label set survives verbatim -- unknown keys are must-ignore, not dropped.
        assert series.attrs == SERIES["attrs"]
        assert series.temporality == "cumulative"
        point = s.get(OtelMetricPoint, (POINT["series_id"], POINT["time_unix_nano"]))
        assert point.value == Decimal("7433")
        # nanosecond timestamps exceed int32 by six orders of magnitude -- BIGINT, not INTEGER
        assert point.start_unix_nano == POINT["start_unix_nano"]


@requires_db
def test_replayed_datapoint_inserts_zero_rows(cfg):
    """`(series_id, time_unix_nano)` is the dedup key: an SDK retry is a no-op (§4.3)."""
    operations.init(cfg, force=True)
    with session_scope() as s:
        assert _insert_if_absent(s, OtelSeries, SERIES) == 1
        assert _insert_if_absent(s, OtelMetricPoint, POINT) == 1
    with session_scope() as s:
        assert _insert_if_absent(s, OtelSeries, SERIES) == 0
        assert _insert_if_absent(s, OtelMetricPoint, POINT) == 0
        # a later export of the same series is a new row, not a conflict
        later = {**POINT, "time_unix_nano": POINT["time_unix_nano"] + 60_000_000_000,
                 "value": Decimal("9001")}
        assert _insert_if_absent(s, OtelMetricPoint, later) == 1
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(OtelMetricPoint)) == 2
        assert s.scalar(select(func.count()).select_from(OtelSeries)) == 1


@requires_db
def test_replayed_api_request_inserts_zero_rows(cfg):
    """`(vendor, request_id)` is the dedup key -- and a namespace: the same request_id
    under a different vendor is a different request (D-OT6)."""
    operations.init(cfg, force=True)
    with session_scope() as s:
        assert _insert_if_absent(s, OtelApiRequest, REQUEST) == 1
    with session_scope() as s:
        assert _insert_if_absent(s, OtelApiRequest, REQUEST) == 0
        assert _insert_if_absent(s, OtelApiRequest, {**REQUEST, "vendor": "gen_ai"}) == 1
    with session_scope() as s:
        stored = s.get(OtelApiRequest, ("claude_code", REQUEST["request_id"]))
        assert stored.cost_usd_micros == 629
        # payload-asserted org lands in residue; the row's org_id came from the token (§4.5)
        assert stored.org_id == "org-1"
        assert stored.attrs["organization.id"] == "payload-org"


@requires_db
def test_api_request_tolerates_a_sparse_record(cfg):
    """Only vendor/request_id/org_id/received_at are required: one malformed record must
    not be able to abort a whole export body (§4.1 partialSuccess)."""
    operations.init(cfg, force=True)
    with session_scope() as s:
        s.add(OtelApiRequest(vendor="gen_ai", request_id="r-sparse", org_id="org-1",
                             attrs={}, received_at=NOW))
    with session_scope() as s:
        sparse = s.get(OtelApiRequest, ("gen_ai", "r-sparse"))
        assert sparse.session_id is None and sparse.model is None
        assert sparse.input_tokens is None and sparse.cost_usd_micros is None


@requires_db
def test_org_token_stores_only_the_hash(cfg):
    operations.init(cfg, force=True)
    with session_scope() as s:
        s.add(OrgToken(token_hash="h" * 64, org_id="org-1",
                       descriptor={"note": "acme fleet"}, created_at=NOW))
    with session_scope() as s:
        token = s.get(OrgToken, "h" * 64)
        assert token.revoked is False
        assert token.descriptor == {"note": "acme fleet"}
        # no column anywhere holds key material other than the hash (§4.5)
        columns = set(Base.metadata.tables["org_tokens"].columns.keys())
        assert columns == {"token_hash", "org_id", "descriptor", "created_at", "revoked"}
