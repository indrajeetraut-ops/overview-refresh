"""HTTP-level and service-level tests for `GET /v1/forecast` (T6, plan §3.6/§3.8).

A run is seeded through the real loader (`parse_export` + `replace_run`) from a hand-built export
(`forecast_fixtures.py`); actuals come from seeded sessions. Expected numbers are worked out by
hand in the comments: the example scope `default` (org) has `base` $300/day, so the October
forecast part (23 days, Oct 9..31) has total quantile `6.9e9 * (1 + 0.05 i)` for grid position
`i` (mean = the median row, 10.35e9), and November (30 days) `9e9 * (1 + 0.05 i)`.
"""
from datetime import date, datetime, timezone

import pytest
import structlog
from fastapi.testclient import TestClient
from sqlalchemy import text
from structlog.testing import capture_logs

from server.api import forecast as fc
from server.api.app import create_app
from server.api.config import ApiConfig, ForecastConfig
from server.api.errors import BadRequestError
from server.api.manager import aggregate
from server.api.manager.service import budget_forecast, economics
from server.storage import session_scope

from .conftest import requires_db
from .forecast_fixtures import (
    GRID, add_session, load_export, make_export, seed_directory, utc,
)

ADMIN = {"x-talium-viewer": "admin1"}
GET = "/v1/forecast"
ORG = {"scope_type": "org", "scope_id": "default"}


@pytest.fixture
def client(api_config, storage_config, engine):
    return TestClient(create_app(api_config, storage_config))


@pytest.fixture
def seeded(engine, catalog, tmp_path):
    """Directory + the default run (data through 2026-10-08, Asia/Kolkata)."""
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(), tmp_path, catalog)


def _forecast(client, params=ORG, headers=ADMIN):
    resp = client.get(GET, params=params, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _series(body, preset="baseline"):
    return next(x for x in body["data"]["forecast"]["series"] if x["preset_id"] == preset)


def _put_budget(client, scope_type, scope_id, amount):
    resp = client.put(f"/v1/budgets/{scope_type}/{scope_id}", json={"amount_usd": str(amount)},
                      headers=ADMIN)
    assert resp.status_code == 200, resp.text


# ------------------------------------------------------------------------------ shape

@requires_db
def test_response_shape_presets_and_series_order(client, seeded):
    body = _forecast(client)
    assert body["unavailable"] is None
    data = body["data"]
    assert (data["scope_type"], data["scope_id"]) == ("org", "default")
    f = data["forecast"]
    assert f["origin"] == "2026-09-30" and f["data_through"] == "2026-10-08"
    assert f["timezone"] == "Asia/Kolkata" and (f["band_low"], f["band_high"]) == (10, 90)
    assert f["budget_usd_micros"] is None
    assert [p["id"] for p in f["presets"]] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]
    assert f["presets"][0] == {"id": "baseline", "label": "Current trajectory", "group": None,
                               "description": "No change.", "assumptions": ["fixed_mix_estimate"]}
    assert [x["preset_id"] for x in f["series"]] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]
    for ser in f["series"]:
        assert ser["status"] == "complete"
        assert len(ser["daily"]) == 61                       # Oct 1 .. Nov 30
        assert [(p["start"], p["end"], p["forecast_from"]) for p in ser["periods"]] == [
            ("2026-10-01", "2026-10-31", "2026-10-09"), ("2026-11-01", "2026-11-30", "2026-11-01")]
    daily = _series(body)["daily"]
    # base 300e6 * factor 1.0 per day; low = half, high = double (builder rule)
    assert daily[0] == {"date": "2026-10-01", "expected_usd_micros": 300_000_000,
                        "low_usd_micros": 150_000_000, "high_usd_micros": 600_000_000}
    assert daily[-1]["date"] == "2026-11-30"
    assert _series(body, "usage-up-20")["daily"][0]["expected_usd_micros"] == 360_000_000


@requires_db
def test_actual_daily_is_zero_filled_from_28_days_before_the_first_period(client, seeded):
    f = _forecast(client)["data"]["forecast"]
    days = f["actual_daily"]
    assert days[0]["date"] == "2026-09-03" and days[-1]["date"] == "2026-10-08"
    assert len(days) == 36
    assert all(d["usd_micros"] == 0 for d in days)


# ------------------------------------------------------------------------------ null cases

@requires_db
def test_forecast_is_null_when_no_run_is_loaded(client, engine, catalog):
    with session_scope() as s:
        seed_directory(s, catalog)
    body = _forecast(client)
    assert body["data"] == {"scope_type": "org", "scope_id": "default", "forecast": None}
    assert body["mode"] == {"modes_present": [], "sessions_by_mode": {}}
    assert body["unavailable"] is None


@requires_db
def test_forecast_is_null_for_a_scope_outside_the_run(client, seeded, catalog):
    with session_scope() as s:
        from server.directory import TeamRecord, sync
        sync(s, catalog, [], teams=[TeamRecord("other-team", "default", "Other")])
    for params in ({"scope_type": "team", "scope_id": "other-team"},
                   {"scope_type": "org", "scope_id": "nope"}):
        body = _forecast(client, params)
        assert body["data"]["forecast"] is None
        assert body["data"]["scope_id"] == params["scope_id"]


@requires_db
def test_the_latest_loaded_run_wins(client, seeded, catalog, tmp_path):
    other = make_export(tz="UTC", generated_at="2026-10-08T11:00:00Z")
    # a second run row with a later loaded_at but loaded behind the loader's back: the API takes
    # the greatest loaded_at
    with session_scope() as s:
        s.execute(text("INSERT INTO forecast_runs SELECT 'zz-new', origin, data_through, generated_at, "
                       "jsonb_set(meta, '{timezone}', '\"UTC\"'), loaded_at + interval '1 hour' "
                       "FROM forecast_runs"))
        s.execute(text("INSERT INTO forecast_scope_series SELECT 'zz-new', scope_type, scope_id, "
                       "preset_id, status, payload FROM forecast_scope_series"))
    assert other["timezone"] == "UTC"
    assert _forecast(client)["data"]["forecast"]["timezone"] == "UTC"


# ------------------------------------------------------------------------------ gate

@requires_db
def test_gate_behaves_as_economics(client, seeded):
    org = ORG
    assert client.get(GET, params=org).status_code == 401                       # no viewer
    assert client.get(GET, params=org, headers={"x-talium-viewer": "ghost"}).status_code == 401
    assert client.get(GET, params=org, headers={"x-talium-viewer": "dev1"}).status_code == 403
    assert client.get(GET, params=org, headers=ADMIN).status_code == 200
    # a lead is a manager: org ok; own team ok; another team forbidden
    lead = {"x-talium-viewer": "lead1"}
    assert client.get(GET, params=org, headers=lead).status_code == 200
    assert client.get(GET, params={"scope_type": "team", "scope_id": "platform"},
                      headers=lead).status_code == 200
    assert client.get(GET, params={"scope_type": "team", "scope_id": "qa"},
                      headers=lead).status_code == 403
    # a developer outside any manager role cannot view even their own team
    assert client.get(GET, params={"scope_type": "team", "scope_id": "platform"},
                      headers={"x-talium-viewer": "dev1"}).status_code == 403
    # an unknown team is never visible, so it is forbidden (not a null forecast), even to an admin
    assert client.get(GET, params={"scope_type": "team", "scope_id": "nope"},
                      headers=ADMIN).status_code == 403


@requires_db
def test_unknown_scope_type_is_a_bad_request(client, seeded, engine, catalog):
    resp = client.get(GET, params={"scope_type": "bogus", "scope_id": "x"}, headers=ADMIN)
    assert resp.status_code == 400
    with session_scope() as s:
        with pytest.raises(BadRequestError):
            budget_forecast(s, catalog, ApiConfig(), "bogus", "x")


# ------------------------------------------------------------------------------ actuals

@requires_db
def test_actual_daily_buckets_by_the_run_timezone(client, seeded, catalog):
    """Asia/Kolkata is UTC+5:30, so local midnight is 18:30 UTC. Sessions near it land on the
    local day, not the UTC day. A subagent row counts like economics does."""
    with session_scope() as s:
        add_session(s, "a", "csv:dev1", 1_000_000, utc(2026, 10, 5, 18, 0))     # Oct 5 23:30 local
        add_session(s, "b", "csv:dev1", 20_000_000, utc(2026, 10, 5, 18, 31))   # Oct 6 00:01 local
        add_session(s, "c", "csv:dev2", 300_000_000, utc(2026, 10, 3, 22, 0))   # Oct 4 03:30 local
        add_session(s, "c-sub", "csv:dev2", 4_000_000, utc(2026, 10, 3, 22, 10), parent="c")
        add_session(s, "d", "csv:dev1", 50_000_000, utc(2026, 10, 8, 18, 40))   # Oct 9 local: after data_through
        add_session(s, "e", "csv:dev1", 600_000_000, utc(2026, 9, 2, 20, 0))    # Sep 3 01:30 local: first day
        add_session(s, "f", "csv:dev1", 7_000_000_000, utc(2026, 9, 2, 18, 0))  # Sep 2 23:30 local: before the range
    days = {d["date"]: d["usd_micros"] for d in _forecast(client)["data"]["forecast"]["actual_daily"]}
    assert days["2026-10-05"] == 1_000_000
    assert days["2026-10-06"] == 20_000_000
    assert days["2026-10-04"] == 304_000_000
    assert days["2026-09-03"] == 600_000_000
    assert "2026-09-02" not in days and "2026-10-09" not in days
    assert sum(days.values()) == 1_000_000 + 20_000_000 + 304_000_000 + 600_000_000
    zero = [d for d, v in days.items() if v == 0]
    assert len(zero) == 36 - 4                                   # zero-filled everywhere else


@requires_db
def test_actual_daily_equals_the_economics_fetch_for_the_same_window(engine, catalog, tmp_path):
    """One cost source: for a window of whole local days the forecast's actuals sum to what
    economics' own session fetch returns for the sessions that start in it."""
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(), tmp_path, catalog)
    with session_scope() as s:
        add_session(s, "w1", "csv:dev1", 11_000_000, utc(2026, 10, 2, 3, 0))
        add_session(s, "w2", "csv:dev2", 22_000_000, utc(2026, 10, 4, 17, 0))
        add_session(s, "w3", "csv:dev1", 33_000_000, utc(2026, 10, 5, 18, 0))
        add_session(s, "w3-sub", "csv:dev1", 4_000_000, utc(2026, 10, 5, 18, 5), parent="w3")
        add_session(s, "out-before", "csv:dev1", 500_000_000, utc(2026, 10, 1, 17, 0))   # Oct 1 local
        add_session(s, "out-after", "csv:dev1", 600_000_000, utc(2026, 10, 6, 18, 31))   # Oct 7 local
        # starts before the window (Oct 1 local) but is still running inside it: economics'
        # fetch (overlap) returns it, the per-day actuals attribute it to its start day
        add_session(s, "spanning", "csv:dev1", 900_000_000, utc(2026, 10, 1, 18, 0),
                    ended_at=utc(2026, 10, 2, 2, 0))
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Kolkata")
    first, last = date(2026, 10, 2), date(2026, 10, 6)
    frm, to = utc(2026, 10, 1, 18, 30), utc(2026, 10, 6, 18, 30)       # local midnights as instants
    with session_scope() as s:
        fetch, _users = aggregate.scope_cost_fetcher(s, catalog, "org", "default")
        rows = fetch(frm, to)
        started_inside = sum(r.cost_effective_usd_micros for r in rows if frm <= r.started_at < to)
        overlap_total = sum(r.cost_effective_usd_micros for r in rows)
    with session_scope() as s:
        out = budget_forecast(s, catalog, ApiConfig(), "org", "default")
    days = {d["date"]: d["usd_micros"] for d in out["data"]["forecast"]["actual_daily"]}
    window = sum(v for d, v in days.items() if first <= d <= last)
    assert window == started_inside == 11_000_000 + 22_000_000 + 33_000_000 + 4_000_000
    assert overlap_total == started_inside + 900_000_000       # the spanning session: boundary case
    assert days[date(2026, 10, 1)] >= 900_000_000                # attributed to its local start day
    assert aggregate.daily_actuals(rows, tz, first, last) == {
        date(2026, 10, 2): 11_000_000, date(2026, 10, 4): 22_000_000, date(2026, 10, 5): 37_000_000}   # w3 is 23:30 local on Oct 5


@requires_db
def test_actual_daily_matches_economics_budget_burn_in_a_utc_run(engine, catalog, tmp_path):
    """With a UTC run the local days are economics' own `budget_burn` days, so the cumulative
    actuals must agree to the micro-dollar."""
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(tz="UTC"), tmp_path, catalog)
    with session_scope() as s:
        add_session(s, "u1", "csv:dev1", 11_000_000, utc(2026, 10, 1, 23, 59))
        add_session(s, "u2", "csv:dev2", 22_000_000, utc(2026, 10, 2, 0, 1))
        add_session(s, "u3", "csv:dev1", 33_000_000, utc(2026, 10, 4, 12, 0))
        add_session(s, "u3-sub", "csv:dev1", 5_000_000, utc(2026, 10, 4, 12, 3), parent="u3")
    with session_scope() as s:
        econ = economics(s, catalog, ApiConfig(), "org", "default",
                         utc(2026, 9, 6), utc(2026, 10, 6, 12))
    burn = {p["day"]: p.get("actual_usd_micros") for p in econ["data"]["budget_burn"]}
    assert burn[5] == 11_000_000 + 22_000_000 + 33_000_000 + 5_000_000       # Oct 1..5 cumulative
    with session_scope() as s:
        out = budget_forecast(s, catalog, ApiConfig(), "org", "default")
    days = {d["date"]: d["usd_micros"] for d in out["data"]["forecast"]["actual_daily"]}
    assert sum(days[date(2026, 10, k)] for k in range(1, 6)) == burn[5]
    assert days[date(2026, 10, 1)] == 11_000_000 and days[date(2026, 10, 2)] == 22_000_000


@requires_db
def test_mode_counts_the_sessions_the_actuals_came_from(client, seeded):
    with session_scope() as s:
        add_session(s, "m1", "csv:dev1", 1, utc(2026, 10, 2, 5), datasource="both")
        add_session(s, "m2", "csv:dev2", 1, utc(2026, 10, 3, 5), datasource="jsonl_only")
        add_session(s, "m3", "csv:dev2", 1, utc(2026, 10, 4, 5), datasource="jsonl_only")
        add_session(s, "m4", "csv:dev2", 1, utc(2026, 7, 4, 5), datasource="otel_only")   # out of range
    body = _forecast(client)
    assert body["mode"] == {"modes_present": ["both", "jsonl_only"],
                            "sessions_by_mode": {"both": 1, "jsonl_only": 2}}


@requires_db
def test_team_and_repo_scopes_use_their_own_session_sets(client, engine, catalog, tmp_path):
    repo = "github:acme/auth-service"
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(), tmp_path, catalog)
    with session_scope() as s:
        add_session(s, "r1", "csv:dev1", 100_000_000, utc(2026, 10, 2, 5), repo_id=repo)
        add_session(s, "r2", "csv:dev2", 200_000_000, utc(2026, 10, 2, 6), repo_id=repo)
        add_session(s, "r3", "csv:dev1", 400_000_000, utc(2026, 10, 2, 7), repo_id="github:acme/other")
    def oct2(params):
        days = {d["date"]: d["usd_micros"] for d in _forecast(client, params)["data"]["forecast"]["actual_daily"]}
        return days["2026-10-02"]
    assert oct2({"scope_type": "team", "scope_id": "platform"}) == 500_000_000      # dev1, both repos
    assert oct2({"scope_type": "team", "scope_id": "qa"}) == 200_000_000
    # the repo id has ':' and '/': a plain query parameter, raw or percent-encoded
    assert oct2({"scope_type": "repo", "scope_id": repo}) == 300_000_000
    raw = client.get(f"{GET}?scope_type=repo&scope_id=github%3Aacme%2Fauth-service", headers=ADMIN)
    assert raw.status_code == 200 and raw.json()["data"]["forecast"] is not None
    assert raw.json()["data"]["scope_id"] == repo
    assert oct2(ORG) == 700_000_000


# ------------------------------------------------------------------------------ periods

@requires_db
def test_current_and_future_period_numbers(client, seeded):
    """Spend on the period's FIRST day, on `data_through` (early and late in the local day) and
    a session with no cost all count exactly; September and the day after `data_through` do not."""
    with session_scope() as s:
        add_session(s, "first", "csv:dev1", 40_000_000, utc(2026, 9, 30, 20, 0))   # Oct 1 01:30 local
        add_session(s, "p1", "csv:dev1", 1_000_000_000, utc(2026, 10, 2, 5))
        add_session(s, "nocost", "csv:dev2", None, utc(2026, 10, 3, 5))             # NULL cost counts 0
        add_session(s, "p2", "csv:dev2", 2_000_000_000, utc(2026, 10, 5, 5))
        add_session(s, "last-am", "csv:dev1", 7_000_000, utc(2026, 10, 8, 5))       # data_through, 10:30
        add_session(s, "last-pm", "csv:dev1", 3_000_000, utc(2026, 10, 8, 18, 25))  # data_through, 23:55
        add_session(s, "after", "csv:dev1", 90_000_000, utc(2026, 10, 8, 18, 35))   # Oct 9 local
        add_session(s, "sep30", "csv:dev1", 900_000_000, utc(2026, 9, 30, 18, 0))   # Sep 30 23:30 local
        add_session(s, "p0", "csv:dev1", 500_000_000, utc(2026, 9, 20, 5))          # September
    body = _forecast(client)
    cur, fut = _series(body)["periods"]
    actual = 40_000_000 + 1_000_000_000 + 2_000_000_000 + 7_000_000 + 3_000_000
    assert actual == 3_050_000_000
    assert cur["actual_to_date_usd_micros"] == actual
    days = {d["date"]: d["usd_micros"] for d in body["data"]["forecast"]["actual_daily"]}
    assert days["2026-10-01"] == 40_000_000 and days["2026-10-03"] == 0
    assert days["2026-10-08"] == 10_000_000 and "2026-10-09" not in days
    assert days["2026-09-30"] == 900_000_000
    # forecast part Oct 9..31 (23 days): total row 6.9e9 * (1 + 0.05 i); the median is the i=10
    # row, 10.35e9, and the mean is 10% above it (11.385e9: the builder's skewed distribution)
    assert cur["landing"] == {"expected_usd_micros": actual + 11_385_000_000,
                              "p10_usd_micros": actual + 7_590_000_000,        # i = 2: x1.10
                              "p50_usd_micros": actual + 10_350_000_000,       # i = 10: x1.50
                              "p90_usd_micros": actual + 13_110_000_000}       # i = 18: x1.90
    assert cur["landing"]["expected_usd_micros"] != cur["landing"]["p50_usd_micros"]
    first, last = cur["projected"][0], cur["projected"][-1]
    assert first["date"] == "2026-10-09" and last["date"] == "2026-10-31"
    assert len(cur["projected"]) == 23
    # day 0 row: 300e6 * (1 + 0.05 i): mean 1.1 * 450e6 = 495e6, low i=2 -> 330e6, high i=18 -> 570e6
    assert first == {"date": "2026-10-09", "expected_usd_micros": actual + 495_000_000,
                     "low_usd_micros": actual + 330_000_000, "high_usd_micros": actual + 570_000_000}
    assert last["expected_usd_micros"] == cur["landing"]["expected_usd_micros"]
    assert last["low_usd_micros"] == cur["landing"]["p10_usd_micros"]
    assert last["high_usd_micros"] == cur["landing"]["p90_usd_micros"]
    # November: nothing spent; 30 days -> total row 9e9 * (1 + 0.05 i), median 13.5e9, mean 14.85e9
    assert fut["actual_to_date_usd_micros"] == 0
    assert fut["forecast_from"] == fut["start"] == "2026-11-01"
    assert fut["landing"]["expected_usd_micros"] == 14_850_000_000
    assert fut["landing"]["p50_usd_micros"] == 13_500_000_000
    assert fut["landing"]["p10_usd_micros"] == 9_900_000_000
    assert fut["projected"][0]["date"] == "2026-11-01" and len(fut["projected"]) == 30
    assert fut["runway"] is None
    # no budget -> no probability and no runway
    assert cur["within_budget_probability"] is None and cur["runway"] is None
    assert body["data"]["forecast"]["budget_usd_micros"] is None


@requires_db
def test_baseline_delta_is_zero_and_presets_move_the_landing(client, seeded):
    body = _forecast(client)
    for period in range(2):
        assert _series(body)["periods"][period]["delta_vs_baseline_usd_micros"] == 0
    cur_up = _series(body, "usage-up-20")["periods"][0]
    cur_down = _series(body, "opus-to-sonnet-30")["periods"][0]
    # October total mean: baseline 11.385e9, x1.2 = 13.662e9, x0.9 = 10.2465e9
    assert cur_up["delta_vs_baseline_usd_micros"] == 2_277_000_000 > 0
    assert cur_down["delta_vs_baseline_usd_micros"] == -1_138_500_000 < 0
    assert (cur_up["landing"]["expected_usd_micros"]
            - _series(body)["periods"][0]["landing"]["expected_usd_micros"]) == 2_277_000_000
    assert _series(body, "usage-up-20")["periods"][1]["delta_vs_baseline_usd_micros"] == 2_970_000_000


@requires_db
def test_month_end_data_has_no_current_period(engine, catalog, tmp_path, client):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(data_through="2026-10-31"), tmp_path, catalog)
    with session_scope() as s:
        add_session(s, "me", "csv:dev1", 77_000_000, utc(2026, 10, 20, 5))
    _put_budget(client, "org", "default", 5000)
    body = _forecast(client)
    f = body["data"]["forecast"]
    assert f["origin"] == f["data_through"] == "2026-10-31"
    days = f["actual_daily"]
    assert days[0]["date"] == "2026-10-04" and days[-1]["date"] == "2026-10-31" and len(days) == 28
    assert sum(d["usd_micros"] for d in days) == 77_000_000
    for ser in f["series"]:
        nov, dec = ser["periods"]
        assert (nov["start"], nov["forecast_from"]) == ("2026-11-01", "2026-11-01")
        assert (dec["start"], dec["forecast_from"]) == ("2026-12-01", "2026-12-01")
        for p in (nov, dec):
            assert p["actual_to_date_usd_micros"] == 0
            assert p["runway"] is None
            assert p["within_budget_probability"] is not None       # the budget still applies
    nov = _series(body)["periods"][0]
    assert nov["landing"]["expected_usd_micros"] == 14_850_000_000
    assert nov["landing"]["p50_usd_micros"] == 13_500_000_000
    # budget 5e9 is below the whole November distribution (min 9e9): certain to exceed it
    assert nov["within_budget_probability"] == 0.0


@requires_db
def test_incomplete_status_has_no_numbers(engine, catalog, tmp_path, client):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(statuses={"platform": "incomplete"}), tmp_path, catalog)
    body = _forecast(client, {"scope_type": "team", "scope_id": "platform"})
    f = body["data"]["forecast"]
    assert [x["preset_id"] for x in f["series"]] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]
    for ser in f["series"]:
        assert ser["status"] == "incomplete" and ser["daily"] == [] and ser["periods"] == []
    assert len(f["actual_daily"]) == 36
    assert _series(_forecast(client))["status"] == "complete"          # the org is unaffected


# ------------------------------------------------------------------------------ budget + runway

@requires_db
def test_budget_edit_changes_probability_and_runway_on_the_next_request(client, seeded):
    with session_scope() as s:
        add_session(s, "b1", "csv:dev1", 3_000_000_000, utc(2026, 10, 2, 5))
    _put_budget(client, "org", "default", 7000)
    first = _forecast(client)
    f = first["data"]["forecast"]
    assert f["budget_usd_micros"] == 7_000_000_000
    cur = _series(first)["periods"][0]
    # remaining 4e9 is below the smallest outcome (6.9e9): certain to run out, from day 14 on
    assert cur["within_budget_probability"] == 0.0
    run = cur["runway"]
    assert run["status"] == "runs_out" and run["runs_out_probability"] == 1.0
    assert run["runs_out_on"] == {"p10": "2026-10-16", "p50": "2026-10-17", "p90": "2026-10-21"}
    assert run["days_short"] == 14                                   # Oct 31 - Oct 17

    _put_budget(client, "org", "default", 12000)                      # remaining 9e9
    second = _forecast(client)
    cur2 = _series(second)["periods"][0]
    # total row 6.9e9 * (1 + 0.05 i): 9e9 sits between i=6 (8.97e9) and i=7 (9.315e9)
    expected = (30 + 5 * (9_000_000_000 - 8_970_000_000) / (9_315_000_000 - 8_970_000_000)) / 100
    assert cur2["within_budget_probability"] == pytest.approx(expected)
    assert cur2["within_budget_probability"] > cur["within_budget_probability"]
    run2 = cur2["runway"]
    assert run2["status"] == "at_risk"
    assert run2["runs_out_probability"] + cur2["within_budget_probability"] == 1.0
    assert run2["runs_out_on"]["p50"] > run["runs_out_on"]["p50"]   # a later date, or...
    # the budget is read live: nothing about it is stored with the run
    _put_budget(client, "org", "default", 30000)
    third = _series(_forecast(client))["periods"][0]
    assert third["within_budget_probability"] == 1.0
    assert third["runway"]["status"] == "lasts"
    assert third["runway"]["runs_out_on"] == {"p10": None, "p50": None, "p90": None}
    assert third["runway"]["days_short"] is None


@requires_db
def test_runway_is_exhausted_when_the_budget_is_already_spent(client, seeded):
    with session_scope() as s:
        add_session(s, "x1", "csv:dev1", 600_000_000, utc(2026, 10, 2, 5))     # Oct 2 local
        add_session(s, "x2", "csv:dev1", 700_000_000, utc(2026, 10, 4, 5))     # Oct 4 local
        add_session(s, "x3", "csv:dev1", 900_000_000, utc(2026, 10, 6, 5))
    _put_budget(client, "org", "default", 1)                                      # $1: spent on Oct 2
    cur = _series(_forecast(client))["periods"][0]
    assert cur["actual_to_date_usd_micros"] == 2_200_000_000
    assert cur["within_budget_probability"] == 0.0
    assert cur["runway"]["status"] == "exhausted" and cur["runway"]["runs_out_probability"] == 1.0
    assert cur["runway"]["runs_out_on"] == {"p10": "2026-10-02", "p50": "2026-10-02", "p90": "2026-10-02"}
    assert cur["runway"]["days_short"] == 29
    _put_budget(client, "org", "default", 1300)                                   # crossed on Oct 4
    cur = _series(_forecast(client))["periods"][0]
    assert cur["runway"]["runs_out_on"]["p50"] == "2026-10-04"
    assert cur["runway"]["status"] == "exhausted"
    _put_budget(client, "org", "default", 0)                                      # zero: the period start
    cur = _series(_forecast(client))["periods"][0]
    assert cur["runway"]["status"] == "exhausted"
    assert cur["runway"]["runs_out_on"]["p50"] == "2026-10-01" and cur["runway"]["days_short"] == 30


@requires_db
def test_non_finite_budget_is_no_budget_and_negative_is_exhausted(client, seeded):
    with session_scope() as s:
        s.execute(text("INSERT INTO budgets (scope_type, scope_id, period, amount_usd, updated_at) "
                       "VALUES ('org', 'default', 'monthly', 'NaN', now())"))
    f = _forecast(client)["data"]["forecast"]
    assert f["budget_usd_micros"] is None
    assert f["series"][0]["periods"][0]["runway"] is None
    with session_scope() as s:
        s.execute(text("UPDATE budgets SET amount_usd = -5 WHERE scope_id = 'default'"))
        add_session(s, "n1", "csv:dev1", 100_000_000, utc(2026, 10, 3, 5))
    cur = _series(_forecast(client))["periods"][0]
    assert cur["runway"]["status"] == "exhausted"
    assert cur["runway"]["runs_out_on"]["p50"] == "2026-10-01"          # budget <= 0: the period start


# ------------------------------------------------------------------------------ unreadable runs

CORRUPTIONS = {
    "bad_timezone": ("UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', '\"Not/AZone\"')"),
    "long_timezone_name": ("UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', to_jsonb(repeat('a', 300)))"),
    "non_string_timezone": ("UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', '5')"),
    "meta_not_an_object": ("UPDATE forecast_runs SET meta = '[1]'::jsonb"),
    "missing_band": ("UPDATE forecast_runs SET meta = meta - 'band'"),
    "preset_without_description": (
        "UPDATE forecast_runs SET meta = meta #- '{presets,0,description}'"),
    "short_daily_array": (
        "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{daily,expected}', "
        "(payload #> '{daily,expected}') - 0) WHERE scope_id = 'default' AND preset_id = 'baseline'"),
    "non_numeric_total": (
        "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods,0,total,expected}', "
        "'\"x\"') WHERE scope_id = 'default' AND preset_id = 'usage-up-20'"),
    "short_quantile_row": (
        "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods,0,total,quantiles}', "
        "(payload #> '{periods,0,total,quantiles}') - 0) WHERE scope_id = 'default'"),
    "periods_not_an_array": (
        "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods}', '\"oops\"') "
        "WHERE scope_id = 'default' AND preset_id = 'baseline'"),
    "missing_tokens_by_model": (
        "UPDATE forecast_scope_series SET payload = payload #- '{periods,1,tokens_by_model}' "
        "WHERE scope_id = 'default' AND preset_id = 'baseline'"),
    "missing_series_row": (
        "DELETE FROM forecast_scope_series WHERE scope_id = 'default' AND preset_id = 'baseline'"),
    "unknown_status": (
        "UPDATE forecast_scope_series SET status = 'weird' WHERE scope_id = 'default'"),
}


@requires_db
@pytest.mark.parametrize("name", sorted(CORRUPTIONS))
def test_an_unreadable_stored_run_is_null_never_a_500(client, seeded, name):
    with session_scope() as s:
        s.execute(text(CORRUPTIONS[name]))
    with capture_logs() as logs:
        body = _forecast(client)
    assert body["data"] == {"scope_type": "org", "scope_id": "default", "forecast": None}
    assert body["mode"] == {"modes_present": [], "sessions_by_mode": {}}
    events = [e for e in logs if e.get("event") == "forecast.run_unreadable"]
    assert len(events) == 1, logs
    assert events[0]["reason"] and events[0]["run_id"]
    assert events[0]["log_level"] == "warning"


@requires_db
def test_a_damaged_row_of_another_scope_does_not_break_this_one(client, seeded):
    with session_scope() as s:
        s.execute(text("UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods}', "
                       "'\"oops\"') WHERE scope_id = 'qa'"))
    assert _forecast(client)["data"]["forecast"] is not None
    with capture_logs() as logs:
        body = _forecast(client, {"scope_type": "team", "scope_id": "qa"})
    assert body["data"]["forecast"] is None
    assert any(e.get("event") == "forecast.run_unreadable" for e in logs)


@requires_db
@pytest.mark.parametrize("cycle, expected", [
    ({"kind": "month"}, 1_000_000_000),                                  # monthly: the stored amount, unscaled
    ({"kind": "quarter"}, round(1_000_000_000 * 31 / 92)),               # Q4 2026 is 92 days; October 31
    ({"kind": "sprint", "sprint_weeks": 2, "anchor_date": "2026-09-28"}, round(1_000_000_000 * 31 / 14)),
])
def test_the_forecast_budget_is_per_calendar_month_under_every_cycle(client, seeded, cycle, expected):
    """The forecast is by calendar month. The stored budget is per cycle period, so a non-monthly
    cycle's amount is expressed per month by days (TODO(A4): forecast per org cycle)."""
    assert client.put("/v1/budget-cycle", json=cycle, headers=ADMIN).status_code == 200
    _put_budget(client, "org", "default", 1000)
    f = _forecast(client)["data"]["forecast"]
    assert f["budget_usd_micros"] == expected


@requires_db
def test_the_forecast_has_no_budget_until_one_is_entered_for_the_new_cadence(client, seeded):
    _put_budget(client, "org", "default", 1000)                          # stored under "monthly"
    assert client.put("/v1/budget-cycle", json={"kind": "quarter"}, headers=ADMIN).status_code == 200
    assert _forecast(client)["data"]["forecast"]["budget_usd_micros"] is None
