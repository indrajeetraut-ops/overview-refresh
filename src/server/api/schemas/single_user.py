"""Typed response models for the shipped single-user surface.

F0 reference port: `GET /v1/pricing/models` is typed here first, as the worked example of
Pydantic model -> OpenAPI -> generated TS. The rest of the single-user surface
(`overview`/`rhythm`/`sessions`/`session_detail`/`insights`/`repos`) still returns bare dicts
from `single_user/service.py`; port each to a model here as its screen is wired in `src/webui/`.

`/v1/pricing/models` is deliberately envelope-free: a static price table has no datasource
mode. Display only -- ground rule 3 forbids the API re-deriving dollars from it.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .envelope import AggregateMode, Money, Sourced, Unavailable


class PricingModel(BaseModel):
    """One model's price block effective at `as_of`. All figures are decimal *strings*, never
    floats -- the wire encoding must not introduce the binary-fraction error `PriceBlock`
    itself guards against. `None` where the block leaves a rate unset."""

    model_config = ConfigDict(extra="forbid")

    effective_from: str
    input: str | None = None
    output: str | None = None
    cache_read: str | None = None
    cache_write_5m: str | None = None
    cache_write_1h: str | None = None


class PricingModelsResponse(BaseModel):
    """`GET /v1/pricing/models` (A8)."""

    model_config = ConfigDict(extra="forbid")

    as_of: str
    models: dict[str, PricingModel]


# ------------------------------------------------------------------------- U5-myusage: overview

class FilesTouched(BaseModel):
    model_config = ConfigDict(extra="forbid")

    created: int
    edited: int
    total: int


class Z1Headline(BaseModel):
    """DASH §3.1. `subagents_spawned`/`turns`/`long_turns`/`files_touched`/`repos` are `int`/
    `list[str]`/`FilesTouched` in `jsonl`/`both` mode, `Unavailable` as a whole in an
    `otel_only`-only range (`single_user/service.py::_z1_headline`'s own docstring)."""

    model_config = ConfigDict(extra="forbid")

    tokens: Sourced[int]
    cost_usd_micros: Sourced[int]
    sessions: int
    active_sessions: int
    active_time_s: Sourced[float]
    merged_prs: int
    cost_per_pr: Money
    subagents_spawned: int | Unavailable
    turns: int | Unavailable
    long_turns: int | Unavailable
    files_touched: FilesTouched | Unavailable
    repos: list[str] | Unavailable


class FavoriteModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    tokens: int
    cost_usd_micros: int


class Z0Snapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    peak_hour: Sourced[int]
    peak_hour_histogram: list[int]
    favorite_model: FavoriteModel | None = None


class ModelSplitEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tokens: int
    cost_usd_micros: int


class QuerySourceBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requests: int
    cost_usd_micros: int
    cost_est_usd_micros: int
    cost_effective_usd_micros: int


class OtelCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sessions_with_otel: int
    sessions_total: int


class Z2Consumption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_split: dict[str, ModelSplitEntry]
    by_query_source: dict[str, QuerySourceBucket] | Unavailable
    by_query_source_coverage: OtelCoverage | None = None
    #: DASH §5's provenance decomposition -- not computed by any phase yet (cold-start, every
    #: mode), so always `None` today; typed `dict | None` rather than a fixed shape since no
    #: phase has defined one to type against yet.
    provenance_mix: dict | None = None


class TopTool(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int
    failed: int


class TopSkill(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int
    tokens: int


class TopMcpServer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    calls: int
    bytes_read_total: int


class TopRepo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo_key: str
    sessions: int


class Z3UsagePatterns(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_tools: list[TopTool] | Unavailable
    top_skills: list[TopSkill] | Unavailable
    top_mcp_servers: list[TopMcpServer] | Unavailable
    top_repos: list[TopRepo] | Unavailable


class UserSeat(BaseModel):
    """The viewer's own seat (cost-based seats plan §3.5): their primary team's monthly budget /
    members (`seat_monthly`), scaled to the viewed window and prorated from the seat's start
    (`allowance`), and their window spend over it (`usage_pct`). `status` is
    `over`/`healthy`/`under`, or `new` inside the grace period."""

    model_config = ConfigDict(extra="forbid")

    team_id: str
    seat_monthly: Money
    allowance: Money
    usage_pct: float | None = None
    status: str | None = None
    in_grace: bool = False


class Overview(AggregateMode):
    """`GET /v1/users/{id}/overview?from&to` (dashboard-api-implementation plan A3, typed for
    U5-myusage). Not `Envelope`-wrapped: `single_user/service.py::overview` already spreads
    `aggregate_mode_block` at the top level (inherited from `AggregateMode` here) rather than
    nesting it under `mode`, the shape every `/v1/users/{id}/*` endpoint has always used.

    `recoverable`/`seat` (U5-myusage): the two headline pieces MyUsage needs that `z1`/`z2`/
    `z3` don't otherwise carry -- see `overview()` and `_my_seat` for where each comes from.
    """

    model_config = ConfigDict(extra="forbid")

    z0: Z0Snapshot
    z1: Z1Headline
    z2: Z2Consumption
    z3: Z3UsagePatterns
    #: Practice scorecard (PRACT §4.4) -- not computed by any phase yet, cold-start.
    z5_scorecard: None = None
    recoverable: Money
    seat: UserSeat | None = None
