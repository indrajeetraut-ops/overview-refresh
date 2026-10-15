"""F2: viewer identity + role gate for the manager surface (MVP plan F2).

Wraps `server.directory.resolve_viewer` (Track G, already the `{user_id, roles,
visible_teams, visible_repos}` shape F2 needs) -- this module reads it, it does **not**
embed a role map of its own (F2's own rule). AuthN is a later SSO seam: `viewer_id_from_headers`
is the one place identity comes from; everything below it is unchanged when OIDC/SAML lands
(IDENT §8). The single-user surface (`single_user/service.py`) is untouched by this -- it
stays unauthenticated per the shipped posture (ground rules §2); only new manager endpoints
route through here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from sqlalchemy.orm import Session

from server import directory
from server.storage.queries import QueryCatalog

from .config import ApiConfig
from .errors import ForbiddenError, UnauthorizedError

#: Roles that reach any manager surface at all. A plain `dev` -- including the directory's
#: default `dev@org` grant every synced user gets -- never does (U4's nav gate mirrors this).
_MANAGER_ROLES = ("lead", "admin")


@dataclass(frozen=True)
class Viewer:
    user_id: str
    roles: list[dict]
    visible_teams: list[str]
    visible_repos: list[str]

    @property
    def is_manager(self) -> bool:
        return any(r["role"] in _MANAGER_ROLES for r in self.roles)


def resolve(session: Session, catalog: QueryCatalog, headers: Mapping[str, str],
           config: ApiConfig) -> Viewer:
    """The trusted-header viewer, resolved to Track G's directory tables. Raises
    `UnauthorizedError` (401) for no header or an unresolvable one -- distinct from
    `ForbiddenError` (403), which means the viewer is known but not entitled to this scope."""
    viewer_id = directory.viewer_id_from_headers(headers)
    if viewer_id is None:
        raise UnauthorizedError("missing viewer identity")
    resolved = directory.resolve_viewer(session, catalog, viewer_id, config.directory)
    if resolved is None:
        raise UnauthorizedError("viewer does not resolve to a known user")
    return Viewer(**resolved)


def require_manager(viewer: Viewer) -> None:
    """Org-wide surfaces (Overview/Usage/Teams/Projects, and any budget write): lead/admin
    only. A dev hitting these gets exactly the same 403 a dev hitting another team's endpoint
    does -- there is no visibility to check, because a plain dev has none beyond their own team."""
    if not viewer.is_manager:
        raise ForbiddenError("manager surfaces require lead or admin")


def require_team(viewer: Viewer, team_id: str) -> None:
    """Team-scoped surfaces (Team/Person detail): manager role, AND the team must be in the
    viewer's resolved visible set -- a lead sees only their own teams (F2's own Verify line)."""
    require_manager(viewer)
    if team_id not in viewer.visible_teams:
        raise ForbiddenError(f"team {team_id!r} not visible to this viewer")


def require_repo(viewer: Viewer, repo_id: str) -> None:
    """Repo-scoped surfaces (Project detail, budgets on a repo scope)."""
    require_manager(viewer)
    if repo_id not in viewer.visible_repos:
        raise ForbiddenError(f"repo {repo_id!r} not visible to this viewer")
