"""api-latency plan §5.1: pins every SQL mirror (Phase C's guarded fragments in
config/queries/manager.yaml) to the Python definitions in session_scalars.py. Unit tests over
the pure functions (no DB) come first; the DB-requiring parity tests below run the actual
catalog queries and check they agree with the same functions applied to the same JSON.
"""
from datetime import datetime, timezone

from server.storage import session_scalars
from server.storage.engine import session_scope
from server.storage.queries import QueryCatalog
from server.storage.schema import Base
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo

from .conftest import requires_db

QUERIES_DIR = "config/queries"

# ------------------------------------------------------------------------------------------
# Unit tests: the pure functions, no DB
# ------------------------------------------------------------------------------------------


def test_priced_total_reads_the_key_missing_null_or_unpriced_is_zero():
    assert session_scalars.priced_total({"total_usd_micros": 500}, "total_usd_micros") == 500
    assert session_scalars.priced_total({"total_usd_micros": 0}, "total_usd_micros") == 0
    assert session_scalars.priced_total({"total_usd_micros": None}, "total_usd_micros") == 0
    assert session_scalars.priced_total({}, "total_usd_micros") == 0
    assert session_scalars.priced_total(None, "total_usd_micros") == 0


def test_recoverable_buckets_are_uncapped_and_default_to_zero():
    info = {"resume_cost": {"total_recoverable_usd_micros": 100},
           "cache_busts": {}, "interrupted": None}
    assert session_scalars.recoverable_buckets(info) == {
        "resume": 100, "cache_bust": 0, "interrupted": 0,
    }
    assert session_scalars.recoverable_buckets(None) == {
        "resume": 0, "cache_bust": 0, "interrupted": 0,
    }
    assert session_scalars.recoverable_buckets({}) == {
        "resume": 0, "cache_bust": 0, "interrupted": 0,
    }


def test_reasoning_tokens_none_means_never_estimated_not_zero():
    assert session_scalars.reasoning_tokens({"reasoning_tokens": 0}) == 0
    assert session_scalars.reasoning_tokens({"reasoning_tokens": None}) is None
    assert session_scalars.reasoning_tokens({}) is None
    assert session_scalars.reasoning_tokens(None) is None


def test_has_files():
    assert session_scalars.has_files({"a.py": {}}) is True
    assert session_scalars.has_files({}) is False
    assert session_scalars.has_files(None) is False


def test_segment_turns_number_list_and_missing():
    assert session_scalars.segment_turns({"turns": 5}) == 5
    assert session_scalars.segment_turns({"turns": 5.0}) == 5
    assert session_scalars.segment_turns({"turns": [1, 2, 3]}) == 3
    assert session_scalars.segment_turns({"turns": []}) == 0
    assert session_scalars.segment_turns({}) == 0
    assert session_scalars.segment_turns({"turns": "not-a-number"}) == 0


def test_dominant_segment_label_first_max_wins_on_a_tie():
    segments = [{"label": "A", "turns": 5}, {"label": "B", "turns": 5}]
    assert session_scalars.dominant_segment_label(segments) == "A"


def test_dominant_segment_label_handles_list_turns_and_missing_label():
    # the list-shaped segment has 3 turns (len), beating the number-shaped one's 2
    segments = [{"label": "A", "turns": [1, 2, 3]}, {"label": "B", "turns": 2}]
    assert session_scalars.dominant_segment_label(segments) == "A"
    assert session_scalars.dominant_segment_label([{"turns": 9}]) is None  # no label on the winner
    assert session_scalars.dominant_segment_label([]) is None
    assert session_scalars.dominant_segment_label(None) is None


def test_otel_vendor_cost_none_on_estimated_unknown_or_empty_basis():
    assert session_scalars.otel_vendor_cost(None) is None
    assert session_scalars.otel_vendor_cost({}) is None
    assert session_scalars.otel_vendor_cost({"cost_basis": {}, "cost_usd_micros": 100}) is None
    assert session_scalars.otel_vendor_cost(
        {"cost_basis": {"estimated": 1, "unknown": 0}, "cost_usd_micros": 100}) is None
    assert session_scalars.otel_vendor_cost(
        {"cost_basis": {"estimated": 0, "unknown": 1}, "cost_usd_micros": 100}) is None
    assert session_scalars.otel_vendor_cost(
        {"cost_basis": {"estimated": 0, "unknown": 0}, "cost_usd_micros": 100}) == 100
    assert session_scalars.otel_vendor_cost(
        {"cost_basis": {"estimated": 0, "unknown": 0}, "cost_usd_micros": None}) is None


def test_fold_scalars_bundles_every_fold_owned_column():
    info = {
        "resume_cost": {"total_recoverable_usd_micros": 10},
        "cache_busts": {"total_usd_micros": 5},
        "interrupted": {"total_usd_micros": 1},
        "reasoning_tokens": 42,
        "segments": [{"label": "PLAN", "turns": 3}],
    }
    assert session_scalars.fold_scalars(info, {"a.py": {}}) == {
        "recoverable_resume_usd_micros": 10,
        "recoverable_cache_bust_usd_micros": 5,
        "recoverable_interrupted_usd_micros": 1,
        "reasoning_tokens": 42,
        "has_files": True,
        "session_type": "PLAN",
    }
    assert session_scalars.fold_scalars(None, None) == {
        "recoverable_resume_usd_micros": 0,
        "recoverable_cache_bust_usd_micros": 0,
        "recoverable_interrupted_usd_micros": 0,
        "reasoning_tokens": None,
        "has_files": False,
        "session_type": None,
    }


# ------------------------------------------------------------------------------------------
# DB parity: the guarded SQL fragments (Phase C) vs. the same functions over the same JSON
# ------------------------------------------------------------------------------------------

_USER_ID = "parity-user"
_WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)
_FRM = datetime(2026, 1, 1, tzinfo=timezone.utc)
_TO = datetime(2026, 2, 1, tzinfo=timezone.utc)

#: Every JSON shape §5.1 calls for. `otel`/`info`/`files` default to `{}` when not overridden.
_CASES: list[dict] = [
    # otel.cost_basis shapes
    dict(session_id="otel-missing", otel={}),
    dict(session_id="otel-empty-basis", otel={"cost_basis": {}, "cost_usd_micros": 100}),
    dict(session_id="otel-estimated", otel={"cost_basis": {"estimated": 50, "unknown": 0},
                                            "cost_usd_micros": 200}),
    dict(session_id="otel-unknown", otel={"cost_basis": {"estimated": 0, "unknown": 30},
                                          "cost_usd_micros": 200}),
    dict(session_id="otel-all-vendor-null-cost", otel={"cost_basis": {"estimated": 0, "unknown": 0},
                                                        "cost_usd_micros": None}),
    dict(session_id="otel-all-vendor-priced", otel={"cost_basis": {"estimated": 0, "unknown": 0},
                                                     "cost_usd_micros": 999}),
    # resume_cost / cache_busts / interrupted shapes
    dict(session_id="recoverable-missing", info={}),
    dict(session_id="recoverable-null-blocks",
        info={"resume_cost": None, "cache_busts": None, "interrupted": None}),
    dict(session_id="recoverable-null-totals",
        info={"resume_cost": {"total_recoverable_usd_micros": None},
              "cache_busts": {"total_usd_micros": None},
              "interrupted": {"total_usd_micros": None}}),
    dict(session_id="recoverable-zero-totals",
        info={"resume_cost": {"total_recoverable_usd_micros": 0},
              "cache_busts": {"total_usd_micros": 0},
              "interrupted": {"total_usd_micros": 0}}),
    dict(session_id="recoverable-priced",
        info={"resume_cost": {"total_recoverable_usd_micros": 111},
              "cache_busts": {"total_usd_micros": 22},
              "interrupted": {"total_usd_micros": 3}}),
    # reasoning_tokens shapes
    dict(session_id="reasoning-missing", info={}),
    dict(session_id="reasoning-null", info={"reasoning_tokens": None}),
    dict(session_id="reasoning-zero", info={"reasoning_tokens": 0}),
    dict(session_id="reasoning-positive", info={"reasoning_tokens": 77}),
    # files shapes
    dict(session_id="files-empty", files={}),
    dict(session_id="files-nonempty", files={"a.py": {}}),
    # segments shapes
    dict(session_id="segments-missing", info={}),
    dict(session_id="segments-empty", info={"segments": []}),
    dict(session_id="segments-one", info={"segments": [{"label": "PLAN", "turns": 4}]}),
    dict(session_id="segments-tie",
        info={"segments": [{"label": "A", "turns": 5}, {"label": "B", "turns": 5}]}),
    dict(session_id="segments-list-turns",
        info={"segments": [{"label": "X", "turns": [1, 2, 3, 4]}, {"label": "Y", "turns": 2}]}),
    dict(session_id="segments-missing-turns", info={"segments": [{"label": "Z"}]}),
    dict(session_id="segments-missing-label", info={"segments": [{"turns": 9}]}),
]


def _seed(s) -> None:
    """Populates both the JSON and the promoted columns (api-latency plan Phase E), the same
    as `persist_session_info`/`otel_trueup` would -- `scope_session_facts` reads the columns
    directly now, not a SQL mirror of the JSON, so a fixture that only set `info`/`otel`/
    `files` would read back defaults instead of these cases' real values."""
    for case in _CASES:
        sid = case["session_id"]
        otel = case.get("otel", {})
        info = case.get("info", {})
        files = case.get("files", {})
        s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=_USER_ID,
                         agent="claude_code", status="ended", started_at=_WHEN,
                         last_event_at=_WHEN, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfo(session_id=sid, created_at=_WHEN, updated_at=_WHEN, models={},
                          tools={}, files=files, skills=[], subagents={}, pr_list=[], turns=[],
                          turn_count=0, long_turn_count=0, info=info, otel=otel,
                          datasource="both", cost_effective_usd_micros=0, tokens_total=0,
                          otel_vendor_cost_usd_micros=session_scalars.otel_vendor_cost(otel),
                          **session_scalars.fold_scalars(info, files)))


@requires_db
def test_scope_session_facts_matches_session_scalars_on_every_shape(engine):
    """Now that Phase E has promoted these to real columns, `scope_session_facts` just selects
    them -- the nontrivial SQL-mirrors-Python parity check is the backfill migration's own test
    (test_session_info_scalar_columns_migration.py). This pins the query's column list/shape."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    with session_scope() as s:
        _seed(s)
    with session_scope() as s:
        rows = {r.session_id: r for r in s.execute(
            catalog.get("manager", "scope_session_facts"),
            {"user_ids": [_USER_ID], "from": _FRM, "to": _TO},
        )}

    assert len(rows) == len(_CASES)
    for case in _CASES:
        sid = case["session_id"]
        otel, info, files = case.get("otel", {}), case.get("info", {}), case.get("files", {})
        row = rows[sid]
        assert row.otel_vendor_cost_usd_micros == session_scalars.otel_vendor_cost(otel), sid
        buckets = session_scalars.recoverable_buckets(info)
        assert row.recoverable_resume_usd_micros == buckets["resume"], sid
        assert row.recoverable_cache_bust_usd_micros == buckets["cache_bust"], sid
        assert row.recoverable_interrupted_usd_micros == buckets["interrupted"], sid
        assert row.reasoning_tokens == session_scalars.reasoning_tokens(info), sid
        assert row.has_files == session_scalars.has_files(files), sid
        assert row.session_type == session_scalars.dominant_segment_label(
            info.get("segments") or []), sid


@requires_db
def test_person_session_facts_channel_fragments(engine):
    """D7's person/PR-only fragments (title, channel_priced, channel_*) -- no Python mirror
    exists for these (D6 lists no such function; they're never promoted to columns), so this
    pins the SQL's own guarded logic directly against hand-computed expectations."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    cases = {
        "priced": {
            "info": {
                "title": "fix the thing",
                "channel_cost": {"recache": {"usd_micros": 5}, "output": {"usd_micros": 10},
                                 "input": {"usd_micros": 2}},
                "carry": {"carry_usd_micros": 7},
            },
            "expect": {"title": "fix the thing", "channel_priced": True,
                      "channel_recache_usd_micros": 5, "channel_output_usd_micros": 10,
                      "channel_input_usd_micros": 2, "channel_carry_usd_micros": 7},
        },
        "missing-carry": {
            "info": {"channel_cost": {"output": {"usd_micros": 10}}},
            "expect": {"title": None, "channel_priced": False,
                      "channel_recache_usd_micros": 0, "channel_output_usd_micros": 10,
                      "channel_input_usd_micros": 0, "channel_carry_usd_micros": 0},
        },
        "null-carry": {
            "info": {"channel_cost": {"output": {"usd_micros": 10}}, "carry": None},
            "expect": {"title": None, "channel_priced": False,
                      "channel_recache_usd_micros": 0, "channel_output_usd_micros": 10,
                      "channel_input_usd_micros": 0, "channel_carry_usd_micros": 0},
        },
        "both-missing": {
            "info": {},
            "expect": {"title": None, "channel_priced": False,
                      "channel_recache_usd_micros": 0, "channel_output_usd_micros": 0,
                      "channel_input_usd_micros": 0, "channel_carry_usd_micros": 0},
        },
    }
    with session_scope() as s:
        for sid, case in cases.items():
            s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=_USER_ID,
                             agent="claude_code", status="ended", started_at=_WHEN,
                             last_event_at=_WHEN, signal_blob={}, info_blob={}, subagents=[]))
            s.add(SessionInfo(session_id=sid, created_at=_WHEN, updated_at=_WHEN, models={},
                              tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                              turn_count=0, long_turn_count=0, info=case["info"], otel={},
                              datasource="both", cost_effective_usd_micros=0, tokens_total=0))
    with session_scope() as s:
        rows = {r.session_id: r for r in s.execute(
            catalog.get("manager", "person_session_facts"),
            {"user_ids": [_USER_ID], "from": _FRM, "to": _TO},
        )}

    for sid, case in cases.items():
        row = rows[sid]
        for field, expected in case["expect"].items():
            assert getattr(row, field) == expected, f"{sid}.{field}"


@requires_db
def test_scope_model_split_matches_model_split_over_the_full_rows(engine):
    """A mix of ledger, JSONL-only, no-requests-key and empty-requests sessions: the SQL
    aggregate must agree with `aggregate.model_split` over the same rows (§5.1's own
    requirement) -- this is the parity the plan's probe caught a bug in (dropping JSONL-only
    sessions when `otel.requests` was merely absent, not empty)."""
    from server.api.manager.aggregate import model_split
    from server.api.manager.aggregate import model_split_from_rows

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    catalog = QueryCatalog.load(QUERIES_DIR)
    cases = {
        "ledger": {
            "otel": {"requests": [
                {"model": "claude-opus-5", "input_tokens": 100, "output_tokens": 20,
                 "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd_micros": 500},
                {"model": "claude-opus-5", "input_tokens": 50, "output_tokens": 10,
                 "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_est_usd_micros": 90},
            ]},
            "models": {},
        },
        "jsonl-only-no-requests-key": {
            "otel": {},
            "models": {"claude-sonnet-5": {"input": 200, "output": 40, "cache_read": 0, "cache_write": 0}},
        },
        "jsonl-only-empty-requests": {
            "otel": {"requests": []},
            "models": {"claude-haiku-4-5-20251001": {"input": 30, "output": 5, "cache_read": 0,
                                                      "cache_write": 0}},
        },
    }
    with session_scope() as s:
        for sid, case in cases.items():
            s.add(SessionRow(session_id=sid, producer_id="p", org_id="default", user_id=_USER_ID,
                             agent="claude_code", status="ended", started_at=_WHEN,
                             last_event_at=_WHEN, signal_blob={}, info_blob={}, subagents=[]))
            s.add(SessionInfo(session_id=sid, created_at=_WHEN, updated_at=_WHEN,
                              models=case["models"], tools={}, files={}, skills=[], subagents={},
                              pr_list=[], turns=[], turn_count=0, long_turn_count=0, info={},
                              otel=case["otel"], datasource="both",
                              cost_effective_usd_micros=0, tokens_total=0))
    with session_scope() as s:
        sql_rows = s.execute(catalog.get("manager", "scope_model_split"),
                             {"user_ids": [_USER_ID], "from": _FRM, "to": _TO}).all()
        full_rows = s.execute(catalog.get("manager", "sessions_by_ids"),
                              {"session_ids": list(cases)}).all()

    assert model_split_from_rows(sql_rows) == model_split(full_rows)
