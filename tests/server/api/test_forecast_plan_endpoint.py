"""`GET /v1/forecast/plan` (T6, plan §3.8): the planner. Same seeded run as the forecast tests
(`forecast_fixtures.py`). Hand numbers: team `platform` has `base` $100/day, so its October
forecast part (23 days) has total row `2.3e9 * (1 + 0.05 i)` and November (30 days)
`3e9 * (1 + 0.05 i)`; `qa` has $40/day and the repo $50/day."""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from structlog.testing import capture_logs

from server.api.app import create_app
from server.api.config import ApiConfig, ForecastConfig
from server.storage import session_scope

from .conftest import requires_db
from .forecast_fixtures import DEFAULT_SCOPES, add_session, load_export, make_export, seed_directory, utc

ADMIN = {"x-talium-viewer": "admin1"}
URL = "/v1/forecast/plan"
REPO = "github:acme/auth-service"
SCOPES = [
    ("org", "default", "acme.dev", 300_000_000),
    ("team", "platform", "Platform", 100_000_000),
    ("team", "qa", "Quality Assurance", 40_000_000),          # the directory says "QA"
    ("repo", REPO, "acme/auth-service", 50_000_000),
]


def _client(api_config, storage_config):
    return TestClient(create_app(api_config, storage_config))


@pytest.fixture
def seeded(engine, catalog, tmp_path):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(scopes=SCOPES), tmp_path, catalog)


def _plan(client, headers=ADMIN):
    resp = client.get(URL, headers=headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _row(body, scope_id):
    return next(r for r in body["data"]["rows"] if r["scope_id"] == scope_id)


def _preset(row, preset_id="baseline"):
    return next(p for p in row["by_preset"] if p["preset_id"] == preset_id)["periods"]


def _put_budget(client, scope_type, scope_id, amount):
    resp = client.put(f"/v1/budgets/{scope_type}/{scope_id}", json={"amount_usd": str(amount)},
                      headers=ADMIN)
    assert resp.status_code == 200, resp.text


@requires_db
def test_rows_are_exactly_the_runs_teams_then_repos_with_export_names(api_config, storage_config, seeded):
    body = _plan(_client(api_config, storage_config))
    assert body["unavailable"] is None
    data = body["data"]
    assert [(r["scope_type"], r["scope_id"], r["name"]) for r in data["rows"]] == [
        ("team", "platform", "Platform"), ("team", "qa", "Quality Assurance"),
        ("repo", REPO, "acme/auth-service")]                      # no org row; the export's names
    assert data["periods"] == [
        {"start": "2026-10-01", "end": "2026-10-31", "forecast_from": "2026-10-09", "current": True},
        {"start": "2026-11-01", "end": "2026-11-30", "forecast_from": "2026-11-01", "current": False}]
    assert [p["id"] for p in data["presets"]] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]
    for row in data["rows"]:
        assert [p["preset_id"] for p in row["by_preset"]] == ["baseline", "usage-up-20", "opus-to-sonnet-30"]
        assert all(len(p["periods"]) == 2 for p in row["by_preset"])


@requires_db
def test_needed_and_gap_at_the_configured_confidence(api_config, storage_config, seeded):
    with session_scope() as s:
        add_session(s, "a0", "csv:dev1", 60_000_000, utc(2026, 9, 30, 20, 0))      # Oct 1 01:30 local: first day
        add_session(s, "a1", "csv:dev1", 500_000_000, utc(2026, 10, 3, 5))        # platform
        add_session(s, "a1n", "csv:lead1", None, utc(2026, 10, 4, 5))              # NULL cost counts 0
        add_session(s, "a1z", "csv:dev1", 40_000_000, utc(2026, 10, 8, 18, 25))    # data_through, 23:55 local
        add_session(s, "a1x", "csv:dev1", 99_000_000, utc(2026, 10, 8, 18, 35))    # Oct 9 local: not counted
        add_session(s, "a2", "csv:dev2", 90_000_000, utc(2026, 10, 3, 6))          # qa
    a = 600_000_000                                                                   # platform actual to date
    client = _client(api_config, storage_config)
    _put_budget(client, "team", "platform", 4000)                                # 4e9
    row = _row(_plan(client), "platform")
    cur, nov = _preset(row)
    # October: actual + forecast total row 2.3e9 * (1 + 0.05 i); the median is the i = 10 row
    # (3.45e9), the mean is 10% above it (3.795e9): expected and p50 are separate figures
    assert cur["needed"] == {"expected_usd_micros": a + 3_795_000_000,
                             "p50_usd_micros": a + 3_450_000_000,                      # i = 10
                             "p80_usd_micros": a + 4_140_000_000,                      # i = 16
                             "p90_usd_micros": a + 4_370_000_000}                      # i = 18
    assert cur["needed"]["expected_usd_micros"] != cur["needed"]["p50_usd_micros"]
    assert cur["gap_usd_micros"] == a + 4_140_000_000 - 4_000_000_000 == 740_000_000
    # plan_for is `needed` at the planning confidence (0.8 = p80 here), actual included
    assert cur["plan_for_usd_micros"] == a + 4_140_000_000 == cur["needed"]["p80_usd_micros"]
    assert cur["plan_for_usd_micros"] - 4_000_000_000 == cur["gap_usd_micros"]
    assert nov["plan_for_usd_micros"] == 5_400_000_000 == nov["needed"]["p80_usd_micros"]
    assert _plan(client)["data"]["planning_confidence"] == 0.8
    # November: nothing spent; row 3e9 * (1 + 0.05 i), mean 4.95e9
    assert nov["needed"] == {"expected_usd_micros": 4_950_000_000, "p50_usd_micros": 4_500_000_000,
                             "p80_usd_micros": 5_400_000_000, "p90_usd_micros": 5_700_000_000}
    assert nov["gap_usd_micros"] == 1_400_000_000

    for confidence, october_gap, november_gap in (
            (0.9, a + 4_370_000_000 - 4_000_000_000, 1_700_000_000),
            (0.95, a + 4_485_000_000 - 4_000_000_000, 1_850_000_000),            # i = 19
            (0.5, a + 3_450_000_000 - 4_000_000_000, 500_000_000),
            (0.825, a + 4_197_500_000 - 4_000_000_000, 1_475_000_000)):          # between i = 16 and 17
        cfg = ApiConfig(webui_dist="/nonexistent/webui/dist",
                        forecast=ForecastConfig(planning_confidence=confidence))
        row = _row(_plan(_client(cfg, storage_config)), "platform")
        cur, nov = _preset(row)
        assert cur["gap_usd_micros"] == october_gap, confidence
        assert nov["gap_usd_micros"] == november_gap, confidence
        assert cur["plan_for_usd_micros"] == october_gap + 4_000_000_000, confidence
        assert nov["plan_for_usd_micros"] == november_gap + 4_000_000_000, confidence
        assert _plan(_client(cfg, storage_config))["data"]["planning_confidence"] == confidence
        assert cur["needed"]["p80_usd_micros"] == a + 4_140_000_000      # needed does not move


@requires_db
def test_gap_is_null_without_a_budget_and_a_budget_edit_moves_it(api_config, storage_config, seeded):
    client = _client(api_config, storage_config)
    body = _plan(client)
    for scope_id in ("platform", "qa", REPO):
        row = _row(body, scope_id)
        assert row["budget_usd_micros"] is None
        for preset in row["by_preset"]:
            assert all(p["gap_usd_micros"] is None for p in preset["periods"])
            # the plan-for figure does not need a budget
            assert all(isinstance(p["plan_for_usd_micros"], int) and p["plan_for_usd_micros"] > 0
                       for p in preset["periods"])
    _put_budget(client, "team", "qa", 1000)                       # 1e9; qa row 40e6 * 30 = 1.2e9 * (1 + .05 i)
    qa = _row(_plan(client), "qa")
    assert qa["budget_usd_micros"] == 1_000_000_000
    nov = _preset(qa)[1]
    assert nov["gap_usd_micros"] == 2_160_000_000 - 1_000_000_000          # i = 16: 1.2e9 * 1.8
    assert nov["plan_for_usd_micros"] == 2_160_000_000                      # budget does not move it
    _put_budget(client, "team", "qa", 5000)
    assert _preset(_row(_plan(client), "qa"))[1]["gap_usd_micros"] == 2_160_000_000 - 5_000_000_000


@requires_db
def test_presets_change_the_figures(api_config, storage_config, seeded):
    row = _row(_plan(_client(api_config, storage_config)), "platform")
    base = _preset(row)[1]["needed"]["expected_usd_micros"]
    assert _preset(row, "usage-up-20")[1]["needed"]["expected_usd_micros"] == base * 12 // 10
    assert _preset(row, "opus-to-sonnet-30")[1]["needed"]["expected_usd_micros"] == base * 9 // 10


@requires_db
def test_tokens_are_passed_through_from_the_export(api_config, storage_config, seeded):
    row = _row(_plan(_client(api_config, storage_config)), "platform")
    cur, nov = _preset(row)
    # builder: model-a expected 1000 * days * factor / 100, p90 1500 * days; model-b 10 / 20 per day
    assert cur["tokens_by_model"] == [{"model": "model-a", "expected": 23_000, "p90": 34_500},
                                      {"model": "model-b", "expected": 230, "p90": 460}]
    assert nov["tokens_by_model"] == [{"model": "model-a", "expected": 30_000, "p90": 45_000},
                                      {"model": "model-b", "expected": 300, "p90": 600}]
    assert _preset(row, "usage-up-20")[0]["tokens_by_model"][0]["expected"] == 27_600


@requires_db
def test_the_planner_reads_only_total_and_tokens(api_config, storage_config, seeded):
    """The cumulative arrays never leave the database for the planner: damage them and the plan
    is unchanged, while the scope's own forecast (which needs them) reads as no forecast."""
    client = _client(api_config, storage_config)
    before = _plan(client)
    with session_scope() as s:
        s.execute(text("UPDATE forecast_scope_series SET payload = jsonb_set(jsonb_set(payload, "
                       "'{periods,0,cumulative}', '\"garbage\"'), '{daily}', '\"garbage\"') "
                       "WHERE scope_id = 'platform'"))
    assert _plan(client) == before
    resp = client.get("/v1/forecast", params={"scope_type": "team", "scope_id": "platform"},
                      headers=ADMIN)
    assert resp.status_code == 200 and resp.json()["data"]["forecast"] is None


@requires_db
def test_current_period_uses_the_scopes_own_session_set(api_config, storage_config, seeded):
    with session_scope() as s:
        add_session(s, "r1", "csv:dev1", 100_000_000, utc(2026, 10, 2, 5), repo_id=REPO)
        add_session(s, "r2", "csv:dev2", 200_000_000, utc(2026, 10, 2, 6), repo_id=REPO)
        add_session(s, "r3", "csv:dev1", 400_000_000, utc(2026, 10, 2, 7), repo_id="github:acme/other")
        add_session(s, "r4", "csv:dev1", 800_000_000, utc(2026, 9, 28, 7), repo_id=REPO)     # September
        add_session(s, "r5", "csv:dev2", 5_000_000, utc(2026, 9, 30, 19, 0), repo_id=REPO)   # Oct 1 00:30 local: first day
        add_session(s, "r6", "csv:dev2", 6_000_000, utc(2026, 10, 8, 18, 0), repo_id=REPO)   # data_through, 23:30 local
        add_session(s, "r7", "csv:dev2", None, utc(2026, 10, 8, 6, 0), repo_id=REPO)         # NULL cost
    body = _plan(_client(api_config, storage_config))
    # needed.expected = actual to date + the median-row total of the forecast part
    # (mean = 110% of the median row: 3.45e9 -> 3.795e9, qa 1.38e9 -> 1.518e9, repo 1.725e9 -> 1.8975e9)
    assert _preset(_row(body, "platform"))[0]["needed"]["expected_usd_micros"] == 500_000_000 + 3_795_000_000
    assert _preset(_row(body, "qa"))[0]["needed"]["expected_usd_micros"] == 211_000_000 + 1_518_000_000
    assert _preset(_row(body, "qa"))[0]["needed"]["p50_usd_micros"] == 211_000_000 + 1_380_000_000
    # the repo: r1 + r2 + r5 + r6 (+ r7, NULL cost) in October; r3 is another repo, r4 is September
    assert _preset(_row(body, REPO))[0]["needed"]["expected_usd_micros"] == 311_000_000 + 1_897_500_000
    assert _preset(_row(body, REPO))[0]["needed"]["p50_usd_micros"] == 311_000_000 + 1_725_000_000
    # November is a future period: no actuals
    assert _preset(_row(body, "platform"))[1]["needed"]["expected_usd_micros"] == 4_950_000_000
    assert body["mode"] == {"modes_present": ["both"], "sessions_by_mode": {"both": 6}}  # r1, r2, r3, r5, r6, r7 (r4 is September)


@requires_db
def test_month_end_data_has_no_current_period(api_config, storage_config, engine, catalog, tmp_path):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(data_through="2026-10-31", scopes=SCOPES), tmp_path, catalog)
    with session_scope() as s:
        add_session(s, "me", "csv:dev1", 77_000_000, utc(2026, 10, 20, 5))
    body = _plan(_client(api_config, storage_config))
    assert [p["current"] for p in body["data"]["periods"]] == [False, False]
    assert [p["start"] for p in body["data"]["periods"]] == ["2026-11-01", "2026-12-01"]
    nov = _preset(_row(body, "platform"))[0]
    assert nov["needed"]["expected_usd_micros"] == 4_950_000_000          # no actual in a future period
    assert nov["needed"]["p50_usd_micros"] == 4_500_000_000


@requires_db
def test_incomplete_scope_has_no_period_figures(api_config, storage_config, engine, catalog, tmp_path):
    with session_scope() as s:
        seed_directory(s, catalog)
    load_export(make_export(scopes=SCOPES, statuses={"qa": "incomplete"}), tmp_path, catalog)
    body = _plan(_client(api_config, storage_config))
    assert all(p["periods"] == [] for p in _row(body, "qa")["by_preset"])
    assert len(_preset(_row(body, "platform"))) == 2


# ------------------------------------------------------------------------------ gate + null

@requires_db
def test_gate_matches_the_teams_table(api_config, storage_config, seeded):
    client = _client(api_config, storage_config)
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers={"x-talium-viewer": "ghost"}).status_code == 401
    assert client.get(URL, headers={"x-talium-viewer": "dev1"}).status_code == 403
    assert client.get(URL, headers=ADMIN).status_code == 200
    # a team lead is a manager, as for /v1/teams: the plan is org-wide for any lead or admin
    assert client.get(URL, headers={"x-talium-viewer": "lead1"}).status_code == 200
    assert client.get("/v1/teams", params={"period": "30d"},
                      headers={"x-talium-viewer": "lead1"}).status_code == 200


@requires_db
def test_no_run_is_null_data_with_unavailable(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        seed_directory(s, catalog)
    body = _plan(_client(api_config, storage_config))
    assert body["data"] is None
    assert body["unavailable"] == {"block": "forecast", "requires": "forecast"}
    assert body["mode"] == {"modes_present": [], "sessions_by_mode": {}}


@requires_db
@pytest.mark.parametrize("sql", [
    "UPDATE forecast_runs SET meta = jsonb_set(meta, '{timezone}', '\"Not/AZone\"')",
    "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods}', '\"oops\"') WHERE scope_id = 'qa'",
    "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods,0,total,expected}', '\"x\"') WHERE scope_id = 'platform'",
    "UPDATE forecast_scope_series SET payload = payload #- '{periods,0,tokens_by_model}' WHERE scope_id = 'platform'",
    "UPDATE forecast_scope_series SET payload = jsonb_set(payload, '{periods,0,total,quantiles}', "
    "(payload #> '{periods,0,total,quantiles}') - 0) WHERE scope_id = 'platform'",
    "DELETE FROM forecast_scope_series WHERE scope_id = 'platform' AND preset_id = 'usage-up-20'",
])
def test_an_unreadable_run_is_unavailable_never_a_500(api_config, storage_config, seeded, sql):
    with session_scope() as s:
        s.execute(text(sql))
    client = _client(api_config, storage_config)
    with capture_logs() as logs:
        body = _plan(client)
    assert body["data"] is None
    assert body["unavailable"] == {"block": "forecast", "requires": "forecast"}
    assert [e for e in logs if e.get("event") == "forecast.run_unreadable"]


@requires_db
def test_plan_budgets_are_per_calendar_month_under_a_non_monthly_cycle(api_config, storage_config, seeded):
    client = _client(api_config, storage_config)
    assert client.put("/v1/budget-cycle", json={"kind": "quarter"}, headers=ADMIN).status_code == 200
    _put_budget(client, "team", "qa", 1000)
    qa = _row(_plan(client), "qa")
    assert qa["budget_usd_micros"] == round(1_000_000_000 * 31 / 92)    # October of a 92-day Q4
    nov = _preset(qa)[1]
    assert nov["gap_usd_micros"] == 2_160_000_000 - qa["budget_usd_micros"]
    assert client.put("/v1/budget-cycle", json={"kind": "month"}, headers=ADMIN).status_code == 200
    assert _row(_plan(client), "qa")["budget_usd_micros"] is None        # monthly amount not entered
