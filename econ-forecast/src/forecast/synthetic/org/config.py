"""Layer 2 (`org-expand`) configuration: `org.yaml` schema, loading, and cross-dataset validation.

Reuses Layer 1's strict base model, annotated types, and `shares()` helper (plan §3) so the two
layers validate mixtures and bounds identically. All cross-file checks in §3.5 live in
`validate_org_inputs`, which raises `ConfigError` naming the offending field -- never a bare
assertion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Annotated, Literal, Mapping

import yaml
from pydantic import Field, ValidationError, field_validator, model_validator

from ..config import (
    CalendarDate, Identifier, Name, Number, Pct, PositiveInt, Ratio, Seed, Strict, UniqueLoader,
    Versioned, ident, name, shares,
)
from ..errors import ConfigError
from ..models import TeamRecord, UserRecord

_SLUG_RE = re.compile(r"[a-z0-9-]+")


def _email_shape(value: str) -> str:
    if value.count("@") != 1 or any(char.isspace() for char in value):
        raise ValueError("invalid email shape")
    local, domain = value.split("@")
    if not local or not domain:
        raise ValueError("invalid email shape")
    return value


class MinMax(Strict):
    min: Number
    max: Number

    @model_validator(mode="after")
    def ordered(self):
        if self.min > self.max:
            raise ValueError("min must not exceed max")
        return self


# --------------------------------------------------------------------------------------------
# org: block
# --------------------------------------------------------------------------------------------

class RootTeam(Strict):
    team_id: Identifier = "eng"
    name: Name = "Engineering"


class Group(Strict):
    team_id: Identifier
    name: Name
    children: list[Name] = Field(min_length=1)


class Admin(Strict):
    github_login: Identifier
    display_name: Name
    email: Annotated[str, Field()]

    @field_validator("email")
    @classmethod
    def email_shape(cls, value):
        _email_shape(value)
        return value

    @field_validator("github_login")
    @classmethod
    def login_shape(cls, value):
        if not re.fullmatch(r"[a-z0-9-]{1,39}", value):
            raise ValueError("admin.github_login must match [a-z0-9-]{1,39}")
        return value


class OrgOptions(Strict):
    series_dir: Path
    #: Catalog version to price with; None = the current catalog version (resolved and
    #: recorded in the dataset manifest). A version name alone does not reproduce a dataset --
    #: the dataset carries its own `pricing/` snapshot (see `pricing_dir`).
    pricing_version: str | None = None
    #: Org id for channel selection; None = vendor channels (org id "").
    pricing_org: str | None = None
    #: A self-contained pricing snapshot (`catalog/`, `history.yaml`, optional channel/alias
    #: config) written by `cli_synth_org.py` into the dataset. None = the live catalog.
    pricing_dir: Path | None = None
    seed: Seed
    directory_org_id: Identifier = "default"
    github_owner: str
    root_team: RootTeam = Field(default_factory=RootTeam)
    groups: list[Group] = Field(default_factory=list)
    admin: Admin
    leads: dict[Name, Identifier] = Field(default_factory=dict)
    agent_versions: list[str] = Field(min_length=1)
    timezone_from_series: Literal[True] = True

    @field_validator("github_owner")
    @classmethod
    def owner_shape(cls, value):
        if not re.fullmatch(r"[a-z0-9-]+", value or ""):
            raise ValueError("org.github_owner must match [a-z0-9-]+")
        return value

    @model_validator(mode="after")
    def group_children_disjoint(self):
        seen: dict[str, str] = {}
        for group in self.groups:
            for child in group.children:
                if child in seen:
                    raise ValueError(f"groups: team {child!r} listed under two groups")
                seen[child] = group.team_id
        return self


class Calibration(Strict):
    target_merged_prs_per_dev_week: Annotated[Number, Field(gt=0)] = 3.0


# --------------------------------------------------------------------------------------------
# repos[]
# --------------------------------------------------------------------------------------------

class Module(Strict):
    path: str
    weight: Ratio
    extensions: dict[str, float]

    @field_validator("path")
    @classmethod
    def path_shape(cls, value):
        if not value.endswith("/"):
            raise ValueError("repos[].modules[].path must end with '/'")
        return value

    @field_validator("extensions", mode="before")
    @classmethod
    def extension_weights(cls, value):
        if not isinstance(value, dict) or not value:
            raise ValueError("repos[].modules[].extensions must be a nonempty mapping")
        for key in value:
            if not isinstance(key, str) or not key.startswith("."):
                raise ValueError("extensions keys must start with '.'")
        return shares(value, "extensions")


class Repo(Strict):
    project: Name
    name: str
    default_branch: Name = "main"
    private: bool = True
    integration_mix: dict[str, float] = Field(
        default_factory=lambda: {"squash": 0.7, "merge": 0.2, "rebase": 0.1})
    modules: list[Module] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def name_shape(cls, value):
        if not re.fullmatch(r"[a-z0-9-]+", value or ""):
            raise ValueError("repos[].name must match [a-z0-9-]+")
        return value

    @field_validator("integration_mix", mode="before")
    @classmethod
    def integration_weights(cls, value):
        return shares(value, "integration_mix")

    @model_validator(mode="after")
    def module_weights_sum(self):
        total = sum(m.weight for m in self.modules)
        if abs(total - 1.0) > 1e-9:
            raise ValueError("repos[].modules[].weight must sum to 1")
        return self


# --------------------------------------------------------------------------------------------
# defaults: work / pr / review / habits / sessions
# --------------------------------------------------------------------------------------------

class WorkDefaults(Strict):
    unlinked_share: Ratio = 0.20
    parallel_items: Annotated[int, Field(ge=1, le=3)] = 1
    new_item_prob: Ratio = 0.25
    feature_size: dict[str, float] = Field(default_factory=lambda: {
        "1": .35, "2": .25, "3": .20, "4": .10, "5": .05, "6": .05})
    shared_feature_prob: Ratio = 0.15
    issue_link_mix: dict[str, float] = Field(default_factory=lambda: {
        "graphql": .50, "branch": .20, "title": .10, "none": .20})
    change_mix: dict[str, float] = Field(default_factory=lambda: {
        "feature": .45, "fix": .25, "refactor": .12, "test": .06, "docs": .06, "chore": .06})
    backlog_issues_per_repo_week: Annotated[Number, Field(ge=0)] = 1.5
    not_planned_share: Ratio = 0.2

    @field_validator("feature_size", mode="before")
    @classmethod
    def _feature_size(cls, value):
        checked = shares(value, "work.feature_size", ids=True)
        for key in checked:
            if key not in {str(n) for n in range(1, 9)}:
                raise ValueError("work.feature_size keys must be '1'..'8'")
        return checked

    @field_validator("issue_link_mix", mode="before")
    @classmethod
    def _issue_link_mix(cls, value):
        checked = shares(value, "work.issue_link_mix")
        if set(checked) - {"graphql", "branch", "title", "none"}:
            raise ValueError("work.issue_link_mix keys must be graphql/branch/title/none")
        return checked

    @field_validator("change_mix", mode="before")
    @classmethod
    def _change_mix(cls, value):
        checked = shares(value, "work.change_mix")
        if set(checked) - {"feature", "fix", "refactor", "test", "docs", "chore"}:
            raise ValueError("work.change_mix keys must be feature/fix/refactor/test/docs/chore")
        return checked


class PrDefaults(Strict):
    size_median_loc: Annotated[Number, Field(gt=0)] = 180
    size_sigma: Annotated[Number, Field(gt=0)] = 0.9
    size_min_loc: PositiveInt = 5
    size_max_loc: PositiveInt = 4000
    open_at_fraction: Annotated[Number, Field(gt=0, le=1)] = 0.7
    deletion_share: Ratio = 0.3
    label_prob: Ratio = 0.6
    commits_per_session: dict[str, float] = Field(default_factory=lambda: {
        "0": .3, "1": .4, "2": .2, "3": .1})
    comments_per_pr_mean: Annotated[Number, Field(ge=0)] = 2.0
    merge_delay_hours: MinMax = Field(default_factory=lambda: MinMax(min=0.2, max=8))

    @field_validator("commits_per_session", mode="before")
    @classmethod
    def _commits_per_session(cls, value):
        return shares(value, "pr.commits_per_session", ids=True)

    @model_validator(mode="after")
    def sizes_ordered(self):
        if self.size_min_loc >= self.size_max_loc:
            raise ValueError("pr.size_min_loc must be less than pr.size_max_loc")
        return self


class ReviewDefaults(Strict):
    first_review_median_hours: Annotated[Number, Field(gt=0)] = 4
    first_review_sigma: Annotated[Number, Field(gt=0)] = 0.8
    changes_requested_rate: Ratio = 0.30
    commented_review_prob: Ratio = 0.3
    abandon_rate: Ratio = 0.06
    reopen_rate: Ratio = 0.03
    revert_rate: Ratio = 0.02
    rework_ratio: MinMax = Field(default_factory=lambda: MinMax(min=0.02, max=0.25))


class HabitsDefaults(Strict):
    cache_bust_per_100_requests: Annotated[Number, Field(ge=0)] = 0.5
    resume_prob_per_turn: Ratio = 0.04
    interrupt_prob_per_turn: Ratio = 0.03
    compactions_per_100_requests: Annotated[Number, Field(ge=0)] = 0.8
    manual_compaction_share: Ratio = 0.3
    verified_prob: Ratio = 0.6
    plan_mode_prob: Ratio = 0.2


class SessionsDefaults(Strict):
    mean_input_tokens: PositiveInt = 1_200_000
    min_input_tokens: PositiveInt = 20_000
    max_per_day: PositiveInt = 8
    subagent_prob: Ratio = 0.25
    max_subagents: PositiveInt = 4
    subagent_share: MinMax = Field(default_factory=lambda: MinMax(min=0.03, max=0.15))
    mean_context_tokens: MinMax = Field(default_factory=lambda: MinMax(min=40_000, max=140_000))
    context_window_tokens: PositiveInt = 1_000_000
    turn_size_mean_requests: Annotated[Number, Field(gt=0)] = 8
    work_window_start_hours: MinMax = Field(default_factory=lambda: MinMax(min=8.5, max=10.5))
    work_window_length_hours: MinMax = Field(default_factory=lambda: MinMax(min=8, max=10))
    dominant_model_share: Ratio = 0.85
    ttl_s: PositiveInt = 3600


class Defaults(Strict):
    work: WorkDefaults = Field(default_factory=WorkDefaults)
    pr: PrDefaults = Field(default_factory=PrDefaults)
    review: ReviewDefaults = Field(default_factory=ReviewDefaults)
    habits: HabitsDefaults = Field(default_factory=HabitsDefaults)
    sessions: SessionsDefaults = Field(default_factory=SessionsDefaults)


# Team overrides carry the same block types (a field present in the team's YAML mapping
# replaces the default field WHOLE -- including a mixture -- never a per-key merge). Reusing
# the concrete `*Defaults` types (all fields already optional-with-default) directly as the
# override shape would silently fill in every unset field with ITS OWN default rather than the
# effective team default, so overrides instead carry only the fields the operator wrote.

class TeamOverride(Strict):
    tokens_per_loc_multiplier: Annotated[Number, Field(gt=0)] = 1.0
    user_multiplier_sigma: Annotated[Number, Field(ge=0)] = 0.35
    work: dict = Field(default_factory=dict)
    pr: dict = Field(default_factory=dict)
    review: dict = Field(default_factory=dict)
    habits: dict = Field(default_factory=dict)
    sessions: dict = Field(default_factory=dict)


class Onboarding(Strict):
    ramp_days: PositiveInt = 30
    initial_multiplier: Annotated[Number, Field(ge=1)] = 2.0


class BudgetsConfig(Strict):
    period: Literal["monthly"] = "monthly"
    org_usd: Annotated[Number, Field(ge=0)]
    teams: dict[Name, Annotated[Number, Field(ge=0)]] = Field(default_factory=dict)


class OrgConfig(Versioned):
    org: OrgOptions
    calibration: Calibration = Field(default_factory=Calibration)
    repos: list[Repo] = Field(min_length=1)
    defaults: Defaults = Field(default_factory=Defaults)
    teams: dict[Name, TeamOverride]
    onboarding: Onboarding = Field(default_factory=Onboarding)
    budgets: BudgetsConfig | None = None
    # No `allowances:` -- token allowances were removed in code-econ
    # (docs/plan/2026-09-26-cost-based-seat-utilization.md); a seat's allowance is its team budget
    # / members. OrgConfig is strict, so an old config still carrying the key fails validation
    # rather than silently generating nothing for it.

    @model_validator(mode="after")
    def repo_names_unique(self):
        names = [repo.name for repo in self.repos]
        if len(names) != len(set(names)):
            raise ValueError("repos[].name must be unique")
        projects = [repo.project for repo in self.repos]
        if len(projects) != len(set(projects)):
            raise ValueError("repos[].project must be unique; each series project maps to one repo")
        return self


def load_org_config(path: Path) -> OrgConfig:
    path = Path(path)
    try:
        raw = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueLoader)
        config = OrgConfig.model_validate(raw)
    except ConfigError:
        raise
    except ValidationError as exc:
        locations = [(".".join(map(str, error["loc"])) or "root") + ": " + error["msg"] for error in exc.errors()]
        raise ConfigError("invalid org configuration fields: " + ", ".join(locations)) from exc
    except Exception as exc:  # yaml.YAMLError, OSError, UnicodeError, TypeError, ValueError
        raise ConfigError(f"cannot read org configuration: {type(exc).__name__}: {exc}") from exc
    for field_name in ("series_dir", "pricing_dir"):
        value = getattr(config.org, field_name)
        if value is None:
            continue
        setattr(config.org, field_name, value.resolve() if value.is_absolute() else (path.resolve().parent / value).resolve())
    return config


# --------------------------------------------------------------------------------------------
# Cross-dataset validation and merge (plan §3.5)
# --------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class TeamEffective:
    work: WorkDefaults
    pr: PrDefaults
    review: ReviewDefaults
    habits: HabitsDefaults
    sessions: SessionsDefaults
    tokens_per_loc_multiplier: float
    user_multiplier_sigma: float


def _merge_block(default_block, override_fields: dict, block_type):
    if not override_fields:
        return default_block
    merged = default_block.model_dump()
    merged.update(override_fields)
    return block_type.model_validate(merged)


def _effective_team(defaults: Defaults, override: TeamOverride) -> TeamEffective:
    return TeamEffective(
        work=_merge_block(defaults.work, override.work, WorkDefaults),
        pr=_merge_block(defaults.pr, override.pr, PrDefaults),
        review=_merge_block(defaults.review, override.review, ReviewDefaults),
        habits=_merge_block(defaults.habits, override.habits, HabitsDefaults),
        sessions=_merge_block(defaults.sessions, override.sessions, SessionsDefaults),
        tokens_per_loc_multiplier=override.tokens_per_loc_multiplier,
        user_multiplier_sigma=override.user_multiplier_sigma,
    )


@dataclass(frozen=True)
class ResolvedOrg:
    config: OrgConfig
    series: "object"  # forecast.synthetic.config.SeriesConfig; typed loosely to avoid an import cycle
    org_id: str
    users: list[UserRecord]
    teams: list[TeamRecord]
    users_by_id: dict[str, UserRecord]
    teams_by_name: dict[str, TeamRecord]
    team_effective: dict[str, TeamEffective]
    project_to_repo: dict[str, Repo]
    repo_by_name: dict[str, Repo]
    leads: dict[str, str]  # team_name -> l1_user_id, only entries explicitly set in org.leads
    team_order: list[str]


def read_series_snapshot(series_dir: Path) -> dict:
    """Read `manifest.json` and the series' OWN snapshots (`inputs/users`, `inputs/roster`,
    `config.yaml`) from a completed series dataset directory -- never the originals. The single
    place `cli_synt_gen.py`'s `org-expand` path (and the test suite) reads these four things,
    so the loading logic that feeds `validate_org_inputs` exists exactly once."""
    import json as _json

    from forecast.artifact_io import read_records
    from ..config import load_series_config

    series_dir = Path(series_dir)
    manifest = _json.loads((series_dir / "manifest.json").read_text(encoding="utf-8"))
    users = read_records(series_dir / "inputs" / "users" / "users.jsonl", UserRecord)
    teams = read_records(series_dir / "inputs" / "roster" / "teams.jsonl", TeamRecord)
    series_cfg = load_series_config(series_dir / "config.yaml")
    return {"manifest": manifest, "users": users, "teams": teams, "series_cfg": series_cfg}


def validate_org_inputs(config: OrgConfig, series_manifest: Mapping, users: list[UserRecord],
                        teams: list[TeamRecord], series_cfg, pricing) -> ResolvedOrg:
    """Cross-file checks (plan §3.5). Raises `ConfigError` naming the offending field."""
    version = series_manifest.get("schema_version")
    if type(version) is int and version == 1:
        raise ConfigError("org.series_dir: dataset uses schema 1; regenerate with this version")
    if type(version) is not int or version != 2:
        raise ConfigError(f"org.series_dir: manifest.schema_version must be 2, got {version!r}")
    if series_manifest.get("operation") != "series":
        raise ConfigError("org.series_dir: manifest.operation must be 'series'")
    if series_manifest.get("status") != "complete":
        raise ConfigError("org.series_dir: manifest.status must be 'complete'")

    project_names = {p.name for p in series_cfg.projects}
    repo_projects = {repo.project for repo in config.repos}
    if repo_projects != project_names:
        missing_repo = project_names - repo_projects
        missing_project = repo_projects - project_names
        raise ConfigError(
            "repos[].project must map 1:1 to series projects; "
            f"projects without a repo: {sorted(missing_repo)}; "
            f"repos without a series project: {sorted(missing_project)}")

    team_names = {team.team_name for team in teams}
    config_team_names = set(config.teams)
    if team_names != config_team_names:
        raise ConfigError(
            "teams: every rostered team needs a teams.<name> entry, and no extra names; "
            f"missing: {sorted(team_names - config_team_names)}; "
            f"extra: {sorted(config_team_names - team_names)}")

    users_by_id = {user.user_id: user for user in users}
    teams_by_name = {team.team_name: team for team in teams}
    for team_name, l1_user_id in config.org.leads.items():
        if team_name not in teams_by_name:
            raise ConfigError(f"org.leads: unknown team {team_name!r}")
        if l1_user_id not in teams_by_name[team_name].user_ids:
            raise ConfigError(f"org.leads.{team_name}: {l1_user_id!r} is not a member of that team")

    group_children = {child for group in config.org.groups for child in group.children}
    unknown_children = group_children - team_names
    if unknown_children:
        raise ConfigError(f"org.groups[].children: unknown teams {sorted(unknown_children)}")

    if any(user.github_login is None for user in users):
        raise ConfigError(
            "every user in the series' snapshotted users file must have a github_login; "
            "set users.identity_style: consistent in the series' user config")

    logins = [user.github_login for user in users]
    if config.org.admin.github_login in logins:
        raise ConfigError(f"org.admin.github_login {config.org.admin.github_login!r} collides with a rostered user")

    agents = getattr(series_cfg, "agents", {})
    for agent_key, agent in agents.items():
        if agent_key != "claude_code":
            raise ConfigError(
                f"series agents.{agent_key}: only 'claude_code' is in scope (D1); "
                "codex/other agents are not supported")
        end_date = series_cfg.series.start_date + timedelta(days=series_cfg.series.days - 1)
        for model in agent.models:
            for at in (series_cfg.series.start_date, end_date):
                if pricing.price_tokens(model, {"input": 1}, at) is None:
                    raise ConfigError(f"series agents.claude_code.models.{model}: unpriced on {at}")

    team_effective = {name: _effective_team(config.defaults, config.teams[name]) for name in config_team_names}
    project_to_repo = {repo.project: repo for repo in config.repos}
    repo_by_name = {repo.name: repo for repo in config.repos}
    org_id = users[0].org_id if users else ""

    return ResolvedOrg(
        config=config, series=series_cfg, org_id=org_id, users=users, teams=teams,
        users_by_id=users_by_id, teams_by_name=teams_by_name, team_effective=team_effective,
        project_to_repo=project_to_repo, repo_by_name=repo_by_name,
        leads=dict(config.org.leads), team_order=sorted(config_team_names))
