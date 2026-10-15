"""Build `git_installations.jsonl`, `git_repos.jsonl`, `repositories.jsonl` from a `ResolvedOrg`
(plan §5 P3)."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ids
from .config import ResolvedOrg
from .models import GitInstallationRecord, GitRepoRecord, RepositoryRecord, synthetic_marker


def _installed_at(resolved: ResolvedOrg) -> datetime:
    start = resolved.series.series.start_date
    return datetime.combine(start - timedelta(days=30), time(0, 0), tzinfo=timezone.utc)


def _last_seen_at(resolved: ResolvedOrg) -> datetime:
    series = resolved.series.series
    end_date = series.start_date + timedelta(days=series.days - 1)
    local = datetime.combine(end_date, time(23, 59), tzinfo=ZoneInfo(series.timezone))
    return local.astimezone(timezone.utc)


def build_repos(resolved: ResolvedOrg) -> tuple[GitInstallationRecord, list[GitRepoRecord], list[RepositoryRecord]]:
    org = resolved.config.org
    marker = synthetic_marker(org.seed)
    installed_at = _installed_at(resolved)
    last_seen_at = _last_seen_at(resolved)
    install_id = ids.installation_id(org.github_owner)

    installation = GitInstallationRecord(
        installation_id=install_id, org_id=org.directory_org_id, account_login=org.github_owner,
        account_type="Organization", repo_selection="all", state="active",
        installed_at=installed_at, updated_at=installed_at, info={"synthetic": marker})

    git_repos: list[GitRepoRecord] = []
    repositories: list[RepositoryRecord] = []
    for repo in sorted(resolved.config.repos, key=lambda r: r.name):
        full_name = f"{org.github_owner}/{repo.name}"
        repo_num = ids.repo_num(full_name)
        git_repos.append(GitRepoRecord(
            repo_id=f"github:{repo_num}", repo_num=repo_num, full_name=full_name,
            owner=org.github_owner, default_branch=repo.default_branch, private=repo.private,
            installation_id=install_id, org_id=org.directory_org_id, backfill_cursor=None,
            backfill_state="done", first_seen_at=installed_at, updated_at=installed_at,
            info={"synthetic": marker}))
        repositories.append(RepositoryRecord(
            repo_id=f"github:{full_name.lower()}", kind="git_remote", label=full_name,
            first_seen_at=installed_at, last_seen_at=last_seen_at, info={"synthetic": marker}))

    return installation, git_repos, repositories
