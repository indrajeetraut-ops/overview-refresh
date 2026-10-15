"""Layer 2 (`org-expand`) output record models: one pydantic model per emitted JSONL file.

Unlike Layer 1's `Rec` (`extra="ignore"`, built for tolerant reading), `OrgRec` forbids unknown
fields: these types are the writer's own contract, so a typo'd field name should fail loudly
rather than silently vanish. Grows across tasks P2-P8 as each output file is introduced.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

Nonempty = Annotated[str, Field(min_length=1, pattern=r"\S")]

#: Layer 2's own generator version, stamped into every artifact's `info.synthetic` (plan D12)
#: and the org dataset's manifest (P8). Bumped only for a breaking change to this package's
#: output contract -- independent of Layer 1's `runner.GENERATOR_VERSION`.
GENERATOR_VERSION = "1.0.0"


class OrgRec(BaseModel):
    model_config = ConfigDict(extra="forbid")


def synthetic_marker(org_seed: int) -> dict:
    """The `info.synthetic` / `info_blob` marker every synthetic row carries (plan §4 D12)."""
    return {"generator_version": GENERATOR_VERSION, "org_seed": org_seed}


class UserMapRecord(OrgRec):
    l1_user_id: Nonempty
    user_id: Nonempty          # mainline id: "github:<login>" (D5)
    github_login: Nonempty
    team_id: Nonempty


# --------------------------------------------------------------------------------------------
# directory.jsonl / user_prefs.jsonl (P3) -- the DirectoryRecord/TeamRecord wire shape
# (server.directory.models), reused verbatim so `sync()` is the only writer of `users`/`teams`.
# --------------------------------------------------------------------------------------------

class DirectoryTeamRecord(OrgRec):
    team_id: Nonempty
    org_id: Nonempty
    name: Nonempty
    parent_team_id: Nonempty | None = None
    source_provider: Nonempty = "csv"


class ExternalKeyRecord(OrgRec):
    provider: Nonempty
    external_id: Nonempty


class TeamMembershipRecord(OrgRec):
    team_id: Nonempty
    role_in_team: Nonempty | None = None


class RoleGrantRecord(OrgRec):
    role: Nonempty
    scope_type: Nonempty
    scope_id: Nonempty


class DirectoryPersonRecord(OrgRec):
    external_keys: list[ExternalKeyRecord] = Field(min_length=1)
    email: Nonempty | None = None
    display_name: Nonempty | None = None
    status: Nonempty = "active"
    teams: list[TeamMembershipRecord] = Field(default_factory=list)
    roles: list[RoleGrantRecord] = Field(default_factory=list)
    source_provider: Nonempty = "csv"


class UserPrefsRecord(OrgRec):
    user_id: Nonempty          # mainline id
    github_user: Nonempty
    timezone: Nonempty
    primary_team_id: Nonempty


# --------------------------------------------------------------------------------------------
# git_installations.jsonl / git_repos.jsonl / repositories.jsonl (P3)
# --------------------------------------------------------------------------------------------

class GitInstallationRecord(OrgRec):
    installation_id: int
    org_id: Nonempty
    account_login: Nonempty
    account_type: Nonempty = "Organization"
    repo_selection: Nonempty = "all"
    state: Nonempty = "active"
    installed_at: datetime
    updated_at: datetime
    info: dict = Field(default_factory=dict)


class GitRepoRecord(OrgRec):
    repo_id: Nonempty          # git-side id: "github:<repo_num>"
    repo_num: int
    full_name: Nonempty        # "<owner>/<name>"
    owner: Nonempty
    default_branch: Nonempty
    private: bool
    installation_id: int
    org_id: Nonempty
    backfill_cursor: str | None = None
    backfill_state: Nonempty = "done"
    first_seen_at: datetime
    updated_at: datetime
    info: dict = Field(default_factory=dict)


class SessionInfoRecord(OrgRec):
    """`session_info.jsonl` (plan §5 P7). `info` carries the many derived sub-keys (title, repo,
    cache, carry, channel_cost, cache_busts, resume_cost, interrupted, compactions, and --
    root-only -- context_curve/story/segments/practices) as a plain dict rather than its own
    strict model: the shape is heterogeneous and mirrors `graph_fold.info_of` field-for-field,
    which is itself untyped for the same reason."""
    session_id: Nonempty
    models: dict[str, dict[str, int]]
    tokens_total: Annotated[int, Field(ge=0)]
    cost_effective_usd_micros: int
    cost_usd: Decimal
    datasource: Nonempty = "jsonl_only"
    jsonl_lag_s: float | None = None
    otel: dict = Field(default_factory=dict)
    repo_id: Nonempty
    repo_id_provenance: Nonempty = "remote"
    turn_count: Annotated[int, Field(ge=0)]
    long_turn_count: Annotated[int, Field(ge=0)]
    compaction_count: Annotated[int, Field(ge=0)]
    subagent_count: Annotated[int, Field(ge=0)]
    error_count: Annotated[int, Field(ge=0)]
    turns: list[dict] = Field(default_factory=list)
    files: dict[str, dict] = Field(default_factory=dict)
    tools: dict[str, dict] = Field(default_factory=dict)
    skills: list = Field(default_factory=list)
    subagents: dict[str, dict] = Field(default_factory=dict)
    pr_list: list[dict] = Field(default_factory=list)
    info: dict = Field(default_factory=dict)


class RepositoryRecord(OrgRec):
    repo_id: Nonempty          # session-side id: "github:<owner>/<name>"
    kind: Nonempty = "git_remote"
    label: Nonempty            # "<owner>/<name>"
    first_seen_at: datetime
    last_seen_at: datetime
    info: dict = Field(default_factory=dict)


class ExpectedRollupRecord(OrgRec):
    """One row per `usage.jsonl` record, mapped onto mainline ids -- the loader's I1 oracle."""
    date: date
    user_id: Nonempty          # mainline id
    repo_id: Nonempty          # session-side id
    agent: Nonempty
    model: Nonempty
    input: Annotated[int, Field(ge=0)]
    cache_write_5m: Annotated[int, Field(ge=0)]
    cache_write_1h: Annotated[int, Field(ge=0)]
    cache_read: Annotated[int, Field(ge=0)]
    output: Annotated[int, Field(ge=0)]


class PrReworkRecord(OrgRec):
    artifact_id: Nonempty
    rework_ratio: Annotated[float, Field(ge=0, le=1)]


# --------------------------------------------------------------------------------------------
# git_artifacts.jsonl / git_artifact_events.jsonl / pr_issue_links.jsonl / session_git_links.jsonl
# (P6/P8) -- git.py builds these as plain dicts (one shared 30-key shape across every artifact
# `kind`, matching map_github's mapper output field-for-field); these wrap them for the writer.
# --------------------------------------------------------------------------------------------

class GitArtifactRecord(OrgRec):
    artifact_id: Nonempty
    kind: Nonempty
    repo_id: Nonempty
    org_id: Nonempty
    number: int | None = None
    sha: str | None = None
    author_login: str | None = None
    author_email: str | None = None
    user_id: str | None = None
    is_bot: bool = False
    state: str | None = None
    title: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    closed_at: str | None = None
    merged_at: str | None = None
    stats: dict = Field(default_factory=dict)
    files: list[dict] | None = None
    parent_artifact_id: str | None = None
    labels: list[str] = Field(default_factory=list)
    milestone: str | None = None
    linked_issue: str | None = None
    merge_commit_sha: str | None = None
    before_sha: str | None = None
    after_sha: str | None = None
    integration_method: str | None = None
    reverts_sha: str | None = None
    commit_shas: list[str] | None = None
    info: dict = Field(default_factory=dict)


class GitArtifactEventRecord(OrgRec):
    artifact_id: Nonempty
    action: Nonempty
    to_state: Nonempty
    occurred_at: Nonempty
    recorded_at: Nonempty
    source_key: Nonempty


class PrIssueLinkRecord(OrgRec):
    pr_artifact_id: Nonempty
    tracker: Nonempty
    project_key: Nonempty
    number: int
    issue_artifact_id: Nonempty
    method: Nonempty
    confidence: Annotated[float, Field(ge=0, le=1)]
    created_at: Nonempty


class SessionGitLinkRecord(OrgRec):
    session_id: Nonempty
    artifact_id: Nonempty
    method: Nonempty
    confidence: Annotated[float, Field(ge=0, le=1)]
    signals: dict = Field(default_factory=dict)
    created_at: Nonempty


# --------------------------------------------------------------------------------------------
# sessions.jsonl (P8)
# --------------------------------------------------------------------------------------------

class SessionRecord(OrgRec):
    session_id: Nonempty
    producer_id: Nonempty
    org_id: Nonempty
    user_id: Nonempty          # mainline id
    agent: Nonempty
    agent_version: Nonempty
    status: Nonempty = "idle_closed"
    started_at: datetime
    last_event_at: datetime
    ended_at: datetime
    signal_blob: dict = Field(default_factory=lambda: {"events": []})
    info_blob: dict
    parent_session_id: str | None = None
    subagents: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------------------------
# budgets.jsonl (P8, optional -- an empty file when not configured). No allowances.jsonl: token
# allowances were removed (code-econ docs/plan/2026-09-26-cost-based-seat-utilization.md).
# --------------------------------------------------------------------------------------------

class BudgetRecord(OrgRec):
    scope_type: Nonempty        # "org" | "team"
    scope_id: Nonempty
    period: Nonempty
    amount_usd: Annotated[float, Field(ge=0)]
