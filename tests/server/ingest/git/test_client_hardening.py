"""A2 client hardening (git-source.md §7.4, §8.3, implementation plan A2): the rate-limit
throttle (secondary points/minute budget, measured-not-estimated hourly budget), ETag
conditional REST requests, the 401-does-not-loop contract, and the `.graphql` file loader.
Uses `respx` to record GitHub exchanges -- no live account needed."""
from __future__ import annotations

import time

import httpx
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from server.ingest.git.client import (
    GitHubClient,
    GitHubInstallationUnauthorized,
    _InstallationThrottle,
    load_graphql,
)
from server.ingest.git.config import GithubConfig

TOKEN_URL = "https://api.github.com/app/installations/42/access_tokens"
PULLS_URL = "https://api.github.com/repos/o/n/pulls"
GRAPHQL_URL = "https://api.github.com/graphql"


@pytest.fixture
def private_key_path(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    path = tmp_path / "key.pem"
    path.write_bytes(pem)
    return str(path)


@pytest.fixture
def client(private_key_path):
    config = GithubConfig(client_id="Iv1.abc123", private_key_path=private_key_path)
    return GitHubClient(config)


# ------------------------------------------------------------------ token lifecycle (§7.3)

def test_installation_token_remints_exactly_once_across_expiry(client, monkeypatch):
    clock = {"t": 1_000.0}
    monkeypatch.setattr("server.ingest.git.client.time.time", lambda: clock["t"])

    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(TOKEN_URL).mock(
            side_effect=[
                httpx.Response(201, json={"token": "tok1"}),
                httpx.Response(201, json={"token": "tok2"}),
            ]
        )
        assert client.installation_token(42) == "tok1"
        assert client.installation_token(42) == "tok1"   # still cached
        assert route.call_count == 1

        clock["t"] += 3600   # past the 1 h expiry (minus the 60 s early margin)
        assert client.installation_token(42) == "tok2"   # exactly one re-mint
        assert route.call_count == 2


def test_installation_token_401_raises_unauthorized_and_never_retries(client):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.post(TOKEN_URL).mock(
            return_value=httpx.Response(401, json={"message": "Bad credentials"})
        )
        with pytest.raises(GitHubInstallationUnauthorized):
            client.installation_token(42)
        assert route.call_count == 1   # a revoked install must not loop (git-source.md §10)


def test_rest_get_401_raises_unauthorized_without_retry_loop(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        route = mock.get(PULLS_URL).mock(return_value=httpx.Response(401, json={"message": "Bad credentials"}))
        with pytest.raises(GitHubInstallationUnauthorized):
            client.rest_get(42, "/repos/o/n/pulls")
        assert route.call_count == 1


# --------------------------------------------------------------- secondary rate limit (§8.3)

def test_rest_get_backs_off_on_secondary_rate_limit_then_succeeds(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        route = mock.get(PULLS_URL).mock(
            side_effect=[
                httpx.Response(403, headers={"Retry-After": "0.01"}, json={"message": "secondary rate limit"}),
                httpx.Response(200, json=[]),
            ]
        )
        resp = client.rest_get(42, "/repos/o/n/pulls")
        assert resp.status_code == 200
        assert route.call_count == 2   # backed off, not failed


def test_rest_get_429_without_retry_after_uses_generic_backoff(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        route = mock.get(PULLS_URL).mock(
            side_effect=[httpx.Response(429, json={}), httpx.Response(200, json=[])]
        )
        resp = client.rest_get(42, "/repos/o/n/pulls")
        assert resp.status_code == 200
        assert route.call_count == 2


# ---------------------------------------------------------------------------- ETag (§8.2)

def test_rest_get_replays_etag_as_if_none_match(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        route = mock.get(PULLS_URL).mock(
            side_effect=[
                httpx.Response(200, json=[{"number": 1}], headers={"ETag": '"abc123"'}),
                httpx.Response(304, headers={"ETag": '"abc123"'}),
            ]
        )
        r1 = client.rest_get(42, "/repos/o/n/pulls", params={"state": "all"})
        assert r1.status_code == 200
        r2 = client.rest_get(42, "/repos/o/n/pulls", params={"state": "all"})
        assert r2.status_code == 304
        assert route.calls[1].request.headers.get("if-none-match") == '"abc123"'
        assert "if-none-match" not in {k.lower() for k in route.calls[0].request.headers.keys()}


def test_rest_get_records_hourly_budget_from_headers(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        mock.get(PULLS_URL).mock(
            return_value=httpx.Response(
                200, json=[], headers={"X-RateLimit-Remaining": "4321", "X-RateLimit-Reset": "9999999999"}
            )
        )
        client.rest_get(42, "/repos/o/n/pulls")
        snap = client.budget_snapshot(42)
        assert snap["hourly_remaining"] == 4321
        assert snap["hourly_reset_at"] == 9999999999.0


# ------------------------------------------------------------- GraphQL measured cost (§7.4)

def test_graphql_feeds_measured_cost_into_throttle(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        mock.post(GRAPHQL_URL).mock(
            return_value=httpx.Response(200, json={
                "data": {"rateLimit": {"cost": 3, "remaining": 4997, "nodeCount": 10,
                                        "resetAt": "2099-01-01T00:00:00Z"}}
            })
        )
        data = client.graphql(42, "query { rateLimit { cost remaining nodeCount resetAt } }")
        assert data["rateLimit"]["cost"] == 3
        snap = client.budget_snapshot(42)
        assert snap["hourly_remaining"] == 4997
        # 1-point placeholder corrected to the measured cost of 3 -> net spend of 3
        assert snap["secondary_points_available"] == pytest.approx(900.0 - 3, abs=1.0)


def test_graphql_errors_array_is_never_treated_as_success(client):
    with respx.mock(assert_all_called=False) as mock:
        mock.post(TOKEN_URL).mock(return_value=httpx.Response(201, json={"token": "tok"}))
        mock.post(GRAPHQL_URL).mock(
            return_value=httpx.Response(200, json={"data": None, "errors": [{"message": "timeout"}]})
        )
        with pytest.raises(Exception):
            client.graphql(42, "query { rateLimit { cost } }")


# --------------------------------------------------------------------- .graphql files (§7.4)

def test_load_graphql_loads_the_probe_query():
    text = load_graphql("probe")
    assert "rateLimit" in text
    assert "pullRequests" in text


def test_load_graphql_rejects_a_document_missing_rate_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("server.ingest.git.client._GRAPHQL_DIR", tmp_path)
    (tmp_path / "bad.graphql").write_text("query { viewer { login } }")
    with pytest.raises(ValueError, match="rateLimit"):
        load_graphql("bad")


# --------------------------------------------------------------- throttle unit behavior

def test_throttle_spend_blocks_until_the_bucket_refills():
    throttle = _InstallationThrottle(installation_id=1)
    throttle._points = 0.5
    start = time.monotonic()
    throttle.spend(1.0)   # needs 0.5 more point at 15 pts/s => ~33ms
    assert time.monotonic() - start >= 0.02
    # time.sleep only guarantees "at least" its argument, so a little refill overshoot -- from
    # real scheduling jitter, not a logic bug -- is expected here.
    assert 0.0 <= throttle._points < 0.5


def test_throttle_adjust_corrects_placeholder_to_measured_cost():
    throttle = _InstallationThrottle(installation_id=1)
    before = throttle._points
    throttle.adjust(1.0 - 3.0)   # placeholder charged 1; real cost measured at 3
    assert throttle._points == pytest.approx(before - 2.0, abs=0.01)


def test_record_hourly_waits_for_reset_when_exhausted(monkeypatch):
    throttle = _InstallationThrottle(installation_id=1)
    monkeypatch.setattr("server.ingest.git.client.time.time", lambda: 1_000.0)
    slept = []
    monkeypatch.setattr("server.ingest.git.client.time.sleep", lambda s: slept.append(s))
    throttle.record_hourly(remaining=0, reset_at=1_000.5)
    assert slept == [0.5]
