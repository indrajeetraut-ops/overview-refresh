"""HTTP-level: GET/PUT /v1/budget-cycle and GET /v1/budget-cycle/periods (Overview refresh A2).

The cycle routes are lead/admin (`require_manager`, like org-scope budgets); `/periods` is open
to any resolvable viewer (the timeframe picker every screen shows needs it)."""
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from server.api.app import create_app
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db

ADMIN = {"x-talium-viewer": "admin1"}
DEV = {"x-talium-viewer": "dev1"}
CYCLE = "/v1/budget-cycle"
PERIODS = "/v1/budget-cycle/periods"
SPRINT = {"kind": "sprint", "sprint_weeks": 2, "anchor_date": "2026-09-14", "anchor_number": 231}


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="admin1@x.com",
                            roles=(RoleGrant("admin", "org", "default"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform")],
    )


def _client(api_config, storage_config, engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
    return TestClient(create_app(api_config, storage_config))


# ------------------------------------------------------------------------------------- gate

@requires_db
def test_every_route_without_a_viewer_header_is_401(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    assert client.get(CYCLE).status_code == 401
    assert client.put(CYCLE, json={"kind": "month"}).status_code == 401
    assert client.get(PERIODS).status_code == 401


@requires_db
def test_an_unresolvable_viewer_is_401(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    assert client.get(PERIODS, headers={"x-talium-viewer": "ghost"}).status_code == 401
    assert client.get(CYCLE, headers={"x-talium-viewer": "ghost"}).status_code == 401


@requires_db
def test_a_plain_dev_cannot_read_or_write_the_cycle_but_can_read_the_periods(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    assert client.get(CYCLE, headers=DEV).status_code == 403
    assert client.put(CYCLE, json={"kind": "month"}, headers=DEV).status_code == 403
    resp = client.get(PERIODS, headers=DEV)
    assert resp.status_code == 200
    assert resp.json()["period_label"] == "monthly"


# --------------------------------------------------------------------------------- the cycle

@requires_db
def test_get_with_no_saved_cycle_is_the_monthly_default(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    resp = client.get(CYCLE, headers=ADMIN)
    assert resp.status_code == 200
    assert resp.json() == {"kind": "month", "recurrence": "recurring", "sprint_weeks": None,
                           "anchor_date": None, "anchor_number": None, "custom_end": None,
                           "period_label": "monthly", "updated_at": None}


@requires_db
def test_put_a_sprint_cycle_round_trips_and_reports_the_label_change(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    put = client.put(CYCLE, json=SPRINT, headers=ADMIN)
    assert put.status_code == 200, put.text
    body = put.json()
    assert (body["kind"], body["recurrence"], body["period_label"]) == ("sprint", "recurring", "sprint")
    assert (body["sprint_weeks"], body["anchor_date"], body["anchor_number"]) == (2, "2026-09-14", 231)
    assert body["updated_at"] is not None
    assert body["previous_period_label"] == "monthly" and body["label_changed"] is True
    assert "not converted" in body["note"]
    got = client.get(CYCLE, headers=ADMIN).json()
    assert {k: got[k] for k in ("kind", "sprint_weeks", "anchor_date", "anchor_number")} == {
        "kind": "sprint", "sprint_weeks": 2, "anchor_date": "2026-09-14", "anchor_number": 231}


@requires_db
def test_a_same_kind_edit_keeps_the_label_and_says_amounts_now_mean_the_new_period(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json=SPRINT, headers=ADMIN)
    resp = client.put(CYCLE, json={**SPRINT, "sprint_weeks": 3}, headers=ADMIN)
    body = resp.json()
    assert body["sprint_weeks"] == 3
    assert body["label_changed"] is False and body["previous_period_label"] == "sprint"
    assert "now apply per period of the changed cycle" in body["note"]
    unchanged = client.put(CYCLE, json={**SPRINT, "sprint_weeks": 3}, headers=ADMIN).json()
    assert unchanged["label_changed"] is False and unchanged["note"] is None


@requires_db
def test_put_is_a_full_replace_and_drops_fields_the_kind_does_not_use(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json=SPRINT, headers=ADMIN)
    body = client.put(CYCLE, json={"kind": "quarter", "sprint_weeks": 4}, headers=ADMIN).json()
    assert body["kind"] == "quarter" and body["period_label"] == "quarterly"
    assert body["sprint_weeks"] is None and body["anchor_date"] is None


@requires_db
def test_put_rejects_an_invalid_cycle_with_a_400_and_keeps_the_saved_one(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json=SPRINT, headers=ADMIN)
    bad = [
        ({"kind": "month", "recurrence": "one-time"}, "always recurring"),
        ({"kind": "sprint"}, "anchor_date"),
        ({"kind": "sprint", "anchor_date": "2026-09-14", "sprint_weeks": 0}, "sprint_weeks"),
        ({"kind": "sprint", "anchor_date": "2026-09-14", "sprint_weeks": 53}, "sprint_weeks"),
        ({"kind": "sprint", "anchor_date": "2026-09-14", "anchor_number": 100001}, "anchor_number"),
        ({"kind": "sprint", "anchor_date": "1999-12-31"}, "between"),
        ({"kind": "custom", "anchor_date": "2026-10-01"}, "custom_end"),
        ({"kind": "custom", "anchor_date": "2026-10-02", "custom_end": "2026-10-01"}, "before anchor_date"),
        ({"kind": "custom", "anchor_date": "2026-10-01", "custom_end": "2101-01-01"}, "between"),
    ]
    for payload, fragment in bad:
        resp = client.put(CYCLE, json=payload, headers=ADMIN)
        assert resp.status_code == 400, (payload, resp.text)
        assert fragment in resp.json()["reason"]
    assert client.get(CYCLE, headers=ADMIN).json()["kind"] == "sprint"


@requires_db
def test_put_rejects_a_malformed_body_with_a_422(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    for payload in ({"kind": "weekly"}, {"kind": "month", "recurrence": "sometimes"},
                    {"kind": "month", "bogus": 1}, {}, {"kind": "sprint", "anchor_date": "soon"},
                    {"kind": "sprint", "anchor_date": "2026-09-14", "sprint_weeks": "x"}):
        assert client.put(CYCLE, json=payload, headers=ADMIN).status_code == 422, payload


@requires_db
def test_budgets_follow_the_active_cycle_label(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    first = client.put("/v1/budgets/org/default", json={"amount_usd": "100"}, headers=ADMIN).json()
    assert first["period"] == "monthly" and first["amount_usd"] == "100.00"
    client.put(CYCLE, json={"kind": "quarter"}, headers=ADMIN)
    after = client.get("/v1/budgets/org/default", headers=ADMIN).json()
    assert after["period"] == "quarterly" and after["amount_usd"] is None     # "no budget" until entered
    client.put("/v1/budgets/org/default", json={"amount_usd": "300"}, headers=ADMIN)
    client.put(CYCLE, json={"kind": "month"}, headers=ADMIN)
    back = client.get("/v1/budgets/org/default", headers=ADMIN).json()
    assert back["period"] == "monthly" and back["amount_usd"] == "100.00"      # kept, not converted


# ------------------------------------------------------------------------------------ periods

@requires_db
def test_periods_default_is_six_calendar_months_ending_with_the_current_one(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    before = datetime.now(timezone.utc)
    body = client.get(PERIODS, headers=ADMIN).json()
    after = datetime.now(timezone.utc)
    assert body["period_label"] == "monthly"
    periods = body["periods"]
    assert len(periods) == 6
    current = periods[-1]
    assert current["is_current"] is True and all(not p["is_current"] for p in periods[:-1])
    assert set(current) == {"key", "label", "from", "to", "end_exclusive", "is_current",
                            "days_elapsed", "days_total"}
    frm = datetime.fromisoformat(current["from"].replace("Z", "+00:00"))
    to = datetime.fromisoformat(current["to"].replace("Z", "+00:00"))
    end = datetime.fromisoformat(current["end_exclusive"].replace("Z", "+00:00"))
    assert frm.day == 1 and (frm.hour, frm.minute) == (0, 0)
    assert before <= to <= after and to < end                   # the current period's `to` is now
    assert 1 <= current["days_elapsed"] <= current["days_total"]
    for p in periods[:-1]:                                      # finished: to == end, all days elapsed
        assert p["to"] == p["end_exclusive"] and p["days_elapsed"] == p["days_total"]
    assert periods[-2]["end_exclusive"] == current["from"]       # contiguous
    nxt = body["next"]
    assert nxt["from"] == current["end_exclusive"] and nxt["to"] == nxt["end_exclusive"]
    assert nxt["days_elapsed"] == 0 and nxt["is_current"] is False


@requires_db
def test_periods_under_a_sprint_cycle_and_the_history_param(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json=SPRINT, headers=ADMIN)
    body = client.get(PERIODS, params={"history": 3}, headers=ADMIN).json()
    assert body["period_label"] == "sprint"
    assert len(body["periods"]) == 3
    assert body["periods"][0]["key"].startswith("sprint-") and body["periods"][0]["label"].startswith("Sprint ")
    assert all(p["days_total"] == 14 for p in [*body["periods"], body["next"]])
    assert body["periods"][-1]["is_current"] is True            # the anchor is in the past
    assert len(client.get(PERIODS, params={"history": 1}, headers=ADMIN).json()["periods"]) == 1
    assert len(client.get(PERIODS, params={"history": 24}, headers=ADMIN).json()["periods"]) == 24


@requires_db
def test_periods_before_a_future_anchor_are_empty_with_the_anchor_as_next(
        api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json={"kind": "sprint", "anchor_date": "2100-01-01", "anchor_number": 5},
               headers=ADMIN)
    body = client.get(PERIODS, headers=ADMIN).json()
    assert body["periods"] == []
    assert body["next"]["label"] == "Sprint 5" and body["next"]["from"] == "2100-01-01T00:00:00Z"


@requires_db
def test_periods_rejects_a_bad_history(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    for history in (0, 25, -1):
        resp = client.get(PERIODS, params={"history": history}, headers=ADMIN)
        assert resp.status_code == 400, history
    assert client.get(PERIODS, params={"history": "many"}, headers=ADMIN).status_code == 422


@requires_db
def test_periods_of_a_finished_one_time_cycle_have_no_next(api_config, storage_config, engine, catalog):
    client = _client(api_config, storage_config, engine, catalog)
    client.put(CYCLE, json={"kind": "custom", "recurrence": "one-time", "anchor_date": "2025-01-01",
                            "custom_end": "2025-03-31"}, headers=ADMIN)
    body = client.get(PERIODS, headers=ADMIN).json()
    assert len(body["periods"]) == 1 and body["next"] is None
    p = body["periods"][0]
    assert p["is_current"] is False and p["days_elapsed"] == p["days_total"] == 90
    assert p["label"] == "01 Jan 2025 – 31 Mar 2025"
    assert p["from"] == "2025-01-01T00:00:00Z" and p["to"] == p["end_exclusive"] == "2025-04-01T00:00:00Z"
