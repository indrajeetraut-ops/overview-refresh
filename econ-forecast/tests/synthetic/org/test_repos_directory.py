"""P3: `org/directory.py`, `org/repos.py` (the `org-expand` CLI wiring's own tests moved to the
plan's dedicated `test_cli_org.py` once that file existed, at P8).

Named `test_repos_directory.py` rather than splitting into `test_directory.py`/`test_repos.py`:
plan §2's fixed file list has no separate entry for either, so this stays under the existing
`test_org_config.py`/`test_ids.py` pair rather than adding new files without reporting it.
"""
import re

import pytest

from forecast.synthetic.org.directory import build_directory
from forecast.synthetic.org.repos import build_repos
from server.directory.models import DirectoryRecord
from server.directory.models import TeamRecord as DirectoryTeamWire


# --------------------------------------------------------------------------------------------
# directory.py
# --------------------------------------------------------------------------------------------

def test_directory_records_parse_with_mainline_models(resolved):
    wire, user_map, user_prefs = build_directory(resolved)
    for team in wire.teams:
        DirectoryTeamWire.from_dict(team.model_dump(mode="json"))
    for person in wire.people:
        DirectoryRecord.from_dict(person.model_dump(mode="json"))


def test_every_rostered_user_has_one_team_and_unique_login(resolved):
    wire, user_map, user_prefs = build_directory(resolved)
    rostered = [p for p in wire.people if p.teams]
    assert len(rostered) == sum(len(t.user_ids) for t in resolved.teams)
    for person in rostered:
        assert len(person.teams) == 1
    logins = [key.external_id for p in rostered for key in p.external_keys if key.provider == "github"]
    assert len(logins) == len(set(logins))


def test_each_team_has_exactly_one_lead(resolved):
    from forecast.synthetic.org import ids

    wire, user_map, user_prefs = build_directory(resolved)
    rostered_team_ids = {ids.team_slug(name) for name in resolved.team_order}
    leads_by_team: dict[str, int] = {}
    for person in wire.people:
        for team in person.teams:
            if team.role_in_team == "lead":
                leads_by_team[team.team_id] = leads_by_team.get(team.team_id, 0) + 1
    for team_id in rostered_team_ids:
        assert leads_by_team.get(team_id) == 1, (team_id, leads_by_team)


def test_admin_has_org_admin_grant_and_no_team(resolved):
    wire, user_map, user_prefs = build_directory(resolved)
    admin_login = resolved.config.org.admin.github_login
    admin = next(p for p in wire.people
                if any(k.provider == "github" and k.external_id == admin_login for k in p.external_keys))
    assert admin.teams == []
    assert any(r.role == "admin" and r.scope_type == "org"
              and r.scope_id == resolved.config.org.directory_org_id for r in admin.roles)
    admin_prefs = next(p for p in user_prefs if p.user_id == f"github:{admin_login}")
    assert admin_prefs.primary_team_id == resolved.config.org.root_team.team_id


def test_unrostered_users_not_emitted(resolved):
    wire, user_map, user_prefs = build_directory(resolved)
    rostered_l1_ids = {uid for t in resolved.teams for uid in t.user_ids}
    assert rostered_l1_ids == {row.l1_user_id for row in user_map}
    emitted_synthetic_ids = {
        key.external_id for p in wire.people for key in p.external_keys if key.provider == "synthetic"}
    assert emitted_synthetic_ids == rostered_l1_ids


def test_auto_lead_picks_heaviest_profile(resolved, build_org_config, org_config_dict,
                                          series_inputs):
    # No org.leads set in `org_config_dict` -> every lead is auto-picked. Cross-check against
    # the raw profile assignment + profile weights directly.
    from pathlib import Path

    from forecast.artifact_io import read_records
    from forecast.synthetic.models import UserProfileRecord
    from forecast.synthetic.org import ids

    wire, _, _ = build_directory(resolved)
    profiles = read_records(Path(resolved.config.org.series_dir) / "user_profiles.jsonl",
                            UserProfileRecord, schema_version=2)
    profile_by_user = {p.user_id: p.profile for p in profiles}
    weight_by_profile = {name: p.daily_input_tokens for name, p in resolved.series.profiles.items()}

    for team in resolved.teams:
        lead_person = next(
            p for p in wire.people
            if p.teams and p.teams[0].team_id == ids.team_slug(team.team_name)
            and p.teams[0].role_in_team == "lead")
        lead_l1_id = next(
            k.external_id for k in lead_person.external_keys if k.provider == "synthetic")
        lead_weight = weight_by_profile[profile_by_user[lead_l1_id]]
        max_weight = max(weight_by_profile[profile_by_user[uid]] for uid in team.user_ids)
        assert lead_weight == max_weight


def test_explicit_lead_is_respected(build_org_config, org_config_dict, series_inputs):
    from forecast.synthetic.org.config import validate_org_inputs

    explicit_lead = series_inputs["teams"][0].user_ids[0]
    team_name = series_inputs["teams"][0].team_name
    org_config_dict["org"]["leads"] = {team_name: explicit_lead}
    cfg = build_org_config(org_config_dict)
    resolved = validate_org_inputs(cfg, series_inputs["manifest"], series_inputs["users"],
                                   series_inputs["teams"], series_inputs["series_cfg"],
                                   series_inputs["pricing"])
    wire, _, _ = build_directory(resolved)
    lead_person = next(
        p for p in wire.people
        if any(k.provider == "synthetic" and k.external_id == explicit_lead for k in p.external_keys))
    assert lead_person.teams[0].role_in_team == "lead"


# --------------------------------------------------------------------------------------------
# repos.py
# --------------------------------------------------------------------------------------------

def test_repo_rows_match_d8_regexes(resolved):
    installation, git_repos, repositories = build_repos(resolved)
    for repo in git_repos:
        assert re.fullmatch(r"github:\d+", repo.repo_id)
        assert repo.repo_id == f"github:{repo.repo_num}"
        assert 900_000_000 <= repo.repo_num < 990_000_000
    for repo in repositories:
        assert re.fullmatch(r"github:[a-z0-9-]+/[a-z0-9-]+", repo.repo_id)


def test_lower_full_name_bridges_git_and_session_side(resolved):
    installation, git_repos, repositories = build_repos(resolved)
    git_full_names = {r.full_name.lower() for r in git_repos}
    session_suffixes = {r.repo_id.removeprefix("github:") for r in repositories}
    assert git_full_names == session_suffixes


def test_installation_id_in_range(resolved):
    installation, _, _ = build_repos(resolved)
    assert 800_000_000 <= installation.installation_id < 890_000_000
    assert installation.account_login == resolved.config.org.github_owner


