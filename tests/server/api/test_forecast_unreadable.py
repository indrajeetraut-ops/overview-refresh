"""A stored forecast run is data we do not control (the loader checks shape only): on both routes
a damaged one must answer 200 as "no forecast" (`forecast: null` / the plan `unavailable`), log
`forecast.run_unreadable` once, and never be a 500, a hang or a huge response. Also the run
selection (latest `loaded_at`, then `run_id`; rows of one run only) and that "no forecast" for a
scope that is simply not in the run is NOT logged as damage."""
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from structlog.testing import capture_logs

from server.api import forecast as fc
from server.api.app import create_app
from server.api.manager import aggregate
from server.storage import session_scope

from .conftest import requires_db
from .forecast_fixtures import add_session, load_export, make_export, seed_directory, utc

ADMIN = {"x-talium-viewer": "admin1"}
ORG = {"scope_type": "org", "scope_id": "default"}
UNAVAILABLE = {"block": "forecast", "requires": "forecast"}


@pytest.fixture
def client(api_config, storage_config, engine):
    return TestClient(create_app(api_config, storage_config))


def _seed(catalog, tmp_path, **export_args):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(**export_args), tmp_path, catalog)


def _sql(statement):
    with session_scope() as s:
        s.execute(text(statement))


def _events(logs):
    return [e for e in logs if e.get("event") == "forecast.run_unreadable"]


def _assert_no_forecast(client, expect_log=True):
    """Both routes answer 200 with no forecast; returns the unreadable events logged."""
    with capture_logs() as logs:
        started = time.perf_counter()
        one = client.get("/v1/forecast", params=ORG, headers=ADMIN)
        two = client.get("/v1/forecast/plan", headers=ADMIN)
        elapsed = time.perf_counter() - started
    assert one.status_code == 200 and two.status_code == 200, (one.text[:300], two.text[:300])
    assert one.json()["data"] == {"scope_type": "org", "scope_id": "default", "forecast": None}
    assert one.json()["mode"] == {"modes_present": [], "sessions_by_mode": {}}
    assert two.json()["data"] is None and two.json()["unavailable"] == UNAVAILABLE
    assert len(one.content) < 5_000 and len(two.content) < 5_000       # not a huge response
    assert elapsed < 3                                                   # nor a long fetch
    events = _events(logs)
    if expect_log:
        assert len(events) == 2, logs                                     # one per route
        assert all(e["reason"] and e["run_id"] for e in events)
    else:
        assert events == []
    return events


# ------------------------------------------------------------------------------ extreme dates

@requires_db
@pytest.mark.parametrize("export_args, statement", [
    ({}, "UPDATE forecast_runs SET data_through = '9999-12-31'"),
    ({}, "UPDATE forecast_runs SET data_through = '3000-01-01'"),
    ({}, "UPDATE forecast_runs SET data_through = '2000-01-01'"),
    ({}, "UPDATE forecast_runs SET data_through = '2026-10-09'"),          # not the day before October's from
    ({}, "UPDATE forecast_runs SET data_through = '2026-10-07'"),
    ({}, "UPDATE forecast_runs SET data_through = 'infinity'"),
    ({}, "UPDATE forecast_runs SET data_through = '-infinity'"),
    ({}, "UPDATE forecast_runs SET origin = 'infinity'"),
    ({}, "UPDATE forecast_runs SET origin = '-infinity'"),
    ({}, "UPDATE forecast_runs SET origin = '2026-10-09'"),                # after data_through
    ({"data_through": "2026-10-31"}, "UPDATE forecast_runs SET data_through = '2026-10-30'"),   # month end
    ({"data_through": "2026-10-31"}, "UPDATE forecast_runs SET data_through = '2026-11-01'"),
    ({"data_through": "2026-10-31"}, "UPDATE forecast_runs SET data_through = '9999-12-31'"),
    ({"data_through": "2026-10-31"}, "UPDATE forecast_runs SET data_through = 'infinity'"),
])
def test_inconsistent_or_extreme_dates_are_unreadable_never_a_500(
        client, catalog, tmp_path, export_args, statement):
    _seed(catalog, tmp_path, **export_args)
    _sql(statement)
    _assert_no_forecast(client)


@requires_db
def test_an_intact_run_is_not_reported(client, catalog, tmp_path):
    _seed(catalog, tmp_path)
    with capture_logs() as logs:
        assert client.get("/v1/forecast", params=ORG, headers=ADMIN).json()["data"]["forecast"]
        assert client.get("/v1/forecast/plan", headers=ADMIN).json()["data"]
    assert _events(logs) == []


# ------------------------------------------------------------------------------ strictness end to end

STRICT = {
    "float_total": "UPDATE forecast_scope_series SET payload = jsonb_set(payload, "
                   "'{periods,0,total,expected}', '1.5') WHERE scope_id = 'platform'",
    "bool_total": "UPDATE forecast_scope_series SET payload = jsonb_set(payload, "
                  "'{periods,0,total,expected}', 'true') WHERE scope_id = 'platform'",
    "unordered_grid": "UPDATE forecast_runs SET meta = jsonb_set(meta, '{quantile_grid}', "
                      "'[0,10,5,15,20,25,30,35,40,45,50,55,60,65,70,75,80,85,90,95,100]')",
    "period_from_not_in_dates": "UPDATE forecast_runs SET meta = jsonb_set(meta, '{periods,0,from}', "
                                "'\"2027-05-01\"')",
    "missing_key": "UPDATE forecast_runs SET meta = meta - 'periods'",
    "float_band": "UPDATE forecast_runs SET meta = jsonb_set(meta, '{band,low}', '10.0')",
    # the planner's period-count check: a series row with one period too few / too many
    "plan_one_period_short": "UPDATE forecast_scope_series SET payload = payload #- '{periods,1}' "
                             "WHERE scope_id = 'platform' AND preset_id = 'baseline'",
    "plan_one_period_extra": "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods}', "
                             "(payload -> 'periods') || (payload -> 'periods' -> 0)) "
                             "WHERE scope_id = 'qa' AND preset_id = 'usage-up-20'",
    "plan_no_periods_but_complete": "UPDATE forecast_scope_series SET payload = jsonb_set(payload, "
                                    "'{periods}', '[]') WHERE scope_id = 'platform'",
}


@requires_db
@pytest.mark.parametrize("name", sorted(STRICT))
def test_strictness_through_the_routes(client, catalog, tmp_path, name):
    _seed(catalog, tmp_path)
    _sql(STRICT[name])
    plan = client.get("/v1/forecast/plan", headers=ADMIN)
    assert plan.status_code == 200
    # every one of these damages something the planner reads (it takes meta and each team and
    # repo row's totals and tokens) except a damaged org row, which only the org forecast reads
    assert plan.json()["data"] is None and plan.json()["unavailable"] == UNAVAILABLE, name
    team = client.get("/v1/forecast", params={"scope_type": "team", "scope_id": "platform"},
                      headers=ADMIN)
    assert team.status_code == 200
    if name != "plan_one_period_extra":                                 # qa row only
        assert team.json()["data"]["forecast"] is None, name


# ------------------------------------------------------------------------------ the catch-alls

@requires_db
def test_date_arithmetic_failing_after_the_run_is_read_is_unreadable(client, catalog, tmp_path, monkeypatch):
    _seed(catalog, tmp_path)

    def boom(*_a, **_k):
        raise OverflowError("date value out of range")
    monkeypatch.setattr(aggregate, "local_window", boom)
    events = _assert_no_forecast(client)
    assert all("actuals window" in e["reason"] and "OverflowError" in e["reason"] for e in events)


@requires_db
def test_a_database_error_while_fetching_actuals_still_propagates(client, catalog, tmp_path, monkeypatch):
    _seed(catalog, tmp_path)

    def failing_fetcher(*_a, **_k):
        def fetch(_frm, _to):
            raise OperationalError("SELECT 1", {}, Exception("connection lost"))
        return fetch, []
    monkeypatch.setattr(aggregate, "scope_cost_fetcher", failing_fetcher)
    with pytest.raises(OperationalError):
        client.get("/v1/forecast", params=ORG, headers=ADMIN)
    with pytest.raises(OperationalError):
        client.get("/v1/forecast/plan", headers=ADMIN)


@requires_db
def test_a_failure_while_building_the_view_is_unreadable(client, catalog, tmp_path, monkeypatch):
    """A stored number that passed the shape checks but cannot be used (here: composing raises)
    ends in the same unreadable path, not a 500."""
    _seed(catalog, tmp_path)
    monkeypatch.setattr(fc, "compose_period", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(fc, "quantile_value", lambda *a, **k: 1 / 0)
    with capture_logs() as logs:
        one = client.get("/v1/forecast", params=ORG, headers=ADMIN)
        two = client.get("/v1/forecast/plan", headers=ADMIN)
    assert one.status_code == 200 and one.json()["data"]["forecast"] is None
    assert two.status_code == 200 and two.json()["data"] is None
    assert two.json()["unavailable"] == UNAVAILABLE
    events = _events(logs)
    assert len(events) == 2 and all("ZeroDivisionError" in e["reason"] for e in events)


# ------------------------------------------------------------------------------ not damage

@requires_db
def test_a_scope_that_is_not_in_the_run_is_not_logged_as_damage(client, catalog, tmp_path):
    _seed(catalog, tmp_path)
    with capture_logs() as logs:
        body = client.get("/v1/forecast", params={"scope_type": "org", "scope_id": "nope"},
                          headers=ADMIN).json()
    assert body["data"]["forecast"] is None
    assert _events(logs) == []


@requires_db
def test_no_run_at_all_is_not_logged_as_damage(client, engine, catalog):
    with session_scope() as s:
        seed_directory(s, catalog)
    with capture_logs() as logs:
        assert client.get("/v1/forecast", params=ORG, headers=ADMIN).json()["data"]["forecast"] is None
        assert client.get("/v1/forecast/plan", headers=ADMIN).json()["unavailable"] == UNAVAILABLE
    assert _events(logs) == []


# ------------------------------------------------------------------------------ which run

def _copy_run(new_id, *, timezone=None, shift="interval '1 hour'", payload=None):
    """Copy the loaded run (and its series rows) under another id, loaded `shift` later."""
    meta = "meta" if timezone is None else f"jsonb_set(meta, '{{timezone}}', to_jsonb('{timezone}'::text))"
    _sql(f"INSERT INTO forecast_runs SELECT '{new_id}', origin, data_through, generated_at, {meta}, "
         f"loaded_at + {shift} FROM forecast_runs WHERE run_id NOT IN "
         f"(SELECT run_id FROM forecast_runs WHERE run_id LIKE 'x-%')")
    body = "payload" if payload is None else payload
    _sql(f"INSERT INTO forecast_scope_series SELECT '{new_id}', scope_type, scope_id, preset_id, "
         f"status, {body} FROM forecast_scope_series WHERE run_id NOT LIKE 'x-%'")


@requires_db
def test_latest_loaded_at_wins_and_run_id_breaks_the_tie(client, catalog, tmp_path):
    _seed(catalog, tmp_path)
    original = client.get("/v1/forecast", params=ORG, headers=ADMIN).json()["data"]["forecast"]
    assert original["timezone"] == "Asia/Kolkata"
    # two newer runs with the very same loaded_at: the greater run_id is served
    _copy_run("x-aaa-run", timezone="UTC")
    _copy_run("x-zzz-run", timezone="America/New_York")
    with session_scope() as s:
        loaded = s.execute(text("SELECT count(DISTINCT loaded_at) FROM forecast_runs "
                                "WHERE run_id LIKE 'x-%'")).scalar()
    assert loaded == 1
    f = client.get("/v1/forecast", params=ORG, headers=ADMIN).json()["data"]["forecast"]
    assert f["timezone"] == "America/New_York"
    # and a still newer one beats both whatever its id
    _copy_run("x-000-run", timezone="Europe/Paris", shift="interval '2 hours'")
    f = client.get("/v1/forecast", params=ORG, headers=ADMIN).json()["data"]["forecast"]
    assert f["timezone"] == "Europe/Paris"


@requires_db
def test_only_the_rows_of_the_latest_run_are_read(client, catalog, tmp_path):
    """An older run's rows for the same scopes (here: unusable payloads, inserted AFTER the
    real ones) must never leak into either route."""
    _seed(catalog, tmp_path)
    before_forecast = client.get("/v1/forecast", params=ORG, headers=ADMIN).json()
    before_plan = client.get("/v1/forecast/plan", headers=ADMIN).json()
    _copy_run("x-old-run", shift="interval '-1 hour'", payload="'{}'::jsonb")
    with session_scope() as s:
        assert s.execute(text("SELECT count(*) FROM forecast_scope_series")).scalar() == 24
    with capture_logs() as logs:
        assert client.get("/v1/forecast", params=ORG, headers=ADMIN).json() == before_forecast
        assert client.get("/v1/forecast/plan", headers=ADMIN).json() == before_plan
    assert _events(logs) == []
    assert len(before_forecast["data"]["forecast"]["series"]) == 3


@requires_db
def test_a_far_data_through_with_sessions_is_still_bounded(client, catalog, tmp_path):
    """The extreme-date guard holds with real sessions in the database."""
    _seed(catalog, tmp_path)
    with session_scope() as s:
        add_session(s, "s1", "csv:dev1", 5, utc(2026, 10, 2, 5))
    _sql("UPDATE forecast_runs SET data_through = '3000-01-01'")
    _assert_no_forecast(client)
