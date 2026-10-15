"""Directory schema shape (IDENT §3.1, plan G1). No DB -- pure metadata assertions."""
from server.storage.schema import Base

DIRECTORY_TABLES = {"identity_links", "teams", "team_members", "role_assignments"}


def test_directory_tables_declared():
    assert DIRECTORY_TABLES <= set(Base.metadata.tables)


def test_users_gained_directory_fields():
    cols = set(Base.metadata.tables["users"].columns.keys())
    assert {"display_name", "status", "primary_team_id", "source_provider"} <= cols
    # the pre-directory columns are untouched
    assert {"user_id", "email", "github_user", "account_uuid"} <= cols


def test_users_status_has_server_default():
    # the existing telemetry INSERTs name columns explicitly and never set status.
    col = Base.metadata.tables["users"].columns["status"]
    assert col.server_default is not None


def test_identity_links_keys():
    t = Base.metadata.tables["identity_links"]
    assert set(t.columns.keys()) == {"provider", "external_id", "user_id"}
    assert [c.name for c in t.primary_key] == ["provider", "external_id"]
    assert {ix.name for ix in t.indexes} == {"ix_identity_links_user_id"}


def test_teams_keys():
    t = Base.metadata.tables["teams"]
    assert set(t.columns.keys()) == {"team_id", "org_id", "name", "parent_team_id", "source_provider"}
    assert [c.name for c in t.primary_key] == ["team_id"]
    assert "ix_teams_parent_team_id" in {ix.name for ix in t.indexes}


def test_team_members_keys():
    t = Base.metadata.tables["team_members"]
    assert set(t.columns.keys()) == {"team_id", "user_id", "role_in_team"}
    assert [c.name for c in t.primary_key] == ["team_id", "user_id"]
    assert t.columns["role_in_team"].nullable is True


def test_role_assignments_keys():
    t = Base.metadata.tables["role_assignments"]
    assert set(t.columns.keys()) == {"user_id", "role", "scope_type", "scope_id"}
    assert [c.name for c in t.primary_key] == ["user_id", "role", "scope_type", "scope_id"]


def test_no_foreign_keys_on_directory_tables():
    # storage.md §3.1 posture -- reconcile keeps identity single, not a constraint.
    for name in DIRECTORY_TABLES:
        assert not Base.metadata.tables[name].foreign_keys
