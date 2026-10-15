"""End-to-end OTLP intake through the live FastAPI app (otel-intake design §4.1).

Two things this file is really about:

1. **Run-twice idempotency.** OTel SDKs retry; duplicate delivery is normal, not exceptional
   (§2). Every write path here has to insert zero rows on a replay, or at-least-once delivery
   silently inflates spend.
2. **Both encodings really are both.** The protobuf bodies are built by re-encoding the JSON
   fixtures through `opentelemetry-proto`, so the protobuf path is exercised as protobuf and
   not as JSON wearing a different Content-Type.
"""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from server.ingest.config import OtlpConfig
from server.ingest.otlp.auth import OrgAuth
from server.ingest.otlp.decode import encode_protobuf, gzip_bytes
from server.ingest.otlp.router import OtlpGuards, make_router
from server.storage import session_scope
from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine
from server.storage.org_token_store import PgOrgTokenStore
from server.storage.schema import OtelApiRequest, OtelMetricPoint, OtelSeries
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401 -- pytest fixture

pytestmark = requires_db

FIXTURES = Path("tests/fixtures/otel")
SESSION_ID = "8f87e0a5-872f-4617-9d27-d9d0bc8db54d"

#: The fixture's event zoo, per tests/fixtures/otel/README.md. v1 keeps only api_request.
EXPECTED_DROPS = {
    "tool_decision": 6, "tool_result": 6, "hook_execution_start": 6,
    "hook_execution_complete": 6, "assistant_response": 2, "plugin_loaded": 2,
    "user_prompt": 1,
}


def _batches(name: str) -> list[dict]:
    return [json.loads(line) for line in
            (FIXTURES / name).read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def app_client(cfg):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    api_key, _hash = PgOrgTokenStore().issue("org-acme", {"note": "test fleet"})

    app = FastAPI()
    app.include_router(make_router(OtlpConfig(), auth=OrgAuth(cache_max=0)))
    with TestClient(app) as client:
        client.headers["authorization"] = f"Bearer {api_key}"
        yield client
    dispose_engine()


def _post_json(client, signal: str, batch: dict, **kwargs):
    return client.post(f"/otlp/v1/{signal}", content=json.dumps(batch),
                       headers={"content-type": "application/json"}, **kwargs)


def _post_protobuf(client, signal: str, batch: dict, **kwargs):
    return client.post(f"/otlp/v1/{signal}", content=encode_protobuf(batch, signal),
                       headers={"content-type": "application/x-protobuf"}, **kwargs)


def _replay(client, post, signal: str, name: str) -> list:
    return [post(client, signal, batch) for batch in _batches(name)]


def _counts() -> dict[str, int]:
    with session_scope() as s:
        return {
            "series": s.scalar(select(func.count()).select_from(OtelSeries)),
            "points": s.scalar(select(func.count()).select_from(OtelMetricPoint)),
            "requests": s.scalar(select(func.count()).select_from(OtelApiRequest)),
        }


# --------------------------------------------------------------- golden ingest

def test_json_metrics_replay_lands_the_fixture(app_client):
    responses = _replay(app_client, _post_json, "metrics", "metrics_session.jsonl")
    assert {r.status_code for r in responses} == {200}
    assert all(r.json() == {} for r in responses)      # no partialSuccess: nothing rejected

    counts = _counts()
    assert counts["series"] == 12                       # README: 12 series
    assert counts["points"] == 80                       # README: 80 datapoints

    with session_scope() as s:
        series = s.scalars(select(OtelSeries)).all()
        assert {x.vendor for x in series} == {"claude_code"}
        assert {x.session_id for x in series} == {SESSION_ID}
        assert {x.temporality for x in series} == {"cumulative"}
        assert {x.metric_kind for x in series} == {"sum"}
        # org comes from the token, never the payload (§4.5)
        assert {x.org_id for x in series} == {"org-acme"}
        assert all(x.attrs["organization.id"] == "22222222-2222-4222-8222-222222222222"
                   for x in series)


def test_json_logs_replay_keeps_only_api_requests(app_client):
    responses = _replay(app_client, _post_json, "logs", "logs_session.jsonl")
    assert {r.status_code for r in responses} == {200}
    assert _counts()["requests"] == 8                   # README: 8 api_request records

    with session_scope() as s:
        rows = s.scalars(select(OtelApiRequest)).all()
        assert {r.vendor for r in rows} == {"claude_code"}
        assert {r.session_id for r in rows} == {SESSION_ID}
        assert all(r.request_id.startswith("req_") for r in rows)
        # the background calls the JSONL path cannot see at all (§3)
        assert {r.query_source for r in rows} >= {"prompt_suggestion", "sdk"}
        assert all(r.cost_usd_micros is not None for r in rows)


def test_protobuf_and_json_produce_identical_rows(app_client, cfg):  # noqa: F811
    _replay(app_client, _post_json, "metrics", "metrics_session.jsonl")
    _replay(app_client, _post_json, "logs", "logs_session.jsonl")
    from_json = _counts()
    with session_scope() as s:
        json_series = sorted(x.series_id for x in s.scalars(select(OtelSeries)).all())
        json_requests = sorted(
            (r.request_id, r.input_tokens, r.output_tokens, r.cost_usd_micros)
            for r in s.scalars(select(OtelApiRequest)).all())

    operations.init(cfg, force=True)
    api_key, _hash = PgOrgTokenStore().issue("org-acme")
    app_client.headers["authorization"] = f"Bearer {api_key}"
    _replay(app_client, _post_protobuf, "metrics", "metrics_session.jsonl")
    _replay(app_client, _post_protobuf, "logs", "logs_session.jsonl")

    assert _counts() == from_json
    with session_scope() as s:
        # the series hash must not depend on the wire encoding, or a fleet switching
        # OTEL_EXPORTER_OTLP_PROTOCOL would fork every one of its series
        assert sorted(x.series_id for x in s.scalars(select(OtelSeries)).all()) == json_series
        assert sorted(
            (r.request_id, r.input_tokens, r.output_tokens, r.cost_usd_micros)
            for r in s.scalars(select(OtelApiRequest)).all()) == json_requests


# ------------------------------------------------------------- idempotency

def test_running_the_whole_replay_twice_writes_nothing_the_second_time(app_client):
    _replay(app_client, _post_json, "metrics", "metrics_session.jsonl")
    _replay(app_client, _post_json, "logs", "logs_session.jsonl")
    first = _counts()

    second_metrics = _replay(app_client, _post_json, "metrics", "metrics_session.jsonl")
    second_logs = _replay(app_client, _post_json, "logs", "logs_session.jsonl")
    assert {r.status_code for r in second_metrics + second_logs} == {200}
    assert _counts() == first


def test_a_body_replayed_within_a_batch_is_deduped(app_client):
    batch = _batches("metrics_session.jsonl")[0]
    _post_json(app_client, "metrics", batch)
    after_first = _counts()
    for _ in range(3):
        assert _post_json(app_client, "metrics", batch).status_code == 200
    assert _counts() == after_first


# ------------------------------------------------------------------ filter

def test_drop_counts_match_the_fixture_event_zoo(app_client, caplog):
    """The filter is server-side by necessity: Claude Code has no per-event-type export
    filter, so the drop distribution is only visible here (§4.1 step 6)."""
    import structlog

    capture = structlog.testing.LogCapture()
    structlog.configure(processors=[capture])
    try:
        _replay(app_client, _post_json, "logs", "logs_session.jsonl")
    finally:
        structlog.reset_defaults()

    dropped: dict[str, int] = {}
    kept = 0
    for entry in capture.entries:
        if entry.get("event") != "otlp_batch_accepted":
            continue
        kept += entry["events_kept"]
        for name, count in entry["events_dropped"].items():
            dropped[name] = dropped.get(name, 0) + count
    assert kept == 8
    assert dropped == EXPECTED_DROPS


# ---------------------------------------------------------- unknown vendor

def test_unknown_vendor_stores_metrics_and_drops_events(app_client):
    """§4.1 step 4: unclaimed metrics are stored tagged `unknown` (prunable, harmless);
    unclaimed log events are dropped with an alarm."""
    metrics = {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "some-other-agent"}}]},
        "scopeMetrics": [{"scope": {"name": "org.example.agent"}, "metrics": [{
            "name": "example.tokens",
            "sum": {"aggregationTemporality": 2, "isMonotonic": True, "dataPoints": [{
                "attributes": [{"key": "session.id", "value": {"stringValue": "s1"}}],
                "startTimeUnixNano": "1", "timeUnixNano": "2", "asInt": "7"}]}}]}]}]}
    assert _post_json(app_client, "metrics", metrics).status_code == 200

    logs = {"resourceLogs": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "some-other-agent"}}]},
        "scopeLogs": [{"scope": {"name": "org.example.agent"}, "logRecords": [{
            "timeUnixNano": "2", "attributes": [
                {"key": "event.name", "value": {"stringValue": "api_request"}},
                {"key": "request_id", "value": {"stringValue": "req_x"}}]}]}]}]}
    assert _post_json(app_client, "logs", logs).status_code == 200

    with session_scope() as s:
        series = s.scalars(select(OtelSeries)).all()
        assert [x.vendor for x in series] == ["unknown"]
        # no mapping means no way to read identity out of the payload
        assert series[0].session_id is None and series[0].user_key is None
        assert s.scalar(select(func.count()).select_from(OtelMetricPoint)) == 1
        assert s.scalar(select(func.count()).select_from(OtelApiRequest)) == 0


# ------------------------------------------------------------ partialSuccess

def test_a_datapoint_without_a_timestamp_is_rejected_not_stored(app_client):
    """Half the dedup key. Storing it would inflate totals on the next retry."""
    body = {"resourceMetrics": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "claude-code"}}]},
        "scopeMetrics": [{"scope": {"name": "com.anthropic.claude_code"}, "metrics": [{
            "name": "claude_code.token.usage",
            "sum": {"aggregationTemporality": 2, "dataPoints": [
                {"attributes": [], "asInt": "5"},
                {"attributes": [], "timeUnixNano": "9", "asInt": "5"}]}}]}]}]}
    response = _post_json(app_client, "metrics", body)
    assert response.status_code == 200                 # never a 5xx for bad records (§4.1)
    assert response.json()["partialSuccess"]["rejectedDataPoints"] == 1
    assert "datapoint_without_timestamp" in response.json()["partialSuccess"]["errorMessage"]
    assert _counts()["points"] == 1                    # the good one still landed


def test_a_request_event_without_a_request_id_is_rejected(app_client):
    body = {"resourceLogs": [{
        "resource": {"attributes": [
            {"key": "service.name", "value": {"stringValue": "claude-code"}}]},
        "scopeLogs": [{"scope": {"name": "com.anthropic.claude_code.events"},
                       "logRecords": [{"timeUnixNano": "2", "attributes": [
                           {"key": "event.name", "value": {"stringValue": "api_request"}},
                           {"key": "model", "value": {"stringValue": "m"}}]}]}]}]}
    response = _post_json(app_client, "logs", body)
    assert response.status_code == 200
    assert response.json()["partialSuccess"]["rejectedLogRecords"] == 1
    assert _counts()["requests"] == 0


# ------------------------------------------------------------------- guards

def test_missing_or_bad_token_is_401(app_client):
    batch = json.dumps(_batches("metrics_session.jsonl")[0])
    del app_client.headers["authorization"]          # the no-header case, really absent
    for header in ({}, {"authorization": "Bearer tk_org_never-issued"},
                   {"authorization": "Basic x"}):
        response = app_client.post(
            "/otlp/v1/metrics", content=batch,
            headers={"content-type": "application/json", **header},
        )
        assert response.status_code == 401
        assert response.json() == {"code": "unauthorized"}   # never says which of the three
    assert _counts()["points"] == 0


def test_gzip_is_honored(app_client):
    batch = _batches("metrics_session.jsonl")[0]
    response = app_client.post(
        "/otlp/v1/metrics", content=gzip_bytes(json.dumps(batch).encode()),
        headers={"content-type": "application/json", "content-encoding": "gzip"},
    )
    assert response.status_code == 200
    assert _counts()["points"] > 0


def test_oversized_body_is_413(app_client):
    app = FastAPI()
    app.include_router(make_router(OtlpConfig(max_body_bytes=64), auth=OrgAuth(cache_max=0)))
    with TestClient(app) as client:
        client.headers["authorization"] = app_client.headers["authorization"]
        response = client.post("/otlp/v1/metrics",
                               content=json.dumps(_batches("metrics_session.jsonl")[0]),
                               headers={"content-type": "application/json"})
    assert response.status_code == 413


def test_malformed_body_is_400(app_client):
    for content, content_type in ((b"{not json", "application/json"),
                                  (b"\xff\xfe\x01garbage", "application/x-protobuf"),
                                  (b"[]", "application/json")):
        response = app_client.post("/otlp/v1/metrics", content=content,
                                   headers={"content-type": content_type})
        assert response.status_code == 400, content_type
    # and an encoding we do not serve
    response = app_client.post("/otlp/v1/metrics", content=b"x",
                               headers={"content-type": "text/plain"})
    assert response.status_code == 400


def test_shed_returns_429_with_retry_after(app_client):
    """Beyond `max_inflight` the route sheds *before* decoding. Cumulative metrics are
    self-healing, so a shed interval costs freshness and never totals (§4.1)."""
    config = OtlpConfig(max_inflight=1)
    guards = OtlpGuards(config)
    app = FastAPI()
    app.include_router(make_router(config, auth=OrgAuth(cache_max=0), guards=guards))
    with TestClient(app) as client:
        client.headers["authorization"] = app_client.headers["authorization"]
        # Occupy the slot by hand: driving real concurrency through TestClient would race,
        # and what is under test is the shed decision, not the scheduler.
        guards.inflight.count = guards.inflight.limit
        response = client.post("/otlp/v1/metrics", content=b"{}",
                               headers={"content-type": "application/json"})
    assert response.status_code == 429
    assert response.headers["retry-after"] == str(OtlpConfig().retry_after_s)


def test_per_org_rate_limit_returns_429(app_client):
    app = FastAPI()
    app.include_router(make_router(OtlpConfig(org_rate_per_s=1.0, org_burst=2.0),
                                   auth=OrgAuth(cache_max=0)))
    with TestClient(app) as client:
        client.headers["authorization"] = app_client.headers["authorization"]
        codes = [client.post("/otlp/v1/metrics", content=b"{}",
                             headers={"content-type": "application/json"}).status_code
                 for _ in range(5)]
    assert codes[:2] == [200, 200]          # the burst
    assert 429 in codes[2:]
