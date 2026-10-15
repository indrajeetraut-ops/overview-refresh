"""TZ track (mvp-gap-closure-plan): `GET`/`PUT /v1/users/{id}/timezone`, the self-service
preference every UTC-bucketing call site (precedence.py, single_user/service.py's rhythm/z0)
now reads."""
from datetime import datetime, timezone

import pytest

from server.api.errors import BadRequestError
from server.api.single_user import service
from server.storage import session_scope
from server.storage.schema import User

from .conftest import requires_db

pytestmark = requires_db

NOW = datetime(2026, 8, 7, tzinfo=timezone.utc)


def _seed_user(s, user_id="u1", timezone_=None):
    s.add(User(user_id=user_id, created_at=NOW, timezone=timezone_))


def test_get_timezone_returns_none_when_unset(engine, catalog):
    with session_scope() as s:
        _seed_user(s)

    with session_scope() as s:
        result = service.get_timezone(s, catalog, "u1")

    assert result == {"user_id": "u1", "timezone": None}


def test_get_timezone_returns_none_for_a_user_with_no_users_row_at_all(engine, catalog):
    """No 404 -- the single-user surface's own `id` (today, always the placeholder viewer
    `"me"`, pending real auth) has no guarantee a `users` row was ever created for it, and
    every other route on this surface already tolerates an unknown id the same way."""
    with session_scope() as s:
        result = service.get_timezone(s, catalog, "nobody")

    assert result == {"user_id": "nobody", "timezone": None}


def test_set_timezone_persists_a_valid_iana_zone(engine, catalog):
    with session_scope() as s:
        _seed_user(s)

    with session_scope() as s:
        result = service.set_timezone(s, catalog, "u1", "America/Los_Angeles")
    assert result == {"user_id": "u1", "timezone": "America/Los_Angeles"}

    with session_scope() as s:
        assert service.get_timezone(s, catalog, "u1") == {
            "user_id": "u1", "timezone": "America/Los_Angeles",
        }


def test_set_timezone_creates_the_users_row_when_none_exists_yet(engine, catalog):
    """The real bug this regression test locks in: the live single-user viewer id ("me") has no
    corresponding `users` row until telemetry ingest or directory sync creates one -- neither
    of which a freshly-seeded/local deployment necessarily has. A plain UPDATE would silently
    affect zero rows here; the upsert must create the row instead."""
    with session_scope() as s:
        result = service.set_timezone(s, catalog, "me", "America/Los_Angeles")
    assert result == {"user_id": "me", "timezone": "America/Los_Angeles"}

    with session_scope() as s:
        assert service.get_timezone(s, catalog, "me") == {
            "user_id": "me", "timezone": "America/Los_Angeles",
        }


def test_set_timezone_rejects_an_unrecognized_zone():
    """Validated against the real IANA database, not a hand-maintained allowlist -- a browser
    sends exactly what `Intl.DateTimeFormat().resolvedOptions().timeZone` returns, so a
    rejection here means the request is malformed. No DB touched -- validation raises before
    any query runs, so this needs neither `engine` nor `catalog`."""
    with pytest.raises(BadRequestError):
        service.set_timezone(None, None, "u1", "Mars/Olympus_Mons")


def test_set_timezone_rejects_an_empty_value():
    with pytest.raises(BadRequestError):
        service.set_timezone(None, None, "u1", "")
