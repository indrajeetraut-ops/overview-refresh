"""The before/after pricing report (#68 T7): stored computed costs vs `Pricer` on the same tokens.

Rows are seeded straight into the tables (the report is read-only and reads what the true-up
and the fold persisted), with "before" numbers written at the *legacy* price table.
"""
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text

from server.storage import session_scope
from server.storage.schema import OtelApiRequest
from server.storage.schema import Session as SessionRow
from server.storage.schema import SessionInfo as SessionInfoRow
from server.worker import cli_pricing_report as report

from .conftest import DSN, requires_db

pytestmark = requires_db

ORG = "org-acme"


def _dt(day: str) -> datetime:
    return datetime.fromisoformat(day).replace(tzinfo=timezone.utc)


def _req(rid, model, event_day, *, est, vendor=None, input_tokens=100_000, output_tokens=50_000):
    return {"request_id": rid, "sources": "otel_only", "model": model,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "cost_usd_micros": vendor, "cost_est_usd_micros": est, "query_source": None}


def _seed_session(s, sid, started, requests=(), *, models=None, info=None):
    s.add(SessionRow(session_id=sid, producer_id="p", org_id=ORG, user_id="u1",
                     agent="claude_code", status="live", started_at=_dt(started),
                     last_event_at=_dt(started), signal_blob={}, info_blob={}, subagents=[]))
    s.flush()
    for r in requests:
        s.add(OtelApiRequest(
            vendor="claude_code", request_id=r["request_id"], session_id=sid, org_id=ORG,
            model=r["model"], input_tokens=r["input_tokens"], output_tokens=r["output_tokens"],
            cache_read_tokens=0, cache_creation_tokens=0, cost_usd_micros=r["cost_usd_micros"],
            event_ts=r["_ts"], attrs={}, received_at=r["_ts"]))
    block = {"vendor": "claude_code",
             "requests": [{k: v for k, v in r.items() if k != "_ts"} for r in requests]} if requests else {}
    s.add(SessionInfoRow(session_id=sid, created_at=_dt(started), updated_at=_dt(started),
                         models=models or {}, tools={}, files={}, skills=[], subagents={},
                         pr_list=[], turns=[], turn_count=0, long_turn_count=0,
                         info=info or {}, otel=block, datasource="both"))


def _with_ts(reqs, day):
    for r in reqs:
        r["_ts"] = _dt(day)
    return reqs


@pytest.fixture
def seeded(engine):
    with session_scope() as s:
        # Sonnet 5 at the LEGACY blocks: 2/10 before 2026-09-01, 3/15 from it (design §4 #2).
        # 100k input + 50k output: 0.2+0.5 = 700_000 micros old-early, 0.3+0.75 = 1_050_000 old-late.
        _seed_session(s, "sonnet-aug", "2026-08-15",
                      _with_ts([_req("a1", "claude-sonnet-5", "2026-08-15", est=700_000)], "2026-08-15"))
        _seed_session(s, "sonnet-sep", "2026-09-10",
                      _with_ts([_req("b1", "claude-sonnet-5", "2026-09-10", est=1_050_000)], "2026-09-10"))
        # Opus 5 and Haiku 4.5 (dated id) agree with the catalog: 5/25 -> 1_750_000; 1/5 -> 350_000.
        _seed_session(s, "opus", "2026-10-01",
                      _with_ts([_req("c1", "claude-opus-5", "2026-10-01", est=1_750_000)], "2026-10-01"))
        _seed_session(s, "haiku", "2026-10-01",
                      _with_ts([_req("d1", "claude-haiku-4-5-20251001", "2026-10-01", est=350_000)], "2026-10-01"))
        # A dated model string the legacy table could not price (stored estimate null): §4 #8.
        _seed_session(s, "dated", "2026-10-01",
                      _with_ts([_req("e1", "claude-sonnet-4-5-20250929", "2026-10-01", est=None)], "2026-10-01"))
        # A vendor-priced request with a wildly stale estimate: informational drift only.
        _seed_session(s, "vendor", "2026-10-01",
                      _with_ts([_req("f1", "claude-opus-5", "2026-10-01", est=1, vendor=999_999)], "2026-10-01"))
        # Fold money at the legacy table: Sonnet 5 on 2026-09-10 (3/15), one cache bust on Opus 5.
        _seed_session(s, "fold", "2026-09-10", models={
            "claude-sonnet-5": {"input": 100_000, "output": 50_000, "cache_read": 0, "cache_write": 0}},
            info={"channel_cost": {"input": {"tokens": 100_000, "usd_micros": 300_000},
                                   "output": {"tokens": 50_000, "usd_micros": 750_000},
                                   "recache": {"tokens": 0, "usd_micros": 0}},
                  "cache_busts": {"events": [{"model": "claude-opus-5", "rewrite_tokens": 1_000_000,
                                              "usd_micros": 10_000_000}], "count": 1,
                                  "total_usd_micros": 10_000_000}})
    return DSN


def _run(capsys, dsn):
    assert report.main(["--dsn", dsn, "--queries-dir", "config/queries"]) == 0
    out = capsys.readouterr().out
    rows = {}
    for line in out.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == 11 and cells[0] not in ("source", "---"):
            rows[(cells[0], cells[1], cells[2])] = cells
    return out, rows


def test_ratios_and_causes(seeded, capsys):
    out, rows = _run(capsys, seeded)
    # cells: source, model, month, items, before, after, ratio, unpriced before, after, cause, drift
    assert rows[("ledger", "claude-sonnet-5", "2026-08")][6] == "1.000"
    sep = rows[("ledger", "claude-sonnet-5", "2026-09")]
    assert sep[6] == "0.667" and sep[9] == "#2 x1"                   # decision #2
    assert rows[("ledger", "claude-opus-5", "2026-10")][6] == "1.000"
    assert rows[("ledger", "claude-haiku-4-5-20251001", "2026-10")][6] == "1.000"


def test_unpriced_before_dated_model_is_cause_8(seeded, capsys):
    _, rows = _run(capsys, seeded)
    row = rows[("ledger", "claude-sonnet-4-5-20250929", "2026-10")]
    assert row[7] == "1" and row[8] == "0"                           # unpriced before / after
    assert row[9] == "#8 x1"


def test_a_vendor_priced_request_never_moves_the_ratios(seeded, capsys):
    _, rows = _run(capsys, seeded)
    opus = rows[("ledger", "claude-opus-5", "2026-10")]
    assert opus[3] == "1" and opus[6] == "1.000"                     # the vendor row is not an item
    assert "(n=1)" in opus[10]                                       # but shows in the drift column


def test_fold_money_is_repriced_per_source(seeded, capsys):
    _, rows = _run(capsys, seeded)
    channel = rows[("fold:channel_cost", "claude-sonnet-5", "2026-09")]
    assert channel[4] == "1050000" and channel[5] == "700000" and channel[6] == "0.667"
    assert channel[9] == "#2 x1"
    bust = rows[("fold:cache_busts", "claude-opus-5", "2026-09")]
    assert bust[6] == "1.000"


def test_the_report_is_read_only(seeded, capsys):
    from sqlalchemy import create_engine

    def counts():
        with create_engine(seeded).connect() as c:
            return [c.execute(text(f"select count(*) from {t}")).scalar()
                    for t in ("sessions", "session_info", "otel_api_requests")]

    before = counts()
    _run(capsys, seeded)
    assert counts() == before
