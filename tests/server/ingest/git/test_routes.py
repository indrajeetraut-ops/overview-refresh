"""The webhook route, graduated to real landing (implementation plan A3, git-source.md §5.1,
§11 test bar): signature valid/invalid/absent/mutated, oversize `413`, a duplicate delivery
landing once but answering `202` twice, and a mid-transaction storage failure landing nothing.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession

from server.ingest.git.config import GithubConfig
from server.ingest.git.routes import make_router
from server.ingest.git.verify import compute_signature
from server.storage import session_scope
from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine
from server.storage.queries import QueryCatalog
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401 -- pytest fixture

pytestmark = requires_db

SECRET = "test-webhook-secret-do-not-use-in-prod"


def _payload(**overrides) -> dict:
    body = {"action": "opened", "number": 1184, "installation": {"id": 42},
            "repository": {"id": 777}}
    body.update(overrides)
    return body


def _post(client, body: bytes, delivery_id: str = "d1", event: str = "pull_request",
          signature: str | None = "__compute__"):
    if signature == "__compute__":
        signature = compute_signature(SECRET, body)
    headers = {"X-GitHub-Event": event, "X-GitHub-Delivery": delivery_id,
               "Content-Type": "application/json"}
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    return client.post("/talium/git/v1/webhook/github", content=body, headers=headers)


@pytest.fixture
def app_client(cfg):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(webhook_secret=SECRET, max_body_bytes=1024)
    catalog = QueryCatalog.load("config/queries")
    app = FastAPI()
    app.include_router(make_router(config, catalog))
    with TestClient(app) as client:
        yield client
    dispose_engine()


def _delivery_count(delivery_id: str) -> int:
    with session_scope() as s:
        return s.execute(
            text("SELECT count(*) FROM git_deliveries WHERE delivery_id = :id"),
            {"id": delivery_id},
        ).scalar()


def _pending_git_map_count(delivery_id: str) -> int:
    with session_scope() as s:
        return s.execute(
            text("SELECT count(*) FROM queue_jobs WHERE topic = 'git_map' AND dedup_key = :id "
                 "AND status = 'pending'"),
            {"id": delivery_id},
        ).scalar()


# --------------------------------------------------------------------- signature (§11)

def test_valid_signature_lands_and_rings_git_map(app_client):
    body = json.dumps(_payload()).encode()
    resp = _post(app_client, body, delivery_id="d-valid")
    assert resp.status_code == 202
    assert _delivery_count("d-valid") == 1
    assert _pending_git_map_count("d-valid") == 1


def test_invalid_signature_rejected_and_lands_nothing(app_client):
    body = json.dumps(_payload()).encode()
    resp = _post(app_client, body, delivery_id="d-bad-sig", signature="sha256=deadbeef")
    assert resp.status_code == 401
    assert _delivery_count("d-bad-sig") == 0


def test_absent_signature_rejected(app_client):
    body = json.dumps(_payload()).encode()
    resp = _post(app_client, body, delivery_id="d-no-sig", signature=None)
    assert resp.status_code == 401
    assert _delivery_count("d-no-sig") == 0


def test_body_mutated_after_signing_rejected(app_client):
    body = json.dumps(_payload()).encode()
    signature = compute_signature(SECRET, body)   # signed over the ORIGINAL body
    mutated = json.dumps(_payload(action="closed")).encode()   # sent body differs
    resp = _post(app_client, mutated, delivery_id="d-mutated", signature=signature)
    assert resp.status_code == 401
    assert _delivery_count("d-mutated") == 0


# ------------------------------------------------------------------------ size cap (§4.1)

def test_oversize_body_rejected_before_verify(app_client):
    # Larger than the fixture's max_body_bytes=1024 -- and deliberately signed correctly, to
    # prove the size check runs BEFORE signature verification (§4.1's ordering).
    body = json.dumps(_payload(padding="x" * 2000)).encode()
    resp = _post(app_client, body, delivery_id="d-huge")
    assert resp.status_code == 413
    assert _delivery_count("d-huge") == 0


# ---------------------------------------------------------------------------- ping (§5.4)

def test_ping_verified_but_lands_nothing(app_client):
    resp = _post(app_client, b"{}", delivery_id="d-ping", event="ping")
    assert resp.status_code == 204
    assert _delivery_count("d-ping") == 0


# ------------------------------------------------------------------------- idempotency (§11)

def test_same_delivery_twice_lands_once_answers_202_twice(app_client):
    body = json.dumps(_payload()).encode()
    r1 = _post(app_client, body, delivery_id="d-dup")
    r2 = _post(app_client, body, delivery_id="d-dup")
    assert r1.status_code == 202
    assert r2.status_code == 202
    assert _delivery_count("d-dup") == 1
    # Redelivery must not ring a second git_map job for the same delivery.
    assert _pending_git_map_count("d-dup") == 1


# ---------------------------------------------------------------- storage failure (§10, §11)

def test_storage_failure_mid_transaction_leaves_no_partial_row(app_client):
    """A failure between `land_delivery` and `ring_git_map` -- simulating the connection
    dropping mid-transaction -- must roll back the whole thing: no delivery row without its
    ring, and the route must answer 503, never claim `202` for data it did not durably land."""
    real_execute = OrmSession.execute
    calls = {"n": 0}

    def flaky_execute(self, statement, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:   # right after land_delivery succeeds, before ring_git_map commits
            raise OperationalError("simulated connection loss", {}, BaseException("boom"))
        return real_execute(self, statement, *args, **kwargs)

    body = json.dumps(_payload()).encode()
    with patch.object(OrmSession, "execute", flaky_execute):
        resp = _post(app_client, body, delivery_id="d-storage-down")
    assert resp.status_code == 503
    assert _delivery_count("d-storage-down") == 0
    assert _pending_git_map_count("d-storage-down") == 0
