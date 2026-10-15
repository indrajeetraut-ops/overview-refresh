"""api-latency plan §6.5: the Phase E migration (e1a4c9f83b26) applies clean up and down, and
the backfill it runs matches session_scalars.py on rows seeded with real JSON edge cases --
the same shapes test_session_scalars.py's SQL-fragment parity test covers, now for the
promoted columns instead of the Phase C query fragments."""
from datetime import datetime, timezone

from sqlalchemy import inspect, text

from alembic import command
from server.storage.dbm.operations import alembic_config
from server.storage.schema import Base
from server.storage import session_scalars

from .conftest import requires_db

_PRE_MIGRATION = "c4e8a2f1d7b3"   # drop_allowances -- the revision just before this one
_WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)

#: The same edge-case shapes as test_session_scalars.py's DB parity test.
_CASES: list[dict] = [
    dict(session_id="otel-missing", otel={}),
    dict(session_id="otel-empty-basis", otel={"cost_basis": {}, "cost_usd_micros": 100}),
    dict(session_id="otel-estimated", otel={"cost_basis": {"estimated": 50, "unknown": 0},
                                            "cost_usd_micros": 200}),
    dict(session_id="otel-all-vendor-priced", otel={"cost_basis": {"estimated": 0, "unknown": 0},
                                                     "cost_usd_micros": 999}),
    dict(session_id="recoverable-null-blocks",
        info={"resume_cost": None, "cache_busts": None, "interrupted": None}),
    dict(session_id="recoverable-priced",
        info={"resume_cost": {"total_recoverable_usd_micros": 111},
              "cache_busts": {"total_usd_micros": 22},
              "interrupted": {"total_usd_micros": 3}}),
    dict(session_id="reasoning-null", info={"reasoning_tokens": None}),
    dict(session_id="reasoning-positive", info={"reasoning_tokens": 77}),
    dict(session_id="files-empty", files={}),
    dict(session_id="files-nonempty", files={"a.py": {}}),
    dict(session_id="segments-missing", info={}),
    dict(session_id="segments-tie",
        info={"segments": [{"label": "A", "turns": 5}, {"label": "B", "turns": 5}]}),
    dict(session_id="segments-list-turns",
        info={"segments": [{"label": "X", "turns": [1, 2, 3, 4]}, {"label": "Y", "turns": 2}]}),
]


def _columns(engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("session_info")}


def _seed_pre_migration_rows(engine) -> None:
    """Inserted as raw SQL, not the ORM: at `_PRE_MIGRATION`, `SessionInfo` (the live model)
    already declares the new columns this migration adds, so an ORM insert would send columns
    the table doesn't have yet."""
    import json
    with engine.connect() as conn:
        for case in _CASES:
            sid = case["session_id"]
            conn.execute(text(
                "INSERT INTO sessions (session_id, producer_id, org_id, user_id, agent, status, "
                "started_at, last_event_at, ended_at, signal_blob, info_blob, parent_session_id, "
                "subagents) VALUES (:sid, 'p', 'default', 'u1', 'claude_code', 'ended', :when, "
                ":when, NULL, '{}', '{}', NULL, '[]')"
            ), {"sid": sid, "when": _WHEN})
            conn.execute(text(
                "INSERT INTO session_info (session_id, cost_usd, models, tools, files, skills, "
                "subagents, pr_list, turns, turn_count, long_turn_count, tokens_total, "
                "compaction_count, subagent_count, error_count, info, otel, "
                "cost_effective_usd_micros, datasource, jsonl_lag_s, repo_id, "
                "repo_id_provenance, created_at, updated_at) VALUES "
                "(:sid, NULL, '{}', '{}', :files, '[]', '{}', '[]', '[]', 0, 0, 0, 0, 0, 0, "
                ":info, :otel, 0, 'both', NULL, NULL, NULL, :when, :when)"
            ), {"sid": sid, "when": _WHEN,
               "files": json.dumps(case.get("files", {})),
               "info": json.dumps(case.get("info", {})),
               "otel": json.dumps(case.get("otel", {}))})
        conn.commit()


@requires_db
def test_migration_backfills_matching_session_scalars(engine, cfg):
    Base.metadata.drop_all(engine)
    with engine.connect() as conn:
        conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        conn.commit()

    acfg = alembic_config(cfg)
    try:
        command.upgrade(acfg, _PRE_MIGRATION)
        assert "recoverable_resume_usd_micros" not in _columns(engine)
        _seed_pre_migration_rows(engine)

        command.upgrade(acfg, "head")
        new_cols = {"otel_vendor_cost_usd_micros", "recoverable_resume_usd_micros",
                   "recoverable_cache_bust_usd_micros", "recoverable_interrupted_usd_micros",
                   "reasoning_tokens", "has_files", "session_type"}
        assert new_cols <= _columns(engine)

        with engine.connect() as conn:
            rows = {r.session_id: r for r in conn.execute(text(
                "SELECT session_id, otel_vendor_cost_usd_micros, recoverable_resume_usd_micros, "
                "recoverable_cache_bust_usd_micros, recoverable_interrupted_usd_micros, "
                "reasoning_tokens, has_files, session_type FROM session_info"
            ))}
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

        command.downgrade(acfg, _PRE_MIGRATION)
        assert not (new_cols & _columns(engine))

        command.upgrade(acfg, "head")
        assert new_cols <= _columns(engine)
    finally:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
