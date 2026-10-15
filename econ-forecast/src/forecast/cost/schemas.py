"""Pydantic models for cost-run artifacts (design §8.2) and the scenario config
(§6.1), plus method constants (§3.2).

Every model tolerates additive fields (`extra="ignore"`, §8.1): a minor schema bump
never breaks a reader here. A schema id's major component is the compatibility
boundary; comparing it is the caller's job (`schema_major`), since a bare version
string carries no structure to check on its own.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: The five normalized token types (cache-write contract §2.1): `input` is uncached input,
#: each type is priced at its own rate-sheet rate.
TOKEN_TYPES: tuple[str, ...] = ("input", "cache_write_5m", "cache_write_1h", "cache_read", "output")

#: Method constants (§3.2). No config file in V1 -- a constant change is a code
#: change. `max_age_days` is not fixed; it is the forecast run's own
#: `history_lookback_days` (recorded per run in `request.json`, §8.2).
HALF_LIFE_DAYS = 14
OWN_MIX_HALF_WEIGHT_DAYS = 7

SCOPE_TYPES: tuple[str, ...] = ("organization", "team", "project")
SCOPE_STATUSES: tuple[str, ...] = ("complete", "conditional", "inactive", "incomplete")
#: Cost series statuses (§7): forecast statuses carried through unchanged, plus the
#: new `unpriced` (matched series with a missing needed rate).
SERIES_STATUSES: tuple[str, ...] = (
    "qualified",
    "unpriced",
    "inactive",
    "insufficient_history",
    "insufficient_startup",
    "sampling_failed",
)


class Tolerant(BaseModel):
    """Base for every artifact/config model here: additive fields never break a
    reader (§8.1)."""

    model_config = ConfigDict(extra="ignore")


def schema_major(schema_id: str) -> str:
    """The `<major>` of a `<name>/<major>.<minor>` schema id (§8.1)."""
    _, _, version = schema_id.rpartition("/")
    major, _, _ = version.partition(".")
    return major


# ---------------------------------------------------------------------------
# manifest.json -- cost-manifest/1.0
# ---------------------------------------------------------------------------


class ManifestFileEntry(Tolerant):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_: str = Field(alias="schema")
    bytes: int = Field(ge=0)
    sha256: str


class CostManifest(Tolerant):
    status: Literal["complete", "incomplete"]
    created_at: str
    code_version: str
    files: dict[str, ManifestFileEntry]


# ---------------------------------------------------------------------------
# request.json -- cost-request/1.0
# ---------------------------------------------------------------------------


class ForecastRunRef(Tolerant):
    name: str
    path: str
    manifest_sha256: str


class RequestPeriod(Tolerant):
    type: Literal["requested", "calendar"]
    start: date
    end: date


class RequestSettings(Tolerant):
    half_life_days: int = Field(gt=0)
    own_mix_half_weight_days: int = Field(gt=0)
    max_age_days: int = Field(gt=0)


class CostRequestDoc(Tolerant):
    org_id: str
    forecast_run: ForecastRunRef
    scenario: str
    periods: list[RequestPeriod]
    settings: RequestSettings


# ---------------------------------------------------------------------------
# paths/index.json -- cost-index/1.0
# ---------------------------------------------------------------------------


class CostIndexScope(Tolerant):
    scope_type: Literal["organization", "team", "project"]
    scope_name: str
    status: Literal["complete", "conditional", "inactive", "incomplete"]
    risk_ready: bool
    assumptions: list[str]
    reasons: list[str]


class CostIndexSeries(Tolerant):
    scope_type: Literal["organization", "team", "project"]
    scope_name: str
    model: str
    status: Literal[
        "qualified",
        "unpriced",
        "inactive",
        "insufficient_history",
        "insufficient_startup",
        "sampling_failed",
    ]
    path: str | None
    assumptions: list[str]
    reasons: list[str]


class CostIndex(Tolerant):
    org_id: str
    dates: list[date]
    samples: int = Field(ge=1)
    scopes: list[CostIndexScope]
    series: list[CostIndexSeries]


# ---------------------------------------------------------------------------
# daily_expected.jsonl -- cost-daily-expected/2.0 (one row per line)
# ---------------------------------------------------------------------------


class TokenTypeAmounts(Tolerant):
    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


class CostDailyExpectedRow(Tolerant):
    date: date
    scope_type: Literal["organization", "team", "project"]
    scope_name: str
    model: str
    agent_type: str | None
    usd: TokenTypeAmounts
    tokens: TokenTypeAmounts


# ---------------------------------------------------------------------------
# period_summary.json -- cost-period-summary/2.0
# ---------------------------------------------------------------------------


class PeriodSummaryEntry(Tolerant):
    scope_type: Literal["organization", "team", "project"]
    scope_name: str
    #: `None` = scope total.
    model: str | None
    period_type: Literal["requested", "calendar"]
    start: date
    end: date
    #: Scope statuses for a scope total (`model` is `None`); series statuses
    #: (`qualified`/`inactive` -- the only ones with a path) for a series entry.
    status: Literal["complete", "conditional", "inactive", "incomplete", "qualified"]
    expected: float
    p10: float
    p50: float
    p90: float
    assumptions: list[str]


class CostPeriodSummary(Tolerant):
    entries: list[PeriodSummaryEntry]


# ---------------------------------------------------------------------------
# mix.json -- cost-mix/2.0
# ---------------------------------------------------------------------------


class MixWindow(Tolerant):
    start: date
    end: date


class MixShare(Tolerant):
    agent_type: str
    input: float
    cache_write_5m: float
    cache_write_1h: float
    cache_read: float
    output: float


class MixEntry(Tolerant):
    scope_type: Literal["organization", "team", "project"]
    scope_name: str
    model: str
    window: MixWindow
    active_days: int = Field(ge=0)
    own_weight: float = Field(ge=0, le=1)
    shares: list[MixShare]
    #: Parent-scope attribution shares (§6.3), keyed by parent `scope_type`; absent
    #: (empty) for the Organization, which has no parent.
    attribution: dict[str, float] = Field(default_factory=dict)


class CostMix(Tolerant):
    entries: list[MixEntry]


# ---------------------------------------------------------------------------
# config/scenarios/<name>.yaml -- scenario/1.0 (§6.1)
# ---------------------------------------------------------------------------


class ScenarioScope(Tolerant):
    type: Literal["organization", "team", "project"] = "organization"
    name: str | None = None

    @model_validator(mode="after")
    def _identity(self):
        if self.type == "organization" and self.name is not None:
            raise ValueError("organization scenario scope cannot specify a name")
        if self.type != "organization" and not self.name:
            raise ValueError("team/project scenario scope requires a name")
        return self


class ModelTransfer(Tolerant):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    fraction: float = Field(gt=0, le=1)
    token_factor: float = Field(default=1.0, gt=0)
    effective_from: date | None = None


class AgentTransfer(Tolerant):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    from_: str = Field(alias="from", min_length=1)
    to: str = Field(min_length=1)
    fraction: float = Field(gt=0, le=1)
    token_factor: float = Field(default=1.0, gt=0)
    effective_from: date | None = None


class Growth(Tolerant):
    factor: float = Field(ge=0)
    effective_from: date | None = None


class ScenarioChange(Tolerant):
    model_transfer: ModelTransfer | None = None
    agent_transfer: AgentTransfer | None = None
    growth: Growth | None = None

    @model_validator(mode="after")
    def _exactly_one(self):
        present = [
            x
            for x in (self.model_transfer, self.agent_transfer, self.growth)
            if x is not None
        ]
        if len(present) != 1:
            raise ValueError(
                "a scenario change must declare exactly one of "
                "model_transfer, agent_transfer, growth"
            )
        return self


class ScenarioConfig(Tolerant):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    schema_: str = Field(alias="schema")
    name: str = Field(min_length=1)
    scope: ScenarioScope = Field(default_factory=ScenarioScope)
    changes: list[ScenarioChange] = Field(min_length=1)

    @model_validator(mode="after")
    def _not_baseline(self):
        if self.name == "baseline":
            raise ValueError("scenario name 'baseline' is reserved")
        return self
