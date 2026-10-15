"""Tenant binding end-to-end (git-source.md §6.1, implementation plan A7, test bar): the setup
callback binds a pending org for a not-yet-confirmed installation; `resolve_org_id` picks it up
on the first delivery for that installation and consumes it; an installation nobody ever bound
resolves to the default org; `cli_installations reconcile` seeds/updates installations with no
webhook involved at all."""
from __future__ import annotations

import httpx
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from server.ingest.git.cli_installations import _map, _reconcile
from server.ingest.git.client import GitHubClient
from server.ingest.git.config import GithubConfig
from server.ingest.git.routes import make_router
from server.ingest.git.state_token import issue_state
from server.storage.dbm import operations
from server.storage.engine import dispose_engine, get_engine, session_scope
from server.worker.git_map import DEFAULT_ORG_ID, resolve_org_id
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401

STATE_SECRET = "state-secret-do-not-use-in-prod"


@requires_db
def test_setup_callback_binds_pending_and_resolve_org_id_consumes_it(cfg, catalog):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(state_secret=STATE_SECRET)
    app = FastAPI()
    app.include_router(make_router(config, catalog))
    token = issue_state("org_acme", STATE_SECRET)

    with TestClient(app) as client:
        resp = client.get(config.setup_path, params={"installation_id": "777", "state": token})
    assert resp.status_code == 202

    with session_scope() as s:
        pending = s.execute(text("SELECT org_id FROM git_pending_installs WHERE installation_id = 777")).first()
    assert pending.org_id == "org_acme"

    # The first delivery mapped for this installation picks up the pending binding...
    with session_scope() as s:
        org_id = resolve_org_id(s, catalog, 777)
    assert org_id == "org_acme"

    # ...and consumes it: it is popped, not re-usable, and now lives on git_installations
    # instead (once something actually upserts that row).
    with session_scope() as s:
        remaining = s.execute(text("SELECT count(*) FROM git_pending_installs WHERE installation_id = 777")).scalar()
    assert remaining == 0
    dispose_engine()


@requires_db
def test_setup_callback_rejects_invalid_state(cfg, catalog):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(state_secret=STATE_SECRET)
    app = FastAPI()
    app.include_router(make_router(config, catalog))

    with TestClient(app) as client:
        resp = client.get(config.setup_path, params={"installation_id": "777", "state": "garbage"})
    assert resp.status_code == 400
    with session_scope() as s:
        count = s.execute(text("SELECT count(*) FROM git_pending_installs")).scalar()
    assert count == 0
    dispose_engine()


@requires_db
def test_setup_callback_rejects_missing_params(cfg, catalog):  # noqa: F811
    get_engine(cfg)
    operations.init(cfg, force=True)
    config = GithubConfig(state_secret=STATE_SECRET)
    app = FastAPI()
    app.include_router(make_router(config, catalog))

    with TestClient(app) as client:
        resp = client.get(config.setup_path)
    assert resp.status_code == 400
    dispose_engine()


@requires_db
def test_unmapped_installation_resolves_to_default_org(engine, catalog):
    with session_scope() as s:
        org_id = resolve_org_id(s, catalog, 999999)
    assert org_id == DEFAULT_ORG_ID


@requires_db
def test_reconcile_seeds_installations_with_no_webhook_involved(engine, catalog, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    client = GitHubClient(GithubConfig(client_id="Iv1.abc", private_key_path=str(key_path)))

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.github.com/app/installations/501/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "tok"})
        )
        mock.get("https://api.github.com/app/installations").mock(return_value=httpx.Response(200, json=[
            {"id": 501, "account": {"login": "acme-corp", "type": "Organization"},
             "repository_selection": "all"},
        ]))
        _reconcile(client, catalog)

    with session_scope() as s:
        row = s.execute(text(
            "SELECT account_login, org_id, state FROM git_installations WHERE installation_id = 501"
        )).first()
    assert row.account_login == "acme-corp"
    assert row.state == "active"
    assert row.org_id == DEFAULT_ORG_ID   # no pending binding, no prior mapping -> default


@requires_db
def test_reconcile_preserves_a_pending_binding(engine, catalog, tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    key_path = tmp_path / "key.pem"
    key_path.write_bytes(pem)
    client = GitHubClient(GithubConfig(client_id="Iv1.abc", private_key_path=str(key_path)))

    with session_scope() as s:
        s.execute(catalog.get("git", "bind_pending_install"), {"installation_id": 502, "org_id": "org_beta"})

    with respx.mock(assert_all_called=False) as mock:
        mock.post("https://api.github.com/app/installations/502/access_tokens").mock(
            return_value=httpx.Response(201, json={"token": "tok"})
        )
        mock.get("https://api.github.com/app/installations").mock(return_value=httpx.Response(200, json=[
            {"id": 502, "account": {"login": "beta-inc", "type": "Organization"},
             "repository_selection": "all"},
        ]))
        _reconcile(client, catalog)

    with session_scope() as s:
        row = s.execute(text("SELECT org_id FROM git_installations WHERE installation_id = 502")).first()
    assert row.org_id == "org_beta"


@requires_db
def test_map_verb_updates_an_existing_installation_directly(engine, catalog):
    with session_scope() as s:
        s.execute(catalog.get("git", "upsert_installation"), {
            "installation_id": 503, "org_id": DEFAULT_ORG_ID, "account_login": "gamma-llc",
            "account_type": "Organization", "repo_selection": "all", "state": "active", "info": "{}",
        })
    _map(catalog, 503, "org_gamma")
    with session_scope() as s:
        row = s.execute(text("SELECT org_id FROM git_installations WHERE installation_id = 503")).first()
    assert row.org_id == "org_gamma"


@requires_db
def test_map_verb_on_an_unseen_installation_records_a_pending_binding(engine, catalog):
    _map(catalog, 504, "org_delta")
    with session_scope() as s:
        row = s.execute(text("SELECT org_id FROM git_pending_installs WHERE installation_id = 504")).first()
    assert row.org_id == "org_delta"
    with session_scope() as s:
        org_id = resolve_org_id(s, catalog, 504)
    assert org_id == "org_delta"
