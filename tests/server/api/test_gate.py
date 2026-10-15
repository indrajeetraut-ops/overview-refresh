"""F2: viewer identity + role gate (`server/api/gate.py`)."""
import pytest

from server.api import gate
from server.api.config import ApiConfig
from server.api.errors import ForbiddenError, UnauthorizedError
from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord, sync
from server.storage import session_scope

from .conftest import requires_db


def _seed(s, catalog):
    sync(
        s, catalog,
        [
            DirectoryRecord(external_keys=(ExternalKey("csv", "dev1"),), email="dev1@x.com",
                            teams=(TeamMembership("platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "lead1"),), email="lead1@x.com",
                            teams=(TeamMembership("platform"),),
                            roles=(RoleGrant("lead", "team", "platform"),)),
            DirectoryRecord(external_keys=(ExternalKey("csv", "admin1"),), email="admin1@x.com",
                            roles=(RoleGrant("admin", "org", "default"),)),
        ],
        teams=[TeamRecord("platform", "default", "Platform"), TeamRecord("growth", "default", "Growth")],
    )


@requires_db
def test_resolve_raises_unauthorized_without_header(engine, catalog):
    with session_scope() as s, pytest.raises(UnauthorizedError):
        gate.resolve(s, catalog, {}, ApiConfig())


@requires_db
def test_resolve_raises_unauthorized_for_an_unknown_viewer(engine, catalog):
    with session_scope() as s, pytest.raises(UnauthorizedError):
        gate.resolve(s, catalog, {"x-talium-viewer": "ghost"}, ApiConfig())


@requires_db
def test_a_plain_dev_is_not_a_manager(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        viewer = gate.resolve(s, catalog, {"x-talium-viewer": "dev1"}, ApiConfig())
        assert viewer.visible_teams == ["platform"]
        assert not viewer.is_manager
        with pytest.raises(ForbiddenError):
            gate.require_manager(viewer)


@requires_db
def test_a_dev_hitting_another_teams_endpoint_is_forbidden(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        viewer = gate.resolve(s, catalog, {"x-talium-viewer": "dev1"}, ApiConfig())
        with pytest.raises(ForbiddenError):
            gate.require_team(viewer, "growth")


@requires_db
def test_a_lead_sees_only_their_own_team(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        viewer = gate.resolve(s, catalog, {"x-talium-viewer": "lead1"}, ApiConfig())
        assert viewer.is_manager
        gate.require_team(viewer, "platform")  # does not raise
        with pytest.raises(ForbiddenError):
            gate.require_team(viewer, "growth")


@requires_db
def test_an_admin_sees_every_team(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        viewer = gate.resolve(s, catalog, {"x-talium-viewer": "admin1"}, ApiConfig())
        assert set(viewer.visible_teams) == {"platform", "growth"}
        gate.require_team(viewer, "platform")
        gate.require_team(viewer, "growth")
        gate.require_manager(viewer)


@requires_db
def test_header_lookup_is_case_insensitive(engine, catalog):
    with session_scope() as s:
        _seed(s, catalog)
        viewer = gate.resolve(s, catalog, {"X-Talium-Viewer": "admin1"}, ApiConfig())
        assert viewer.user_id.endswith("admin1")
