"""Org-token auth for the OTLP routes (otel-intake design §4.5, D-OT4).

Three properties matter here and each has its own test: the plaintext key never reaches the
database, a revoked key stops working, and the verify cache is bounded and expires.
"""
import time

import pytest
from sqlalchemy import select

from server.ingest.otlp.auth import OrgAuth, OrgAuthError, OrgPrincipal
from server.storage import session_scope
from server.storage.dbm import operations
from server.storage.org_token_store import KEY_PREFIX, PgOrgTokenStore, hash_key
from server.storage.schema import OrgToken
from tests.server.storage.conftest import cfg, requires_db  # noqa: F401 -- pytest fixture

pytestmark = requires_db


@pytest.fixture
def store(cfg, engine) -> PgOrgTokenStore:  # noqa: F811
    operations.init(cfg, force=True)
    return PgOrgTokenStore()


@pytest.fixture
def engine(cfg):  # noqa: F811
    from server.storage.engine import dispose_engine, get_engine

    eng = get_engine(cfg)
    yield eng
    dispose_engine()


def test_issue_then_verify_round_trip(store):
    api_key, _hash = store.issue("org-acme", {"note": "fleet"})
    assert api_key.startswith(KEY_PREFIX)      # distinguishable from a producer key on sight
    assert OrgAuth(store).verify(f"Bearer {api_key}") == OrgPrincipal(org_id="org-acme")


def test_plaintext_key_is_nowhere_in_the_database(store):
    api_key, token_hash = store.issue("org-acme", {"note": "fleet"})
    with session_scope() as s:
        rows = s.execute(select(OrgToken)).scalars().all()
        blob = " ".join(f"{r.token_hash}{r.org_id}{r.descriptor}" for r in rows)
    assert api_key not in blob
    assert api_key.removeprefix(KEY_PREFIX) not in blob
    assert token_hash == hash_key(api_key)


def test_revoked_key_is_rejected(store):
    api_key, token_hash = store.issue("org-acme")
    auth = OrgAuth(store, cache_max=0)         # cache off: this is about the store, not TTL
    assert auth.verify(f"Bearer {api_key}").org_id == "org-acme"

    store.set_revoked(token_hash, True)
    with pytest.raises(OrgAuthError) as exc:
        auth.verify(f"Bearer {api_key}")
    assert exc.value.reason == "revoked"


def test_unknown_and_malformed_headers_are_distinguished_in_logs_only(store):
    auth = OrgAuth(store)
    for header in (None, "", "Basic abc", "Bearer ", "Bearer    "):
        with pytest.raises(OrgAuthError) as exc:
            auth.verify(header)
        assert exc.value.reason == "missing"
    with pytest.raises(OrgAuthError) as exc:
        auth.verify(f"Bearer {KEY_PREFIX}never-issued")
    # A different reason for the operator; the route answers a bare 401 either way.
    assert exc.value.reason == "unknown"


def test_a_rejected_key_never_enters_the_cache(store):
    auth = OrgAuth(store)
    with pytest.raises(OrgAuthError):
        auth.verify(f"Bearer {KEY_PREFIX}never-issued")
    assert auth.cache_size() == 0


def test_cache_hit_survives_the_row_disappearing_until_it_expires(store, monkeypatch):
    """The stated §4.5 exposure, pinned: revocation lands in another process, so this one
    keeps accepting the key for at most one TTL window. Bounded, and short."""
    api_key, token_hash = store.issue("org-acme")
    auth = OrgAuth(store)
    assert auth.verify(f"Bearer {api_key}").org_id == "org-acme"
    assert auth.cache_size() == 1

    store.set_revoked(token_hash, True)
    assert auth.verify(f"Bearer {api_key}").org_id == "org-acme"   # still cached

    monkeypatch.setattr("server.ingest.otlp.auth._CACHE_TTL_S", 0.0)
    auth._cache.clear()                        # force the next verify through the store
    with pytest.raises(OrgAuthError) as exc:
        auth.verify(f"Bearer {api_key}")
    assert exc.value.reason == "revoked"


def test_cache_expires_by_wall_clock(store):
    api_key, _hash = store.issue("org-acme")
    auth = OrgAuth(store)
    auth.verify(f"Bearer {api_key}")
    assert auth.cache_size() == 1
    # Reach into the entry rather than sleep out the real 5 s TTL.
    principal, _expiry = auth._cache[hash_key(api_key)]
    auth._cache[hash_key(api_key)] = (principal, time.monotonic() - 1)
    assert auth._cache_get(hash_key(api_key)) is None
    assert auth.cache_size() == 0              # expired entries are dropped, not left to rot


def test_cache_is_bounded(store):
    """Unbounded, one entry went in per distinct key presented -- a caller with N keys
    pinned N principals for the life of the process."""
    auth = OrgAuth(store, cache_max=2)
    for i in range(5):
        api_key, _hash = store.issue(f"org-{i}")
        auth.verify(f"Bearer {api_key}")
    assert auth.cache_size() == 2


@pytest.mark.asyncio
async def test_verify_async_matches_verify(store):
    api_key, _hash = store.issue("org-acme")
    auth = OrgAuth(store)
    assert (await auth.verify_async(f"Bearer {api_key}")).org_id == "org-acme"   # miss path
    assert (await auth.verify_async(f"Bearer {api_key}")).org_id == "org-acme"   # cache path
    with pytest.raises(OrgAuthError):
        await auth.verify_async("Bearer nope")


# ------------------------------------------------------------------- store

def test_hash_prefix_lookup_finds_the_token_an_admin_can_no_longer_see(store):
    _key, token_hash = store.issue("org-acme", {"note": "laptop fleet"})
    found = store.find_by_hash_prefix(token_hash[:12])
    assert [t.token_hash for t in found] == [token_hash]
    assert store.find_by_hash_prefix("ffffffffffff") == []


def test_list_for_org_includes_revoked(store):
    _k1, h1 = store.issue("org-acme")
    _k2, _h2 = store.issue("org-acme")
    _k3, _h3 = store.issue("org-other")
    store.set_revoked(h1, True)
    listed = store.list_for_org("org-acme")
    assert len(listed) == 2
    assert {t.revoked for t in listed} == {True, False}


def test_set_revoked_on_a_missing_token_reports_it(store):
    assert store.set_revoked("f" * 64, True) is False
