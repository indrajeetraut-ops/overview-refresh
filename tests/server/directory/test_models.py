"""DirectoryRecord / TeamRecord contract (IDENT §3.2). No DB."""
import pytest

from server.directory import DirectoryRecord, ExternalKey, RoleGrant, TeamRecord
from server.directory.errors import DirectoryValidationError
from server.directory.models import TeamMembership


def test_from_dict_ignores_unknown_keys():
    # a newer provider may send more fields; this one must not choke (IDENT §3.2).
    rec = DirectoryRecord.from_dict(
        {
            "external_keys": [{"provider": "github", "external_id": "anilk", "extra": "x"}],
            "email": "anil@acme.com",
            "future_field": {"nested": 1},
            "teams": [{"team_id": "platform", "role_in_team": "lead", "unknown": True}],
        }
    )
    assert rec.external_keys == (ExternalKey("github", "anilk"),)
    assert rec.email == "anil@acme.com"
    assert rec.teams == (TeamMembership("platform", "lead"),)


def test_defaults_match_spec():
    rec = DirectoryRecord.from_dict({"external_keys": [{"provider": "csv", "external_id": "a@b.com"}]})
    assert rec.status == "active"
    assert rec.source_provider == "csv"
    assert rec.teams == ()
    assert rec.roles == ()


def test_record_requires_at_least_one_external_key():
    with pytest.raises(DirectoryValidationError):
        DirectoryRecord(external_keys=())


def test_bad_status_rejected():
    with pytest.raises(DirectoryValidationError):
        DirectoryRecord(external_keys=(ExternalKey("csv", "a@b.com"),), status="archived")


def test_bad_role_enum_rejected():
    with pytest.raises(DirectoryValidationError):
        RoleGrant("superuser", "org", "acme")


def test_bad_scope_type_rejected():
    with pytest.raises(DirectoryValidationError):
        RoleGrant("dev", "galaxy", "acme")


def test_external_key_needs_both_parts():
    with pytest.raises(DirectoryValidationError):
        ExternalKey("github", "")


def test_team_record_from_dict_root_parent_is_none():
    t = TeamRecord.from_dict({"team_id": "platform", "org_id": "acme", "name": "Platform"})
    assert t.parent_team_id is None
    assert t.source_provider == "csv"
