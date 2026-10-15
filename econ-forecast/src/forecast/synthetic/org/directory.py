"""Build the directory wire records (`directory_teams.jsonl`, `directory_people.jsonl`,
`user_map.jsonl`, `user_prefs.jsonl`) from a `ResolvedOrg` (plan §5 P3).

Every rostered user's `sync()`-facing record here becomes the mainline `users` row the rest of
the dataset points at (D5): `user_id = "github:" + login`. Unassigned Layer 1 users (not on
any roster team) are never emitted.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from forecast.artifact_io import read_records
from ..models import UserProfileRecord
from ..randomness import rng_for
from . import ids
from .config import ResolvedOrg
from .models import (
    DirectoryPersonRecord, DirectoryTeamRecord, ExternalKeyRecord, RoleGrantRecord,
    TeamMembershipRecord, UserMapRecord, UserPrefsRecord,
)


@dataclass(frozen=True)
class DirectoryWire:
    teams: list[DirectoryTeamRecord]
    people: list[DirectoryPersonRecord]


def resolve_leads(resolved: ResolvedOrg) -> dict[str, str]:
    """Explicit `org.leads` win; every other team's lead is the member with the heaviest
    profile (by the profile's own `daily_input_tokens`, i.e. heavy > mid > light for any
    profile naming), ties broken by an `rng_for(seed, "lead", team_id)` shuffle."""
    org = resolved.config.org
    leads = dict(resolved.leads)
    remaining = [name for name in resolved.team_order if name not in leads]
    if not remaining:
        return leads

    profiles_path = Path(org.series_dir) / "user_profiles.jsonl"
    assignments = read_records(profiles_path, UserProfileRecord, schema_version=2)
    profile_by_user = {a.user_id: a.profile for a in assignments}
    weight_by_profile = {name: profile.daily_input_tokens
                         for name, profile in resolved.series.profiles.items()}

    for team_name in remaining:
        team = resolved.teams_by_name[team_name]
        members = sorted(team.user_ids)
        weights = {uid: weight_by_profile[profile_by_user[uid]] for uid in members}
        heaviest = max(weights.values())
        candidates = sorted(uid for uid, w in weights.items() if w == heaviest)
        if len(candidates) > 1:
            shuffled = list(candidates)
            rng_for(org.seed, "lead", ids.team_slug(team_name)).shuffle(shuffled)
            leads[team_name] = shuffled[0]
        else:
            leads[team_name] = candidates[0]
    return leads


def build_directory(resolved: ResolvedOrg) -> tuple[DirectoryWire, list[UserMapRecord], list[UserPrefsRecord]]:
    org = resolved.config.org
    directory_org_id = org.directory_org_id
    root_id = org.root_team.team_id

    teams: list[DirectoryTeamRecord] = [DirectoryTeamRecord(
        team_id=root_id, org_id=directory_org_id, name=org.root_team.name, parent_team_id=None)]
    for group in org.groups:
        teams.append(DirectoryTeamRecord(team_id=group.team_id, org_id=directory_org_id,
                                         name=group.name, parent_team_id=root_id))
    group_parent = {child: group.team_id for group in org.groups for child in group.children}
    for team_name in resolved.team_order:
        team_id = ids.team_slug(team_name)
        parent = group_parent.get(team_name, root_id)
        display = team_name.replace("_", " ").title()
        teams.append(DirectoryTeamRecord(team_id=team_id, org_id=directory_org_id,
                                         name=display, parent_team_id=parent))

    leads = resolve_leads(resolved)
    timezone = resolved.series.series.timezone  # org.timezone_from_series is Literal[True]

    people: list[DirectoryPersonRecord] = []
    user_map: list[UserMapRecord] = []
    user_prefs: list[UserPrefsRecord] = []
    for team_name in resolved.team_order:
        team = resolved.teams_by_name[team_name]
        team_id = ids.team_slug(team_name)
        lead_id = leads.get(team_name)
        for l1_user_id in sorted(team.user_ids):
            user = resolved.users_by_id[l1_user_id]
            login = user.github_login
            mainline_id = f"github:{login}"
            is_lead = l1_user_id == lead_id
            people.append(DirectoryPersonRecord(
                external_keys=[ExternalKeyRecord(provider="github", external_id=login),
                               ExternalKeyRecord(provider="synthetic", external_id=l1_user_id)],
                email=user.email, display_name=user.name, status="active",
                teams=[TeamMembershipRecord(team_id=team_id,
                                            role_in_team="lead" if is_lead else "member")],
                roles=([RoleGrantRecord(role="lead", scope_type="team", scope_id=team_id)]
                       if is_lead else []),
            ))
            user_map.append(UserMapRecord(l1_user_id=l1_user_id, user_id=mainline_id,
                                          github_login=login, team_id=team_id))
            user_prefs.append(UserPrefsRecord(user_id=mainline_id, github_user=login,
                                              timezone=timezone, primary_team_id=team_id))

    admin = org.admin
    people.append(DirectoryPersonRecord(
        external_keys=[ExternalKeyRecord(provider="github", external_id=admin.github_login)],
        email=admin.email, display_name=admin.display_name, status="active", teams=[],
        roles=[RoleGrantRecord(role="admin", scope_type="org", scope_id=directory_org_id)],
    ))
    user_prefs.append(UserPrefsRecord(user_id=f"github:{admin.github_login}",
                                      github_user=admin.github_login, timezone=timezone,
                                      primary_team_id=root_id))

    return DirectoryWire(teams=teams, people=people), user_map, user_prefs
