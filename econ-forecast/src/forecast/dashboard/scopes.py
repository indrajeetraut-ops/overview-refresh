"""Scope mapping (plan §3.2): forecast scope name -> dashboard scope identity.

`map_scopes` turns the scopes of a dashboard run (organization, teams, projects, named as
the Layer 1 series dataset names them) into the identities the dashboard knows, using the
org dataset that sits next to the series dataset::

    <org-demo>/org/manifest.json            org id guard, `directory_org_id`
    <org-demo>/org/budgets.jsonl            optional org row, must agree with the org id
    <org-demo>/org/directory_teams.jsonl    team ids
    <org-demo>/org/repositories.jsonl       repository ids
    <org-demo>/configs/org.yaml             `org.github_owner`, `repos[].{project,name}`

Mapping rules:

* organization -> `org`; `scope_id` is the manifest's `directory_org_id`; the name is the
  series org id.
* team -> `team`; `scope_id` is the team slug of the Layer 1 team name (the one place this
  package imports from `forecast.synthetic`: the id is defined by that rule), which must
  be a `team_id` of the directory. Directory teams without a Layer 1 scope (a parent team)
  are not exported.
* project -> `repo`; the project's `repos[]` entry names the repository, whose row in
  `repositories.jsonl` is the one labelled `<github_owner>/<repo name>`; `scope_id` is that
  row's `repo_id`, the name is the label.

A scope that cannot be mapped is an error, never a skipped row. Every problem is an
`InputError` with `stage="scopes"`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Sequence

import yaml

from .errors import InputError
from .runner import RunScope

STAGE = "scopes"

#: Forecast scope type -> dashboard scope type.
_SCOPE_TYPES = {"organization": "org", "team": "team", "project": "repo"}


@dataclass(frozen=True)
class DashboardScope:
    """A run scope with the identity the dashboard uses for it."""

    run_scope: RunScope
    scope_type: str  # "org" | "team" | "repo"
    scope_id: str
    name: str


def _fail(message: str) -> InputError:
    return InputError(message, stage=STAGE)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise _fail(f"cannot read {path.name}: {path}") from exc
    if not isinstance(value, dict):
        raise _fail(f"{path.name} must contain a JSON object: {path}")
    return value


def _read_jsonl(path: Path) -> list[dict]:
    """The non-blank lines of a JSON-lines file; each must be an object."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise _fail(f"cannot read {path.name}: {path}") from exc
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise _fail(f"{path.name} line {number} is not valid JSON") from exc
        if not isinstance(row, dict):
            raise _fail(f"{path.name} line {number} is not a JSON object")
        rows.append(row)
    return rows


def _read_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise _fail(f"cannot read {path.name}: {path}") from exc
    if not isinstance(value, dict):
        raise _fail(f"{path.name} must be a mapping: {path}")
    return value


def _text(row: dict, key: str, where: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise _fail(f"{where} has no usable {key!r}")
    return value


def _team_slug(name: str) -> str:
    # Imported here, not at module level: the import pulls in the whole synthetic org
    # package, and this is the only dependency of the dashboard package on it.
    from forecast.synthetic.org.ids import team_slug

    return team_slug(name)


def map_scopes(
    org_demo: Path, run_scopes: Sequence[RunScope], *, org_id: str
) -> tuple[DashboardScope, ...]:
    """Dashboard identities for `run_scopes`, in the same order.

    `org_demo` is the org-demo directory; `org_id` is the series dataset's org id, which
    the org dataset must share (it guards against pairing a series directory with another
    org's data).
    """
    org_dir = Path(org_demo) / "org"
    manifest = _read_json(org_dir / "manifest.json")
    if manifest.get("org_id") != org_id:
        raise _fail(
            f"org dataset belongs to org {manifest.get('org_id')!r}, "
            f"the series dataset to {org_id!r}"
        )
    directory_org_id = _text(manifest, "directory_org_id", "org manifest")

    budgets = _read_jsonl(org_dir / "budgets.jsonl")
    for row in budgets:
        if row.get("scope_type") == "org" and row.get("scope_id") != directory_org_id:
            raise _fail(
                f"budgets.jsonl org row has scope_id {row.get('scope_id')!r}, "
                f"the org manifest has directory_org_id {directory_org_id!r}"
            )

    teams = _read_jsonl(org_dir / "directory_teams.jsonl")
    team_ids = set()
    for row in teams:
        team_id = _text(row, "team_id", "directory_teams.jsonl row")
        if row.get("org_id") != directory_org_id:
            raise _fail(
                f"directory team {team_id!r} belongs to org {row.get('org_id')!r}, "
                f"not {directory_org_id!r}"
            )
        team_ids.add(team_id)

    repositories = _read_jsonl(org_dir / "repositories.jsonl")
    repo_by_label: dict[str, list[str]] = {}
    for row in repositories:
        label = _text(row, "label", "repositories.jsonl row")
        repo_by_label.setdefault(label, []).append(_text(row, "repo_id", f"repository {label!r}"))

    config = _read_yaml(Path(org_demo) / "configs" / "org.yaml")
    org_section = config.get("org")
    owner = org_section.get("github_owner") if isinstance(org_section, dict) else None
    repos = config.get("repos")
    if not isinstance(owner, str) or not owner:
        raise _fail("configs/org.yaml has no org.github_owner")
    if not isinstance(repos, list):
        raise _fail("configs/org.yaml has no repos list")
    repo_by_project: dict[str, list[str]] = {}
    for entry in repos:
        project = entry.get("project") if isinstance(entry, dict) else None
        name = entry.get("name") if isinstance(entry, dict) else None
        if not isinstance(project, str) or not project or not isinstance(name, str) or not name:
            raise _fail("configs/org.yaml has a repos entry without project and name")
        repo_by_project.setdefault(project, []).append(name)

    mapped: list[DashboardScope] = []
    for run_scope in run_scopes:
        kind = _SCOPE_TYPES.get(run_scope.scope_type)
        if kind is None:
            raise _fail(f"unknown forecast scope type {run_scope.scope_type!r}")
        if kind == "org":
            mapped.append(DashboardScope(run_scope, "org", directory_org_id, run_scope.name))
        elif kind == "team":
            slug = _team_slug(run_scope.name)
            if slug not in team_ids:
                raise _fail(
                    f"team {run_scope.name!r} (id {slug!r}) is not in directory_teams.jsonl"
                )
            mapped.append(DashboardScope(run_scope, "team", slug, run_scope.name))
        else:
            names = repo_by_project.get(run_scope.name, [])
            if not names:
                raise _fail(f"project {run_scope.name!r} has no repos entry in configs/org.yaml")
            if len(names) > 1:
                raise _fail(f"project {run_scope.name!r} has several repos entries in configs/org.yaml")
            label = f"{owner}/{names[0]}"
            ids = repo_by_label.get(label, [])
            if not ids:
                raise _fail(
                    f"project {run_scope.name!r}: no repository labelled {label!r} in repositories.jsonl"
                )
            if len(ids) > 1:
                raise _fail(f"repository label {label!r} appears {len(ids)} times in repositories.jsonl")
            mapped.append(DashboardScope(run_scope, "repo", ids[0], label))

    seen: dict[tuple[str, str], RunScope] = {}
    for scope in mapped:
        key = (scope.scope_type, scope.scope_id)
        if key in seen:
            raise _fail(
                f"{scope.run_scope.scope_type} {scope.run_scope.name!r} and "
                f"{seen[key].scope_type} {seen[key].name!r} both map to {scope.scope_type} {scope.scope_id!r}"
            )
        seen[key] = scope.run_scope
    return tuple(mapped)
