"""Manager-surface response models (Tracks A/D/E/F1) -- the source of truth (§1/F0). Grows
one view model per endpoint as its track lands; see `README.md`'s porting-status table."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .envelope import Envelope, Money


class BudgetResponse(BaseModel):
    """`GET/PUT /v1/budgets/{scope_type}/{scope_id}` (F1). Not envelope-wrapped -- a budget
    row carries no datasource mode. `amount_usd` is a decimal string (never a float, same
    rule as `PricingModel`); `None` means no budget set for this scope."""

    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_id: str
    period: str
    amount_usd: str | None = None


class BudgetWriteRequest(BaseModel):
    """`extra="forbid"`: a client still sending the removed `limit_tokens` gets a 422, not a
    silently-dropped field (token allowances were removed, cost-based seats plan D3)."""

    model_config = ConfigDict(extra="forbid")

    amount_usd: str


CycleKind = Literal["month", "quarter", "half", "year", "sprint", "custom"]
CycleRecurrence = Literal["recurring", "one-time"]


class BudgetCycleResponse(BaseModel):
    """`GET /v1/budget-cycle` (Overview refresh A2) -- the org's budget cadence. Not
    envelope-wrapped (`BudgetResponse`'s precedent). `period_label` is what budgets are stored
    under (`monthly`, `quarterly`, `half-yearly`, `yearly`, `sprint`, `custom`); every budget
    amount is "per period of this cycle". With no saved cycle this is the default recurring
    calendar month (`updated_at` null). Fields a kind does not use are null: `sprint` uses
    `sprint_weeks`, `anchor_date` (the start of the sprint numbered `anchor_number`); `custom`
    uses `anchor_date` (first day) and `custom_end` (last day, inclusive)."""

    model_config = ConfigDict(extra="forbid")

    kind: CycleKind
    recurrence: CycleRecurrence
    sprint_weeks: int | None = None
    anchor_date: date | None = None
    anchor_number: int | None = None
    custom_end: date | None = None
    period_label: str
    updated_at: datetime | None = None


class BudgetCycleWriteRequest(BaseModel):
    """`PUT /v1/budget-cycle` -- a full replace, not a patch: fields the kind does not use are
    dropped. `one-time` applies to `sprint` and `custom` only (calendar kinds are always
    recurring); `sprint` and `custom` need `anchor_date`; `sprint_weeks` is 1..52 (default 2),
    `anchor_number` defaults to 1; `custom` needs `custom_end` on or after `anchor_date`. Dates
    are bounded to 2000..2100. Rule violations are a 400 with the reason."""

    model_config = ConfigDict(extra="forbid")

    kind: CycleKind
    recurrence: CycleRecurrence = "recurring"
    sprint_weeks: int | None = None
    anchor_date: date | None = None
    anchor_number: int | None = None
    custom_end: date | None = None


class BudgetCyclePutResponse(BudgetCycleResponse):
    """`PUT /v1/budget-cycle` -- the saved cycle plus what the save did to budget amounts, which
    are never converted. A kind change (`label_changed`) leaves the amounts under
    `previous_period_label` stored but unread: scopes show no budget until amounts are entered
    for the new cadence. A same-kind edit (sprint length, anchor, custom dates) keeps the label,
    so existing amounts now mean "per period" of the changed cycle. `note` says which, in words
    the UI can show as is; null when nothing changed."""

    previous_period_label: str
    label_changed: bool
    note: str | None = None


class BudgetPeriod(BaseModel):
    """One budget period of the org's cycle, with exact UTC instants -- the server is the only
    place calendar or sprint arithmetic happens. `from` is the period's first instant (00:00Z)
    and `end_exclusive` its end; `to` is what to ask the other endpoints for: `end_exclusive`
    for a finished or upcoming period, and `min(now, end_exclusive)` for the current one, so a
    window `[from, to)` over the current period is its period-to-date. `days_elapsed` counts
    today (0 before the period starts, `days_total` once it is over)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    key: str
    label: str
    from_: datetime = Field(alias="from")
    to: datetime
    end_exclusive: datetime
    is_current: bool
    days_elapsed: int
    days_total: int


class BudgetPeriodsResponse(BaseModel):
    """`GET /v1/budget-cycle/periods?history=6` -- the last `history` (1..24, default 6) periods
    that have started, oldest first with the current one last, plus the next one. Empty `periods`
    when the cycle has not started yet (a sprint before its anchor, a one-time cycle in the
    future); `next` is null once a one-time cycle is over. Readable by any resolvable viewer:
    every screen's timeframe needs it, and it carries dates and labels only."""

    model_config = ConfigDict(extra="forbid")

    period_label: str
    periods: list[BudgetPeriod]
    next: BudgetPeriod | None = None


class ScopeEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str


class ScopesResponse(BaseModel):
    """`GET /v1/scopes` (U7-econ-picker) -- the scope picker's full, untruncated,
    timeframe-independent enumeration of the viewer's visible teams/projects. Not
    envelope-wrapped -- no datasource mode, no aggregates (`BudgetResponse`'s own precedent)."""

    model_config = ConfigDict(extra="forbid")

    teams: list[ScopeEntry]
    projects: list[ScopeEntry]


# ------------------------------------------------------------------------------- A1: org overview

class EntityRef(BaseModel):
    """A typed ref the client routes from (plan section 1 / U2's `entityRoute`) -- never a
    `screen`/`screenParam` on the wire."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: str | None = None


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    text: str
    action: str
    severity: str
    entity: EntityRef


class TeamSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    team_id: str
    name: str
    members: int
    spend: Money
    budget_pct: float | None = None
    merged_prs: int
    cost_per_pr: Money
    rework_rate: float | None = None
    over_budget: bool


class OrgHeadline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spend: Money
    budget_pct: float | None = None
    vs_last_pct: float | None = None
    could_save: Money
    dominant_cause: str | None = None


class OrgHighlights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active_teams: int
    active_devs: int
    tokens: int
    active_repos: int
    merged_prs: int
    cost_per_pr: Money
    teams_over_budget: int
    rework_rate: float | None = None
    avg_time_to_pr_days: float | None = None
    #: QW3 (mvp-gap-closure-plan): org-wide token share by model, `ModelUsage` (defined below
    #: with the PR schemas it was first ported for) -- a forward ref, resolved once the whole
    #: module has loaded (`from __future__ import annotations` at the top of this file).
    model_mix: list["ModelUsage"] | None = None
    #: F5 (mvp-gap-closure-plan): same `aggregate.reasoning_share` OrgUsage already carries --
    #: `ReasoningShare` is defined below with the rest of A2, hence the forward ref.
    reasoning_share: "ReasoningShare | None" = None


class AgentSpend(BaseModel):
    """One agent's slice of the spend donut. Resolved through the precedence cost chain per
    agent, so the slices sum to `OrgHeadline.spend` exactly; `spend.cost_basis` is that agent's
    own contributing sources."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    spend: Money


class AgentTaskMix(BaseModel):
    """Root sessions of one agent whose dominant session type is `type` (the raw label, e.g.
    `IMPLEMENT`; the client names and colours it). Counted by session, the same rule as
    `SessionTypeCount`, so summing `count` per `type` reproduces `OrgUsage.session_types` for the
    same window. Sessions with no session type yet are omitted, never bucketed as "unknown"."""

    model_config = ConfigDict(extra="forbid")

    agent: str
    type: str
    count: int


class DailySpendPoint(BaseModel):
    """One UTC calendar day of spend. `spend_usd_micros` is the precedence-chain cost of every
    session bucketed on that day -- the same chain as `OrgHeadline.spend`, so the series sums to
    it exactly. Plain integer micros (not `Money`): one basis is carried by the headline."""

    model_config = ConfigDict(extra="forbid")

    day: date
    spend_usd_micros: int


class OrgOverview(BaseModel):
    """`GET /v1/org/overview` (A1) -- returned as `Envelope[OrgOverview]`.

    One cost source: `headline.spend`, every `agent_spend[].spend` and `daily_spend` are all
    resolved through the same precedence cost chain, per session, so `sum(agent_spend) ==
    sum(daily_spend) == headline.spend` exactly. (Overview refresh Track B; `initiatives_trend`
    and `monthly_spend` were removed in favour of `daily_spend`.)"""

    model_config = ConfigDict(extra="forbid")

    headline: OrgHeadline
    highlights: OrgHighlights
    teams: list[TeamSummary]
    #: Per agent on the precedence chain (see the class docstring); largest first.
    agent_spend: list[AgentSpend]
    #: Root sessions per (agent, dominant session type), agent asc / count desc / type asc.
    agent_task_mix: list[AgentTaskMix]
    #: The window's elapsed UTC days only (frm's day .. min(to, now), today included), missing
    #: days 0. A session counts on the day of `max(started_at, frm)`. Sums to `headline.spend`.
    daily_spend: list[DailySpendPoint]
    worth_a_look: list[Finding]


OrgOverviewEnvelope = Envelope[OrgOverview]


# ------------------------------------------------------------------------------- A2: org usage

class ReasoningShare(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reasoning_tokens: int
    tokens_total: int
    pct: float | None = None


class UsageHeader(BaseModel):
    model_config = ConfigDict(extra="forbid")

    users: int
    agents: int
    hours: float
    sessions: int
    avg_session_minutes: float | None = None
    avg_prs_per_session: float | None = None


class SessionTypeCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: str
    count: int


class TeamUsage(BaseModel):
    """A2 per-team utilization, cost-based (cost-based seats plan §2): `spend` over `allowance`
    (the team's per-period budget measured against the window: the full budget for a whole
    period or a period-to-date, prorated by days otherwise). `tokens_used` is informational only."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    name: str
    members: int
    spend: Money
    tokens_used: int
    allowance: Money
    utilization_pct: float | None = None
    status: str | None = None
    seats_under_threshold: int | None = None


class PersonUsage(BaseModel):
    """G3 (mvp-gap-closure-plan): one person's spend vs. their prorated seat allowance --
    `TeamUsage`'s own shape, one level down. `status` is `over`/`healthy`/`under`, or `new`
    for a seat inside its grace period (never flagged under)."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    spend: Money
    tokens_used: int
    allowance: Money
    utilization_pct: float | None = None
    status: str | None = None
    in_grace: bool = False


class SpotlightTeamUsage(BaseModel):
    """G3: the org's highest-utilization team, expanded to its own roster -- the mockup's
    "who's using their seat in <team>" section, dropped in Phase 1 because A2 returned no
    per-person data at all (`src/webui/AGENTS.md`'s own U5 log). `TeamScreen` (A4) shows any
    team's full roster on its own drill-down page, but that costs a click; this surfaces the
    one team actually worth looking at directly on the org-wide Usage screen. `None` when no
    team is over its window budget -- there is no natural team to spotlight, never a guess."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    team_name: str
    people: list[PersonUsage]


class OrgUsage(BaseModel):
    """`GET /v1/org/usage` (A2) -- returned as `Envelope[OrgUsage]`."""

    model_config = ConfigDict(extra="forbid")

    header: UsageHeader
    session_types: list[SessionTypeCount]
    sessions_no_output_pct: float | None = None
    reasoning_share: ReasoningShare | None = None
    teams: list[TeamUsage]
    spotlight_team: SpotlightTeamUsage | None = None


OrgUsageEnvelope = Envelope[OrgUsage]


# ------------------------------------------------------------------------------- A3: tables

class TeamTableRow(BaseModel):
    """One row of `GET /v1/teams` (A3) -- name / spend / vs budget / people / PRs / $/PR /
    avgTimeToPR / rework / lastActive, in one place (iteration-plan C). `budget_pct` is spend
    over the window's budget (full period budget for a whole period or period-to-date, prorated
    by days for a rolling window), which is also the team's utilization -- there is no second,
    separately-computed utilization column (cost-based seats plan §3.4)."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    name: str
    members: int
    spend: Money
    budget_pct: float | None = None
    merged_prs: int
    cost_per_pr: Money
    avg_time_to_pr_days: float | None = None
    rework_rate: float | None = None
    over_budget: bool
    last_active_at: str | None = None


class ProjectTableRow(BaseModel):
    """One row of `GET /v1/projects` (A3) -- one canonical repo (REPO section 1)."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str
    local_to_user: str | None = None
    name: str
    spend: Money
    merged_prs: int
    contributors: int
    avg_time_to_pr_days: float | None = None
    rework_rate: float | None = None
    last_active_at: str | None = None


class TeamsPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[TeamTableRow]
    total: int


class ProjectsPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProjectTableRow]
    total: int


TeamsPageEnvelope = Envelope[TeamsPage]
ProjectsPageEnvelope = Envelope[ProjectsPage]


# ------------------------------------------------------------------------------- A4: team detail

class PersonRanked(BaseModel):
    """One row of A4's ranked `Person[]`, never a stack-rank on raw spend (EFF section 5.5).
    `status` is the headline verdict: `room-to-save` (recoverable share + scaled floor,
    cost-based seats plan D6), else the seat band `over` / `under` / `new`, else `on-track`.
    `usage_status` is the seat band alone (`over`/`healthy`/`under`/`new`, `None` with no
    allowance) -- `PersonUsage.status`'s vocabulary. `usage_pct` is spend over `seat_allowance`
    (team period budget / members, measured against the window and prorated from the seat's start); `recoverable_pct` is shown so a flag
    is explainable."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    spend: Money
    merged_prs: int
    cost_per_pr: Money
    recoverable: Money
    recoverable_pct: float | None = None
    status: str
    usage_status: str | None = None
    usage_pct: float | None = None
    low_use: bool
    seat_allowance: Money
    in_grace: bool = False


class TeamHighlights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    members: int
    room_to_save_count: int
    low_use_count: int
    merged_prs: int
    cost_per_pr: Money
    rework_rate: float | None = None
    avg_time_to_pr_days: float | None = None


class TeamDetail(BaseModel):
    """`GET /v1/teams/{id}` (A4) -- returned as `Envelope[TeamDetail]`."""

    model_config = ConfigDict(extra="forbid")

    team: TeamSummary
    people: list[PersonRanked]
    highlights: TeamHighlights
    worth_a_look: list[Finding]


TeamDetailEnvelope = Envelope[TeamDetail]


# ------------------------------------------------------------------------------- A5: person detail

class Habit(BaseModel):
    """X3's per-habit breakdown -- `category` in {resume, cache_bust, interrupted} (efficiency
    note §2); `label` is lexicon-safe (never "waste")."""

    model_config = ConfigDict(extra="forbid")

    category: str
    label: str
    recoverable_usd_micros: int


class WorkQualityCount(BaseModel):
    """The Clean/Heavy lift/Quick & dirty/Costly rework 2x2 (X2, Phase 2). Contract defined
    now so the API shape doesn't change when X2 lands; `Person.work_quality` is `None`
    (cold-start) until then."""

    model_config = ConfigDict(extra="forbid")

    label: str
    count: int


class RecentPR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    repo_id: str
    state: str
    title: str | None = None
    created_at: str
    merged_at: str | None = None
    closed_at: str | None = None


class RecentSession(BaseModel):
    """G1 (mvp-gap-closure-plan): one of a person's most recent sessions -- `quality` is the
    label of whichever PR the session is linked to (X2's `quality_label`, over `merged_prs`
    linked PRs), `None` when the session has no PR link yet or the link isn't classifiable
    (never guessed). `duration_minutes` not a pre-formatted string -- strip-rule 2, client
    formats via `fmtDuration`. `title` is `session_info.info.title` -- see `PRSessionRef`'s
    docstring for where it actually comes from (`None` only when the vendor never auto-titled
    this particular session, not a missing capability)."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str | None = None
    repo_id: str | None = None
    started_at: str | None = None
    duration_minutes: float | None = None
    cost_usd_micros: int | None = None
    merged_prs: int
    quality: str | None = None
    #: F5 (mvp-gap-closure-plan): this session's own `info.reasoning_tokens` + `tokens_total` --
    #: raw, not a pre-formatted percentage (strip-rule 2, same as `duration_minutes` above).
    #: `reasoning_tokens` is `None` only for a session folded before the rollup shipped
    #: (`aggregate.reasoning_share`'s own docstring); `tokens_total` is always a real count.
    reasoning_tokens: int | None = None
    tokens_total: int | None = None


class VelocityPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    week: str
    merged_prs: int


class PeerCompare(BaseModel):
    """A5-peer-task-mix: the cheapest teammate by $/KLOC (spend ÷ lines changed, replacing
    $/PR -- a PR is not a unit of volume). Carries the peer's own figure for display, not
    just an id, and `cost_per_kloc_usd_micros` is the per-1,000-line, not per-line, price."""

    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    cost_per_kloc_usd_micros: int


class Person(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    team_id: str | None = None
    spend: Money
    merged_prs: int
    cost_per_pr: Money
    recoverable: Money
    habits: list[Habit]
    work_quality: list[WorkQualityCount] | None = None
    peer_compare: PeerCompare | None = None
    #: G2 (mvp-gap-closure-plan): "where the spend went" by channel, over every session in
    #: scope -- `aggregate.channel_totals`, the same rollup D2's `PRCost.channels` already
    #: uses, generalized here to a person's own sessions rather than one PR's linked ones.
    #: `ChannelTotals` (defined below with D2) -- a forward ref, resolved once this module
    #: has fully loaded (`from __future__ import annotations`, top of file).
    channels: ChannelTotals | None = None


class PersonDetail(BaseModel):
    """`GET /v1/people/{id}` (A5) -- returned as `Envelope[PersonDetail]`. Lead-scoped (F2):
    per-person cost is visible only to that person's team lead/admin, never a stack-rank
    (EFF section 5.5)."""

    model_config = ConfigDict(extra="forbid")

    person: Person
    recent_prs: list[RecentPR]
    recent_sessions: list[RecentSession]
    velocity: list[VelocityPoint]


PersonDetailEnvelope = Envelope[PersonDetail]


# ------------------------------------------------------------------------------- A6: project detail

class ProjectContributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str
    spend: Money
    merged_prs: int


class ProjectSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_id: str
    name: str
    spend: Money
    merged_prs: int
    cost_per_pr: Money
    rework_rate: float | None = None


class ProjectHighlights(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lines_added: int
    lines_removed: int
    #: PRs in this repo reopened at least once, from the `git_artifact_events` transition log
    #: (Track churn/reopen). None -- not 0 -- for a repo whose PRs were all ingested before that
    #: log existed (reopen history unknown until backfill 4a); a real count otherwise.
    reopened_prs: int | None = None


class ProjectPRRef(BaseModel):
    """G4 (mvp-gap-closure-plan): one PR touching this project, any state -- the mockup's
    flat "All pull requests" list (`project.features.flatMap(f => f.prs)`), rebuilt directly
    off `git_artifacts` rather than waiting on F4's feature-boundary grouping (`features`
    above stays the cold-start path; this one doesn't need it). `cost_usd_micros` sums
    whatever priced sessions are linked via `session_git_links` (`aggregate.pr_costs_for_ids`'s
    query) -- `None` only when no session has ever linked to this PR at all."""

    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    number: int | None = None
    title: str | None = None
    author_name: str | None = None
    state: str
    cost_usd_micros: int | None = None
    created_at: str | None = None
    merged_at: str | None = None
    closed_at: str | None = None


class Feature(BaseModel):
    """A project's feature list (A6, gated on F4 -- Phase 2)."""

    model_config = ConfigDict(extra="forbid")

    #: The routable id D3 (`GET /v1/features/{id}`) looks up by -- distinct from `issue_key`
    #: (a display label, not unique across repos on its own).
    id: str
    issue_key: str
    title: str
    spend: Money
    merged_prs: int
    status: str


class ProjectDetail(BaseModel):
    """`GET /v1/projects/{id}` (A6) -- returned as `Envelope[ProjectDetail]`. `features` is
    `None` (cold-start) in Phase 1: gated on F4's resolved issue boundary (Phase 2), not yet
    built even though Track H's issue data already exists."""

    model_config = ConfigDict(extra="forbid")

    project: ProjectSummary
    contributors: list[ProjectContributor]
    highlights: ProjectHighlights
    features: list[Feature] | None = None
    prs: list[ProjectPRRef] = []


ProjectDetailEnvelope = Envelope[ProjectDetail]


# ------------------------------------------------------------------------------- D2: PR detail

class PRSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    number: int | None = None
    repo_id: str
    title: str | None = None
    state: str
    author_user_id: str | None = None
    author_name: str | None = None
    branch: str | None = None
    base_branch: str | None = None
    labels: list[str] = []
    linked_issue: str | None = None
    created_at: str | None = None
    merged_at: str | None = None
    closed_at: str | None = None
    lines_added: int | None = None
    lines_removed: int | None = None
    files_changed: int | None = None
    commits: int | None = None


class ChannelTotals(BaseModel):
    """`aggregate.channel_totals` (D2) -- usd_micros summed across a set of sessions (a PR's
    linked ones, or -- G2, mvp-gap-closure-plan -- a person's own sessions in scope), plus
    whether every one of them had both channel rollups landed (`complete`)."""

    model_config = ConfigDict(extra="forbid")

    carry_usd_micros: int
    recache_usd_micros: int
    output_usd_micros: int
    input_usd_micros: int
    complete: bool


class PRCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spend: Money
    pre_open: Money | None = None
    post_open: Money | None = None
    channels: ChannelTotals | None = None


class PRReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reviewer_user_id: str | None = None
    reviewer_name: str | None = None
    state: str
    submitted_at: str | None = None


class PRReviewSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_rounds: int
    time_to_first_review_hours: float | None = None
    time_to_merge_days: float | None = None
    comment_count: int | None = None
    reviews: list[PRReview]


class PRSessionRef(BaseModel):
    """One session that fed this PR. `title` is `session_info.info.title` -- the causal graph
    fold already lifts the vendor's own `ai_title` (`session_start.ai_title`, backfilled by
    the normalizer once Claude Code auto-titles the session) into `graph.meta["title"]`
    (`builder.py`) and from there into `info["title"]` (`graph_fold.py::info_of`); this was
    simply never read on the manager side -- `None` only when the session never got an
    ai-title at all (short sessions, or a session the vendor never auto-titled), never a
    missing capability."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    title: str | None = None
    user_id: str | None = None
    cost_usd_micros: int | None = None
    role: str


class PRFlag(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    detail: str
    sev: str


class ModelUsage(BaseModel):
    """One model's share of tokens across a session (or a PR's linked sessions) --
    `aggregate.model_usage_breakdown`. `tier` is a category (`high`/`mid`/`low`, from
    `aggregate.model_tier`), never a colour -- `schemas/README.md` strip-rule 1, same
    treatment `channels`/`workQuality` already get. `cost_usd_micros` is populated only when
    an OTel ledger priced this model specifically; a JSONL-only fold has no per-model price
    (only the session-wide total), so it stays `None` there rather than guessing."""

    model_config = ConfigDict(extra="forbid")

    model: str
    tokens: int
    pct: float
    tier: str
    cost_usd_micros: int | None = None


class PRDetail(BaseModel):
    """`GET /v1/prs/{id}` (D2) -- returned as `Envelope[PRDetail]`. `reopens` is the reopen count
    from the `git_artifact_events` transition log; `churn_post_lines`/`rework_ratio` come from
    the commit line stats the git_commit_stats worker fills (Track churn/reopen, Landing 2).
    Each is `None` -- not 0 -- when its source data wasn't captured for this PR yet (a PR/push
    predating the feature, until the backfills run), never a guessed value. `churn_post_usd_micros`
    stays `None` on purpose: commit-line churn has no honest lines->$ conversion (see the
    service comment); the dollar cost of rework is the composite question (Landing 3)."""

    model_config = ConfigDict(extra="forbid")

    pr: PRSummary
    cost: PRCost
    review: PRReviewSummary
    quality: str | None = None
    #: Lines the PR churned in pushes after it opened (X1-churn); None when uncaptured.
    churn_post_lines: int | None = None
    #: `churn_post_lines` over the PR's final size (additions+deletions); None when uncaptured.
    rework_ratio: float | None = None
    churn_post_usd_micros: int | None = None
    reopens: int | None = None
    sessions: list[PRSessionRef]
    flags: list[PRFlag]
    models: list[ModelUsage] | None = None


PRDetailEnvelope = Envelope[PRDetail]


# ------------------------------------------------------------------------------- D3: feature detail

class FeatureMemberPR(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    number: int | None = None
    title: str | None = None
    state: str
    created_at: str | None = None
    merged_at: str | None = None
    cost: Money


class FeatureContributor(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: str
    name: str


class FeatureLifecycle(BaseModel):
    """Raw timestamps only (schemas/README.md's own strip-rule -- no pre-formatted spans or
    x-positions here; that's U2's presentation layer, same posture D1/D2 already took)."""

    model_config = ConfigDict(extra="forbid")

    first_session_started_at: str | None = None
    first_pr_opened_at: str | None = None
    last_merged_at: str | None = None


class FeatureCostToFinish(BaseModel):
    """`forecast.feature_cost_to_finish` (X5)."""

    model_config = ConfigDict(extra="forbid")

    open_prs: int
    low_usd_micros: int
    mid_usd_micros: int
    high_usd_micros: int
    basis: str


class Dist(BaseModel):
    """`forecast.dist_stats` -- a coarser percentile cut (p50/p70/p95) than `CostEnvelope`'s,
    matching the mockup's own `Dist` shape. Shared by E1's cost/time stats and D3's benchmark;
    defined here (ahead of `FeatureDetail`) because both reference it."""

    model_config = ConfigDict(extra="forbid")

    avg: float
    min: float
    p50: float
    p70: float
    p95: float
    max: float


class FeatureBenchmark(BaseModel):
    """`aggregate.feature_benchmark` (D3-benchmark) -- where this feature's spend sits among its
    repo peers. Cost axis only for this first cut (`cost_dist` is unitless whole dollars, the
    `Dist` contract; the UI is told `unit="$"`); a lead-time axis is TODO(D3-benchmark-time).
    `cost_percentile` is 0-100, the share of priced peer features this one costs more than."""

    model_config = ConfigDict(extra="forbid")

    subject_cost_usd_micros: int
    cost_percentile: int
    peer_count: int
    cost_dist: Dist


class FeatureDetail(BaseModel):
    """`GET /v1/features/{id}` (D3) -- returned as `Envelope[FeatureDetail]`. `id` is the
    routable key `A6`'s `features[]` now hands out (`quality._feature_id`), not the display
    `issue_key`. `benchmark` (cost percentile vs repo peer features, D3-benchmark) stays
    cold-start `None` until the repo has enough priced peers. `cost_to_finish` is `None` only
    when there is no PR-cost history at all to base an envelope on
    (`forecast.cost_per_pr_envelope`'s own cold-start); zero open PRs is a real zero (nothing
    left to finish), not an absence."""

    model_config = ConfigDict(extra="forbid")

    id: str
    issue_key: str
    title: str
    status: str
    repo_id: str | None = None
    spend: Money
    channels: ChannelTotals | None = None
    member_prs: list[FeatureMemberPR]
    contributors: list[FeatureContributor]
    lifecycle: FeatureLifecycle
    cost_to_finish: FeatureCostToFinish | None = None
    benchmark: FeatureBenchmark | None = None


FeatureDetailEnvelope = Envelope[FeatureDetail]


# ------------------------------------------------------------------------------- E1: economics

# `Dist` (shared by E1 and D3's benchmark) is defined above `FeatureDetail`.


class CostEnvelope(BaseModel):
    """`forecast.cost_per_pr_envelope` (X5)."""

    model_config = ConfigDict(extra="forbid")

    min_usd_micros: int
    p25_usd_micros: int
    median_usd_micros: int
    p75_usd_micros: int
    p90_usd_micros: int
    p99_usd_micros: int
    max_usd_micros: int


class ForecastConePoint(BaseModel):
    """`forecast.forecast_cone` (X5) -- one step of the widening band anchored on
    `CostEnvelope`; step 0 is the handoff point itself."""

    model_config = ConfigDict(extra="forbid")

    step: int
    median_usd_micros: int
    band_lo_usd_micros: int
    band_hi_usd_micros: int


class BudgetBurnPoint(BaseModel):
    """`forecast.budget_burnup` (X5) -- the handoff day carries both fields, equal."""

    model_config = ConfigDict(extra="forbid")

    day: int
    actual_usd_micros: int | None = None
    projected_usd_micros: int | None = None


class Economics(BaseModel):
    """`GET /v1/economics?scope_type=&scope_id=` (E1) -- returned as `Envelope[Economics]`.
    `scope_type`/`scope_id` echo the request rather than a single composite `scope=` string
    (the same two-part shape F1's budgets routes already use). `budget_burn` runs over the
    calendar month that contains the window's end -- it forecasts to that month's last day, not
    the requested display window's end -- under every budget cycle (it carries spend only, no
    budget amount; TODO(A4): per-cycle burn-up). `None`
    (cold-start) when no session activity has been reported in the period yet."""

    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_id: str
    cost_dist: Dist | None = None
    time_to_merge_dist: Dist | None = None
    pr_cost_envelope: CostEnvelope | None = None
    forecast_cone: list[ForecastConePoint]
    merged_prs: int
    reverted_prs: int
    merged_pr_cost: Money
    reverted_pr_cost: Money
    rework_ratio: float | None = None
    budget_burn: list[BudgetBurnPoint] | None = None


EconomicsEnvelope = Envelope[Economics]


# ------------------------------------------------------------------- T6: budget forecast + plan

# All money here is a plain integer `*_usd_micros` (like `BudgetBurnPoint`): these are model
# outputs read from a precomputed run, and the actuals are `cost_effective_usd_micros` summed
# over the scope's sessions (the `/v1/economics` `budget_burn` path), not a precedence-resolved
# aggregate, so `Money`'s cost basis does not apply. No colours, prose or formatted strings.


class ForecastPreset(BaseModel):
    """A scenario preset of the run (`baseline` first). `group` is `None` for baseline;
    `assumptions` are the cost module's machine codes, which the client words."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    group: str | None = None
    description: str
    assumptions: list[str]


class ActualDailyPoint(BaseModel):
    """One local calendar day of actual spend (zero-filled: a day without sessions is 0)."""

    model_config = ConfigDict(extra="forbid")

    date: date
    usd_micros: int


class ForecastDailyPoint(BaseModel):
    """One forecast day: the mean and the band's low / high percentiles (`band_low` /
    `band_high`) of the scope's daily cost."""

    model_config = ConfigDict(extra="forbid")

    date: date
    expected_usd_micros: int
    low_usd_micros: int
    high_usd_micros: int


class ForecastLanding(BaseModel):
    """Where the period ends: actual to date plus the forecast remainder (mean and the
    10th / 50th / 90th percentiles)."""

    model_config = ConfigDict(extra="forbid")

    expected_usd_micros: int
    p10_usd_micros: int
    p50_usd_micros: int
    p90_usd_micros: int


class RunsOutOn(BaseModel):
    """The date the budget runs out, given that it does, at the 10th / 50th / 90th percentile of
    that date. All three are `None` when the chance of running out is below the configured
    minimum."""

    model_config = ConfigDict(extra="forbid")

    p10: date | None = None
    p50: date | None = None
    p90: date | None = None


class Runway(BaseModel):
    """Will the budget last the period (plan §3.8)? Only for the current period of a
    scope with a budget. `runs_out_probability` + the period's `within_budget_probability` is
    exactly 1. `exhausted`: the budget was already spent by `data_through` (then `runs_out_on`
    is the date actual spend crossed it). `days_short` = period end minus `runs_out_on.p50`."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["lasts", "at_risk", "runs_out", "exhausted"]
    runs_out_probability: float
    runs_out_on: RunsOutOn
    days_short: int | None = None


class ForecastPeriod(BaseModel):
    """One calendar period of the run (a month). `forecast_from` is the first forecast day: the
    day after `data_through` in the current period (`forecast_from` > `start`), else `start`;
    in the month-end case both periods are future (`forecast_from` == `start`, no actuals,
    `runway` `None`). `actual_to_date_usd_micros` is the actual spend on `start`..`forecast_from`
    minus one day. `projected` runs `forecast_from`..`end`: actual to date plus the cumulative
    forecast (mean and band). `within_budget_probability` is `None` without a budget.
    `delta_vs_baseline_usd_micros` is this landing minus the baseline preset's (0 for baseline)."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    forecast_from: date
    actual_to_date_usd_micros: int
    projected: list[ForecastDailyPoint]
    landing: ForecastLanding
    within_budget_probability: float | None = None
    delta_vs_baseline_usd_micros: int
    runway: Runway | None = None


class ForecastSeries(BaseModel):
    """The forecast of one preset. A scope whose cost run was `incomplete` carries no numbers:
    `daily` and `periods` are empty. `daily` covers every date of the run (the days of the
    current month before the forecast origin included)."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str
    status: str
    daily: list[ForecastDailyPoint]
    periods: list[ForecastPeriod]


class ForecastView(BaseModel):
    """The forecast of one scope. `origin` is the forecast cutoff (history after it is not
    fitted), `data_through` the last day of data, `timezone` the zone dates are bucketed in.
    `band_low` / `band_high` are the percentiles behind every `low` / `high`.
    `budget_usd_micros` is the scope's budget per CALENDAR MONTH (`None` without one), read at
    request time: the stored amount for a monthly cycle, and for any other cycle the per-period
    amount scaled by days (`amount x days in the month / days in the cycle period`). The forecast
    stays by calendar month under every cycle (TODO(A4): forecast per org cycle). `actual_daily` runs 28 days before the first period through `data_through`. `series`
    is one entry per preset, baseline first."""

    model_config = ConfigDict(extra="forbid")

    origin: date
    data_through: date
    timezone: str
    band_low: int
    band_high: int
    budget_usd_micros: int | None = None
    presets: list[ForecastPreset]
    actual_daily: list[ActualDailyPoint]
    series: list[ForecastSeries]


class BudgetForecast(BaseModel):
    """`GET /v1/forecast?scope_type=&scope_id=` (T6) -- returned as `Envelope[BudgetForecast]`.
    `forecast` is `None` when no run is loaded, the stored run cannot be read, or this scope is
    not in the run (not an error)."""

    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_id: str
    forecast: ForecastView | None = None


BudgetForecastEnvelope = Envelope[BudgetForecast]


class PlanPeriodRef(BaseModel):
    """A period of the plan. `current` is true for the period that contains `data_through`.
    `forecast_from` is the first forecast day; for the current period the plan's
    `tokens_by_model` cover `forecast_from`..end only (actual tokens to date are not stored)
    while `needed` dollars include the actual spend to date."""

    model_config = ConfigDict(extra="forbid")

    start: date
    end: date
    forecast_from: date
    current: bool


class PlanNeeded(BaseModel):
    """What the period costs in total (actual to date plus forecast): the mean and the 50th /
    80th / 90th percentile."""

    model_config = ConfigDict(extra="forbid")

    expected_usd_micros: int
    p50_usd_micros: int
    p80_usd_micros: int
    p90_usd_micros: int


class TokensByModel(BaseModel):
    """Forecast tokens of one model for the period: the mean and the 90th percentile."""

    model_config = ConfigDict(extra="forbid")

    model: str
    expected: int
    p90: int


class PlanPeriodFigures(BaseModel):
    """One scope x preset x period. `plan_for_usd_micros` = `needed` at the configured planning
    confidence (`BudgetPlan.planning_confidence`), including the actual spend to date: the
    figure to plan the period's budget around. `gap_usd_micros` = `plan_for_usd_micros` minus the
    scope's budget (positive: the budget falls short); `None` without a budget (`plan_for` is
    still set)."""

    model_config = ConfigDict(extra="forbid")

    needed: PlanNeeded
    plan_for_usd_micros: int
    gap_usd_micros: int | None = None
    tokens_by_model: list[TokensByModel]


class PlanPresetFigures(BaseModel):
    """One preset's figures for a scope, one entry per plan period (empty for a scope whose cost
    run was `incomplete`)."""

    model_config = ConfigDict(extra="forbid")

    preset_id: str
    periods: list[PlanPeriodFigures]


class PlanRow(BaseModel):
    """One team or project of the run (`scope_type` team | repo) with its budget per calendar month (see `ForecastView.budget_usd_micros`)."""

    model_config = ConfigDict(extra="forbid")

    scope_type: str
    scope_id: str
    name: str
    budget_usd_micros: int | None = None
    by_preset: list[PlanPresetFigures]


class BudgetPlan(BaseModel):
    """`GET /v1/forecast/plan` (T6) -- returned as `Envelope[BudgetPlan]`: what every team and
    project needs per period and preset, next to its budget. Teams first, then projects, in the
    run's order; the org is not a row. `planning_confidence` is the probability (0..1) each
    `plan_for_usd_micros` is the quantile of."""

    model_config = ConfigDict(extra="forbid")

    planning_confidence: float
    periods: list[PlanPeriodRef]
    presets: list[ForecastPreset]
    rows: list[PlanRow]


BudgetPlanEnvelope = Envelope[BudgetPlan]


# ------------------------------------------------------------------------------- A7: myTips

class MyTip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    text: str
    could_save: Money


class MyTips(BaseModel):
    """`GET /v1/users/{id}/tips` (A7) -- the one new piece MyUsage adds; every other figure
    reuses the shipped single-user endpoints unchanged (ground rules section 2)."""

    model_config = ConfigDict(extra="forbid")

    tips: list[MyTip]


MyTipsEnvelope = Envelope[MyTips]
