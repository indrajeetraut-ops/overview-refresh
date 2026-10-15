"""CSV provider v1 (IDENT §3.3, plan G4). No DB."""
import io
from pathlib import Path

import pytest

from server.directory import DirectoryConfig, parse_csv_dir, parse_csv_streams
from server.directory.errors import DirectoryValidationError
from server.directory.models import ExternalKey

FIXTURE = Path("tests/server/directory/fixtures/csv_roster")


def test_parses_fixture_roster():
    res = parse_csv_dir(FIXTURE, DirectoryConfig())

    assert {t.team_id for t in res.teams} == {"eng", "platform", "data"}
    platform = next(t for t in res.teams if t.team_id == "platform")
    assert platform.parent_team_id == "eng"
    assert platform.org_id == "default"          # from config; teams.csv has no org column

    by_email = {r.email: r for r in res.records}
    # anil: email + github keys, lead on platform, org-level lead grant
    anil = by_email["anil@acme.com"]
    assert ExternalKey("csv", "anil@acme.com") in anil.external_keys
    assert ExternalKey("github", "anilk") in anil.external_keys
    assert anil.teams == (anil.teams[0],) and anil.teams[0].team_id == "platform"
    assert anil.teams[0].role_in_team == "lead"
    assert anil.roles and anil.roles[0].role == "lead"
    assert anil.roles[0].scope_type == "org"

    # cy: no github_login -> only the csv key
    cy = by_email["cy@acme.com"]
    assert [k.provider for k in cy.external_keys] == ["csv"]

    # dana: status carried through
    assert by_email["dana@acme.com"].status == "disabled"

    # erin: in members.csv but not users.csv -> bootstrapped, with a warning
    assert "erin@acme.com" in by_email
    assert any("erin@acme.com" in w for w in res.warnings)
    assert by_email["erin@acme.com"].teams[0].team_id == "platform"


def test_unknown_team_in_members_is_an_error_with_row_number():
    users = io.StringIO("email,display_name,github_login,status\na@x.com,A,,active\n")
    teams = io.StringIO("team_id,name,parent_team_id\neng,Engineering,\n")
    members = io.StringIO("email,team_id,role_in_team,role\na@x.com,ghost,,\n")
    with pytest.raises(DirectoryValidationError) as ei:
        parse_csv_streams(users, teams, members, org_id="acme")
    assert "row 2" in str(ei.value) and "ghost" in str(ei.value)


def test_user_row_with_no_identity_is_an_error():
    users = io.StringIO("email,display_name,github_login,status\n,Nobody,,active\n")
    with pytest.raises(DirectoryValidationError) as ei:
        parse_csv_streams(users, None, None)
    assert "row 2" in str(ei.value)


def test_teams_and_members_are_optional():
    users = io.StringIO("email,display_name,github_login,status\na@x.com,A,agh,active\n")
    res = parse_csv_streams(users, None, None)
    assert len(res.records) == 1
    assert res.teams == []


def test_missing_users_csv_is_an_error(tmp_path):
    with pytest.raises(DirectoryValidationError):
        parse_csv_dir(tmp_path, DirectoryConfig())
