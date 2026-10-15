from fastapi.testclient import TestClient

from server.api.app import create_app

from .conftest import requires_db


def test_healthz(api_config, storage_config):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_unknown_route_404s(api_config, storage_config):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/nonexistent")
    assert resp.status_code == 404


@requires_db
def test_api_request_log_carries_no_payload_content(api_config, storage_config, engine, catalog):
    """A9: the `api.request` middleware log line carries route/method/status_code/duration_ms
    and (ground rule 5's one exception) path-param ids only -- never a query string or a
    request body, even when one is present and even when it looks sensitive. `app.py`'s own
    `_LOGGED_PATH_PARAMS` comment asserts this in prose; this is the test that pins it down."""
    import structlog

    capture = structlog.testing.LogCapture()
    structlog.configure(processors=[capture])
    try:
        client = TestClient(create_app(api_config, storage_config))
        client.get("/v1/users/u1/overview", params={
            "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z",
            "secret_should_not_leak": "shhh",
        })
        client.post("/v1/sessions/does-not-exist/feedback",
                    json={"beat_ref": "sensitive-beat-ref", "verdict": "top-secret-verdict"})
    finally:
        structlog.reset_defaults()

    entries = [e for e in capture.entries if e.get("event") == "api.request"]
    assert len(entries) == 2

    allowed_keys = {"event", "log_level", "module", "route", "method", "status_code",
                    "duration_ms", "id"}
    for entry in entries:
        assert set(entry) <= allowed_keys, f"unexpected keys in api.request log: {set(entry) - allowed_keys}"
        rendered = " ".join(str(v) for v in entry.values())
        for leaked in ("secret_should_not_leak", "shhh", "sensitive-beat-ref", "top-secret-verdict"):
            assert leaked not in rendered


@requires_db
def test_overview_route_parses_from_to_and_returns_the_envelope(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/overview", params={"from": "2026-01-01T00:00:00Z",
                                                        "to": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["modes_present"] == []
    assert body["sessions_by_mode"] == {}
    assert body["z1"]["sessions"] == 0


@requires_db
def test_overview_route_rejects_a_missing_timeframe_param(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/overview", params={"from": "2026-01-01T00:00:00Z"})
    assert resp.status_code == 422           # FastAPI's own validation, `to` is required


@requires_db
def test_sessions_list_route_rejects_an_unknown_sort(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/sessions", params={
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z", "sort": "bogus"})
    assert resp.status_code == 400
    assert resp.json()["reason"].startswith("unknown sort key")


@requires_db
def test_session_detail_route_404s_for_an_unknown_session(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/sessions/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["reason"] == "no session 'does-not-exist'"


@requires_db
def test_session_detail_route_view_economics_adds_the_d1_block(api_config, storage_config, engine, catalog):
    """D1: `?view=economics` on the same route -- the base response is unaffected without it,
    and with it the `economics` block (model_fit/segments/channels/...) is present."""
    from datetime import datetime, timezone

    from server.storage import session_scope
    from server.storage.schema import Session as SessionRow
    from server.storage.schema import SessionInfo as SessionInfoRow

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        s.add(SessionRow(session_id="eco-route-1", producer_id="p", org_id="org-acme",
                         user_id="u1", agent="claude_code", status="live", started_at=now,
                         last_event_at=now, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfoRow(
            session_id="eco-route-1", created_at=now, updated_at=now,
            models={"claude-opus-5": {"input": 1000, "output": 200}},
            tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[], turn_count=0,
            long_turn_count=0, otel={}, datasource="both",
            # MISC, not COMPREHEND -- model-fit-reasoning-demand-rework plan: COMPREHEND is
            # now a high-demand label (never "over"); MISC is the genuinely mechanical one.
            info={"segments": [{"index": 0, "label": "MISC", "beats": {},
                                "cost_usd_micros": 1000}]},
        ))

    client = TestClient(create_app(api_config, storage_config))
    base = client.get("/v1/sessions/eco-route-1")
    assert base.status_code == 200
    assert "economics" not in base.json()

    resp = client.get("/v1/sessions/eco-route-1", params={"view": "economics"})
    assert resp.status_code == 200
    economics = resp.json()["economics"]
    assert economics["model_fit"]["verdict"] == "over"
    assert economics["segments"][0]["fit"] == "over"


@requires_db
def test_session_detail_route_rejects_an_unknown_view(api_config, storage_config, engine, catalog):
    from datetime import datetime, timezone

    from server.storage import session_scope
    from server.storage.schema import Session as SessionRow
    from server.storage.schema import SessionInfo as SessionInfoRow

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        s.add(SessionRow(session_id="eco-bad-route-1", producer_id="p", org_id="org-acme",
                         user_id="u1", agent="claude_code", status="live", started_at=now,
                         last_event_at=now, signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfoRow(session_id="eco-bad-route-1", created_at=now, updated_at=now,
                             models={}, tools={}, files={}, skills=[], subagents={}, pr_list=[],
                             turns=[], turn_count=0, long_turn_count=0, info={}, otel={},
                             datasource="both"))

    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/sessions/eco-bad-route-1", params={"view": "bogus"})
    assert resp.status_code == 400


@requires_db
def test_sessions_list_route_empty_range(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/sessions", params={
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json() == {"sessions": [], "has_more": False, "limit": 20, "offset": 0}


@requires_db
def test_insights_route_empty_range(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/insights", params={
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["insights"] == []


@requires_db
def test_repos_route_empty_range(api_config, storage_config, engine, catalog):
    """No sessions at all in range -- structurally unavailable (no jsonl-capable session
    means no fold ever ran), the same cold-start rule Z1/Z3's repo fields already apply, not
    an empty list a caller could mistake for "zero repos, confirmed"."""
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/users/u1/repos", params={
        "from": "2026-01-01T00:00:00Z", "to": "2026-01-02T00:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["repos"] == {"unavailable": {"block": "repos", "requires": "jsonl"}}


@requires_db
def test_pricing_route_serves_the_shipped_table(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/pricing/models")
    assert resp.status_code == 200
    assert "models" in resp.json()


@requires_db
def test_feedback_route_persists_and_returns_201(api_config, storage_config, engine, catalog):
    from datetime import datetime, timezone

    from server.storage import session_scope
    from server.storage.schema import Session as SessionRow
    from server.storage.schema import SessionInfo as SessionInfoRow

    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    with session_scope() as s:
        s.add(SessionRow(session_id="fb-1", producer_id="p", org_id="org-acme", user_id="u1",
                         agent="claude_code", status="live", started_at=now, last_event_at=now,
                         signal_blob={}, info_blob={}, subagents=[]))
        s.add(SessionInfoRow(session_id="fb-1", created_at=now, updated_at=now, models={},
                             tools={}, files={}, skills=[], subagents={}, pr_list=[], turns=[],
                             turn_count=0, long_turn_count=0, info={}, otel={}, datasource="both"))

    client = TestClient(create_app(api_config, storage_config))
    resp = client.post("/v1/sessions/fb-1/feedback",
                       json={"beat_ref": "insight:29", "verdict": "dismissed"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["session_id"] == "fb-1"
    assert body["verdict"] == "dismissed"


@requires_db
def test_feedback_route_404s_for_an_unknown_session(api_config, storage_config, engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    resp = client.post("/v1/sessions/does-not-exist/feedback",
                       json={"beat_ref": "insight:29", "verdict": "dismissed"})
    assert resp.status_code == 404


@requires_db
def test_no_webui_bundle_root_404s_and_v1_still_serves(api_config, storage_config,
                                                       engine, catalog):
    """The vanilla SPA at api/static was retired (MVP plan U0). With no built `src/webui`
    bundle on disk the StaticFiles mount is skipped -- `/` is a 404 and `/v1/*` is unaffected."""
    client = TestClient(create_app(api_config, storage_config))
    assert client.get("/").status_code == 404
    api_resp = client.get("/v1/pricing/models")
    assert api_resp.status_code == 200
    assert api_resp.headers["content-type"].startswith("application/json")


@requires_db
def test_webui_bundle_is_served_at_root_without_shadowing_v1(api_config, storage_config,
                                                             engine, catalog, tmp_path):
    """When `vite build` has produced a bundle, it is served at `/` -- mounted last, so
    `/v1/*` still resolves to the API (MVP plan U0)."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    cfg = api_config.model_copy(update={"webui_dist": str(dist)})

    client = TestClient(create_app(cfg, storage_config))
    root = client.get("/")
    assert root.status_code == 200
    assert "id=root" in root.text
    api_resp = client.get("/v1/pricing/models")
    assert api_resp.status_code == 200
    assert api_resp.headers["content-type"].startswith("application/json")


@requires_db
def test_pricing_route_with_catalog_absent_returns_empty_models(api_config, storage_config,
                                                                engine, catalog, monkeypatch):
    from common.model_pricing.errors import CatalogError
    from server.api import app as app_mod

    def _absent(*a, **kw):
        raise CatalogError("no catalog")

    monkeypatch.setattr(app_mod.Pricer, "load", staticmethod(_absent))
    client = TestClient(create_app(api_config, storage_config))
    resp = client.get("/v1/pricing/models")
    assert resp.status_code == 200
    assert resp.json()["models"] == {}


@requires_db
def test_pricing_route_serves_registry_ids_with_two_decimal_rates(api_config, storage_config,
                                                                  engine, catalog):
    client = TestClient(create_app(api_config, storage_config))
    models = client.get("/v1/pricing/models").json()["models"]
    assert "claude-haiku-4-5" in models and "claude-haiku-4-5-20251001" not in models
    assert models["claude-opus-5"]["input"] == "5.00"
