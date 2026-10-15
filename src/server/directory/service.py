"""Directory read service -- what F2's role gate and Track A aggregation consume
(docs/design/identity-ingestion.md §3, §5; implementation plan G6).

Framework-free: every function takes a plain `Session` + `QueryCatalog` and returns plain
values, so it is unit-testable without an HTTP client (`api/service.py` is the precedent).

`resolve_viewer` is the shape F2's FastAPI dependency wraps -- the trusted-header viewer id
comes in, the `{user_id, roles, visible_teams, visible_repos}` scope comes out. SSO replaces
only the header->viewer_id step, in F2's auth seam (IDENT §8).
"""
from __future__ import annotations

from collections.abc import Mapping

from sqlalchemy.orm import Session

from server.storage.queries import QueryCatalog

from .config import DirectoryConfig

# Roles that widen visibility beyond a user's own team membership. A plain `dev` -- including
# the default `dev@org` grant `sync()` writes -- never does; otherwise every developer would
# see the whole org.
_ELEVATED_ROLES = ("lead", "admin")


# ------------------------------------------------------------- F2 AUTH SEAM (IDENT §8, seam 3)
# F2 (the API role gate) wraps `resolve_viewer` in a FastAPI dependency. AuthN is deferred
# (IDENT §3 ground rule 4): today the viewer's identity is a trusted header, and
# `viewer_id_from_headers` is the single place it is read. When OIDC/SAML SSO lands (IDENT §8
# v2), the token is verified *inside this function* and its subject becomes the viewer id --
# `resolve_viewer` and everything downstream are unchanged. No FastAPI import here: this module
# stays framework-free (api ground rule 1); F2's `app.py` builds the actual `Depends(...)`.
TRUSTED_VIEWER_HEADER = "x-talium-viewer"


def viewer_id_from_headers(headers: Mapping[str, str]) -> str | None:
    """The trusted-header viewer id (case-insensitive). SSO replaces the body of this
    function with token verification; its callers do not change."""
    for key, value in headers.items():
        if key.lower() == TRUSTED_VIEWER_HEADER:
            return value.strip() or None
    return None


def resolve_user_id(session: Session, catalog: QueryCatalog, viewer_id: str) -> str | None:
    """Map a trusted-header viewer id to a `user_id`: it is either already one, or an
    external identity on the link spine."""
    if session.execute(catalog.get("directory", "viewer_is_user_id"), {"viewer_id": viewer_id}).first():
        return viewer_id
    row = session.execute(
        catalog.get("directory", "resolve_link_any_provider"), {"external_id": viewer_id}
    ).first()
    return row[0] if row else None


def team_of(session: Session, catalog: QueryCatalog, user_id: str) -> list[str]:
    """The user's teams, `primary_team_id` first when set (IDENT §7 default attribution)."""
    teams = [
        r[0] for r in session.execute(
            catalog.get("directory", "team_ids_of_user"), {"user_id": user_id}
        )
    ]
    row = session.execute(
        catalog.get("directory", "primary_team_of_user"), {"user_id": user_id}
    ).first()
    primary = row[0] if row else None
    if primary and primary in teams:
        teams = [primary] + [t for t in teams if t != primary]
    elif primary:
        teams = [primary] + teams
    return teams


def descendants(session: Session, catalog: QueryCatalog, team_id: str) -> list[str]:
    """`team_id` and every team beneath it (recursive CTE over `parent_team_id`)."""
    return [
        r[0] for r in session.execute(
            catalog.get("directory", "team_descendants"), {"team_id": team_id}
        )
    ]


def members_of(
    session: Session,
    catalog: QueryCatalog,
    team_id: str,
    include_subteams: bool = False,
) -> list[str]:
    """User ids in a team; optionally across its whole subtree."""
    team_ids = descendants(session, catalog, team_id) if include_subteams else [team_id]
    return sorted(
        r[0] for r in session.execute(
            catalog.get("directory", "members_of_teams"), {"team_ids": team_ids}
        )
    )


def _org_id(session: Session, catalog: QueryCatalog, team_ids: list[str], config: DirectoryConfig) -> str:
    if team_ids:
        row = session.execute(
            catalog.get("directory", "org_of_teams"), {"team_ids": team_ids}
        ).first()
        if row and row[0]:
            return row[0]
    return config.default_org_id


def _roles(session: Session, catalog: QueryCatalog, user_id: str) -> list[dict]:
    return [
        {"role": r.role, "scope_type": r.scope_type, "scope_id": r.scope_id}
        for r in session.execute(catalog.get("directory", "roles_of_user"), {"user_id": user_id})
    ]


def _visible_teams(
    session: Session,
    catalog: QueryCatalog,
    own_teams: list[str],
    roles: list[dict],
    org_id: str,
) -> list[str]:
    visible: set[str] = set(own_teams)
    for r in roles:
        if r["role"] not in _ELEVATED_ROLES:
            continue
        if r["scope_type"] == "org":
            visible.update(
                row[0] for row in session.execute(
                    catalog.get("directory", "team_ids_for_org"), {"org_id": org_id}
                )
            )
        elif r["scope_type"] == "team":
            visible.update(descendants(session, catalog, r["scope_id"]))
    return sorted(visible)


def _repo_ids_for(
    session: Session, catalog: QueryCatalog, user_ids: list[str], *, include_local: bool
) -> set[str]:
    if not user_ids:
        return set()
    return {
        row[0] for row in session.execute(
            catalog.get("directory", "repo_ids_for_users"),
            {"user_ids": sorted(set(user_ids)), "include_local": include_local},
        )
    }


def users_in_scope(
    session: Session,
    catalog: QueryCatalog,
    scope_type: str,
    scope_id: str,
) -> list[str]:
    """Every user a `(scope_type, scope_id)` grant covers -- the set Track A aggregates over.

    - `org`  -> the active roster
    - `team` -> the team and its subteams
    - `repo` -> the repo's contributors (base-table GROUP BY; `path`-tier evidence excluded)
    """
    if scope_type == "org":
        return sorted(r[0] for r in session.execute(catalog.get("directory", "active_user_ids")))
    if scope_type == "team":
        return members_of(session, catalog, scope_id, include_subteams=True)
    if scope_type == "repo":
        return sorted(
            r.user_id for r in session.execute(
                catalog.get("directory", "repo_contributors"), {"repo_id": scope_id}
            )
        )
    return []


def resolve_viewer(
    session: Session,
    catalog: QueryCatalog,
    viewer_id: str,
    config: DirectoryConfig | None = None,
) -> dict | None:
    """The F2 dependency's payload: `{user_id, roles, visible_teams, visible_repos}`.
    Returns None when the viewer id resolves to no user."""
    config = config or DirectoryConfig()
    user_id = resolve_user_id(session, catalog, viewer_id)
    if user_id is None:
        return None

    own_teams = team_of(session, catalog, user_id)
    org_id = _org_id(session, catalog, own_teams, config)
    roles = _roles(session, catalog, user_id)
    visible_teams = _visible_teams(session, catalog, own_teams, roles, org_id)

    # repos: the viewer's own (their local repos included) + everyone in a team they can see
    # (cross-user, so local labels excluded) + any repo named directly in a role grant.
    repo_ids = _repo_ids_for(session, catalog, [user_id], include_local=True)
    if visible_teams:
        members = sorted(
            r[0] for r in session.execute(
                catalog.get("directory", "members_of_teams"), {"team_ids": visible_teams}
            )
        )
        repo_ids |= _repo_ids_for(session, catalog, members, include_local=False)
    repo_ids.update(r["scope_id"] for r in roles if r["scope_type"] == "repo")

    return {
        "user_id": user_id,
        "roles": roles,
        "visible_teams": visible_teams,
        "visible_repos": sorted(repo_ids),
    }
