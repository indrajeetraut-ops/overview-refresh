"""CSV provider v1 (docs/design/identity-ingestion.md §3.3, implementation plan G4).

Reads three CSVs and maps each row to the common `DirectoryRecord` / `TeamRecord` shape --
the same records a later SCIM/LDAP/OIDC provider will emit (IDENT §8). This module never
touches the DB; it hands its output to `server.directory.sync`.

    users.csv     email, display_name, github_login, status
    teams.csv     team_id, name, parent_team_id
    members.csv   email, team_id, role_in_team, role      # role = optional org-level grant

Key mapping (IDENT §3.3): `email` -> `external_keys[provider=csv]`,
`github_login` -> `external_keys[provider=github]`. A row with neither is a hard error --
there is no identity to carry.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from ..config import DirectoryConfig
from ..errors import DirectoryValidationError
from ..models import DirectoryRecord, ExternalKey, RoleGrant, TeamMembership, TeamRecord

USERS_FILE = "users.csv"
TEAMS_FILE = "teams.csv"
MEMBERS_FILE = "members.csv"

_SOURCE = "csv"


@dataclass
class CsvParseResult:
    teams: list[TeamRecord] = field(default_factory=list)
    records: list[DirectoryRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip()
    return v or None


def _rows(fh: TextIO) -> list[dict[str, str]]:
    return list(csv.DictReader(fh))


def _parse_teams(fh: TextIO, org_id: str) -> tuple[list[TeamRecord], set[str]]:
    teams: list[TeamRecord] = []
    ids: set[str] = set()
    for i, row in enumerate(_rows(fh), start=2):  # row 1 is the header
        team_id = _clean(row.get("team_id"))
        if not team_id:
            raise DirectoryValidationError(f"{TEAMS_FILE} row {i}: team_id is required")
        name = _clean(row.get("name")) or team_id
        teams.append(
            TeamRecord(
                team_id=team_id,
                org_id=org_id,
                name=name,
                parent_team_id=_clean(row.get("parent_team_id")),
                source_provider=_SOURCE,
            )
        )
        ids.add(team_id)
    return teams, ids


def _parse_users(fh: TextIO) -> dict[str, DirectoryRecord]:
    """email (or, when absent, ``github:<login>``) -> record. Keyed for the members join."""
    out: dict[str, DirectoryRecord] = {}
    for i, row in enumerate(_rows(fh), start=2):
        email = _clean(row.get("email"))
        github_login = _clean(row.get("github_login"))
        if not email and not github_login:
            raise DirectoryValidationError(
                f"{USERS_FILE} row {i}: needs an email or a github_login"
            )
        keys: list[ExternalKey] = []
        if email:
            keys.append(ExternalKey("csv", email))
        if github_login:
            keys.append(ExternalKey("github", github_login))
        status = _clean(row.get("status")) or "active"
        rec = DirectoryRecord(
            external_keys=tuple(keys),
            email=email,
            display_name=_clean(row.get("display_name")),
            status=status,
            source_provider=_SOURCE,
        )
        out[email or f"github:{github_login}"] = rec
    return out


def _augment_from_members(
    fh: TextIO,
    users: dict[str, DirectoryRecord],
    team_ids: set[str],
    org_id: str,
    warnings: list[str],
) -> None:
    """Fold team memberships and org-level role grants onto the user records. A members row
    for an email not in users.csv bootstraps a record (IDENT plan G4)."""
    memberships: dict[str, list[TeamMembership]] = {}
    grants: dict[str, list[RoleGrant]] = {}

    for i, row in enumerate(_rows(fh), start=2):
        email = _clean(row.get("email"))
        team_id = _clean(row.get("team_id"))
        if not email:
            raise DirectoryValidationError(f"{MEMBERS_FILE} row {i}: email is required")
        if not team_id:
            raise DirectoryValidationError(f"{MEMBERS_FILE} row {i}: team_id is required")
        if team_id not in team_ids:
            raise DirectoryValidationError(
                f"{MEMBERS_FILE} row {i}: unknown team_id {team_id!r} (not in {TEAMS_FILE})"
            )
        if email not in users:
            warnings.append(
                f"{MEMBERS_FILE} row {i}: {email} is not in {USERS_FILE}; creating a bootstrap user"
            )
            users[email] = DirectoryRecord(
                external_keys=(ExternalKey("csv", email),),
                email=email,
                source_provider=_SOURCE,
            )
        memberships.setdefault(email, []).append(
            TeamMembership(team_id, _clean(row.get("role_in_team")))
        )
        role = _clean(row.get("role"))
        if role:
            grants.setdefault(email, []).append(RoleGrant(role, "org", org_id))

    for email, rec in list(users.items()):
        mem = tuple(memberships.get(email, ()))
        grn = tuple(grants.get(email, ()))
        if mem or grn:
            users[email] = DirectoryRecord(
                external_keys=rec.external_keys,
                email=rec.email,
                display_name=rec.display_name,
                status=rec.status,
                teams=mem or rec.teams,
                roles=grn or rec.roles,
                source_provider=rec.source_provider,
            )


def parse_csv_streams(
    users_fh: TextIO,
    teams_fh: TextIO | None,
    members_fh: TextIO | None,
    *,
    org_id: str = "default",
) -> CsvParseResult:
    result = CsvParseResult()
    team_ids: set[str] = set()
    if teams_fh is not None:
        result.teams, team_ids = _parse_teams(teams_fh, org_id)

    users = _parse_users(users_fh)
    if members_fh is not None:
        _augment_from_members(members_fh, users, team_ids, org_id, result.warnings)

    result.records = list(users.values())
    return result


def parse_csv_dir(path: str | Path, config: DirectoryConfig | None = None) -> CsvParseResult:
    """Read `users.csv` (required), `teams.csv` and `members.csv` (each optional) from a
    directory. `org_id` comes from config (`teams.csv` carries no org column, IDENT §3.3)."""
    config = config or DirectoryConfig()
    d = Path(path)
    users_path = d / USERS_FILE
    if not users_path.exists():
        raise DirectoryValidationError(f"{users_path} not found ({USERS_FILE} is required)")
    teams_path = d / TEAMS_FILE
    members_path = d / MEMBERS_FILE

    with users_path.open(encoding="utf-8", newline="") as ufh:
        teams_ctx = teams_path.open(encoding="utf-8", newline="") if teams_path.exists() else None
        members_ctx = (
            members_path.open(encoding="utf-8", newline="") if members_path.exists() else None
        )
        try:
            return parse_csv_streams(
                ufh, teams_ctx, members_ctx, org_id=config.default_org_id
            )
        finally:
            if teams_ctx is not None:
                teams_ctx.close()
            if members_ctx is not None:
                members_ctx.close()
