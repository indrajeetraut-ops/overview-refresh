"""Config models for the dashboard API. Layering: defaults here -> YAML -> env vars, the same
pattern as `server/uploader/config.py` and `server/ingest/config.py`.

TODO(api): switch load_config to the shared config loader in src/common when that loader
lands (arch §12 "one shared loader").
"""
import os
import re
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, model_validator

from server.directory import DirectoryConfig

_ENV_REF = re.compile(r"\$\{(\w+)\}")


class HttpConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8090
    #: uvicorn worker processes (api-latency plan D1) -- route handlers are sync `def`, so a
    #: single process's DB waits overlap via the thread pool; CPU-bound aggregation is still
    #: GIL-bound to one core per process, so real parallelism needs more than one worker.
    workers: int = 1


class InsightsConfig(BaseModel):
    #: PRACT §4.3 -- below this many sessions in range, a correlation-backed insight card
    #: falls back to the citation-only form ("Anthropic recommends X; detected its absence
    #: in N sessions") rather than a personal comparison.
    min_sessions: int = 5
    #: R6.2 "long-tail sessions" -- a session's tail counts as long when at least this many
    #: turns follow its last file-changing turn, *and* those trailing turns account for at
    #: least this share of the session's total tokens. Computed read-time from `row.turns`
    #: (already a stored session_info column), computed read-time and not by the graph fold --
    #: this insight needs no fold pass, so it has no worker-side counterpart to share the field with.
    long_tail_min_trailing_turns: int = 3
    long_tail_min_trailing_token_share: float = 0.3
    #: R7.4 / R3.3 -- the session-page "resuming costs ~$Y" finding only fires once a session
    #: has actually gone idle; below this, `info.resume_cost.projected` is real but not yet
    #: worth surfacing. `now - last_event_at`, computed read-time in `service.py`, never a
    #: dollar figure -- the projected cost itself stays fold-computed (ground rule 4).
    resume_idle_min_s: int = 3600


class StoryConfig(BaseModel):
    #: Kept even though the personal band (storytelling doc §3/§7) is parked this pass --
    #: the practice scorecard's own cold-start check reads it, and the name is already
    #: fixed by the design docs.
    min_sessions_for_band: int = 5


class FlagsConfig(BaseModel):
    """Sessions-list badge thresholds (dashboard design §3.4). Derived at query time, never
    stored -- a threshold change must not require a refold."""

    costly_usd_floor: float = 5.0
    long_running_duration_s: int = 3600
    compaction_heavy_count: int = 2
    fan_out_subagent_count: int = 3
    error_retry_count: int = 1
    cold_cache_hit_ratio: float = 0.5


class PrecedenceConfig(BaseModel):
    """Modes doc §3.3: ordered chains, first available wins, resolved per session."""

    tokens: list[str] = ["otel_measures", "otel_ledger", "jsonl_models"]
    cost: list[str] = ["otel_vendor", "estimated"]


class QualityConfig(BaseModel):
    """X2 (EFF §4.1) quality-matrix thresholds (`X2-config`) -- query-time, never stored, so a
    tuning change needs no refold. The **making-cost** axis compares the PR's cost against its
    repo's own merged-PR cost distribution (efficiency note §4.1, decision 2026-09-17); the
    **acceptance-cost** axis is an OR-composite over three now-captured signals (Track
    churn/reopen), any one at/above its cutoff marking "messy acceptance." A signal that isn't
    captured for a PR (reopens/rework `None`) simply doesn't contribute -- missing data never
    flips a clean PR to messy."""

    making_cost_high_percentile: float = 0.75  # cost >= this percentile of the repo's merged PRs
    making_cost_trailing_days: int = 90        # window of repo merged PRs, ending at the PR's merge
    making_cost_min_prs: int = 5               # fewer priced repo PRs in-window -> unlabeled
    review_rounds_high: int = 1          # >=1 changes-requested round
    reopens_high: int = 1                # >=1 reopen (git_artifact_events)
    rework_ratio_high: float = 0.5       # post-open churn >= 50% of the PR's final size


class ForecastConfig(BaseModel):
    """X5 forecast/envelope scope (`D3-envelope-scope`). A feature's cost-to-finish envelope is
    drawn from the whole REPO's trailing merged-PR cost distribution, not just the feature's own
    member PRs -- most features are single-PR, so a feature-local envelope degenerates to one
    point. The window and floor are query-time knobs, never stored, so a tuning change needs no
    refold. Below `envelope_min_prs` merged PRs in the repo window, the estimate falls back to
    the feature's own members rather than publishing a thin repo distribution.

    The `runway_*` and `planning_confidence` settings belong to the precomputed budget forecast
    (`GET /v1/forecast`, `GET /v1/forecast/plan`): the status cut-offs of a budget runway and the
    confidence the planner's gap is taken at."""

    envelope_trailing_days: int = 90     # trailing window of repo merged PRs the envelope reads
    envelope_min_prs: int = 5            # below this many repo PRs in-window, use feature-local

    # Budget runway and planner (docs/plan/2026-10-05-forecast-dashboard-integration.md §3.8).
    # Query-time, never stored: a budget edit or a threshold change needs no batch rerun.
    #: runway status `lasts` below this chance of running out of budget in the period.
    runway_lasts_below: float = Field(default=0.2, gt=0, le=1)
    #: runway status `runs_out` above this chance; between the two it is `at_risk`.
    runway_runs_out_above: float = Field(default=0.8, gt=0, le=1)
    #: below this chance of running out, no run-out date is given (all three are null).
    runway_min_probability: float = Field(default=0.05, ge=0, le=1)
    #: the confidence the planner's `gap` is computed at (the share of outcomes the plan covers).
    planning_confidence: float = Field(default=0.8, gt=0, le=1)

    @model_validator(mode="after")
    def _ordered_thresholds(self) -> "ForecastConfig":
        if not (self.runway_min_probability <= self.runway_lasts_below
                <= self.runway_runs_out_above):
            raise ValueError(
                "runway thresholds must satisfy runway_min_probability <= runway_lasts_below "
                f"<= runway_runs_out_above, got {self.runway_min_probability}, "
                f"{self.runway_lasts_below}, {self.runway_runs_out_above}")
        return self


class RoomToSaveConfig(BaseModel):
    """A4's per-person "room to save" rule (cost-based seats plan §2, D6): flagged when
    recoverable spend is at least `min_share` of the person's spend AND at least
    `min_usd_per_30d` scaled to the viewed window. A share, not a flat dollar amount, because
    the share is the same at every window length and spend level (a flat $50 only ever caught
    the biggest spenders on the longest windows); the scaled floor keeps a $3 spender with $1
    recoverable from being flagged."""

    min_share: float = 0.10         # recoverable / spend
    min_usd_per_30d: float = 10.0   # recoverable floor, x window_days / 30


class UtilizationConfig(BaseModel):
    """A2/A4 seat utilization (cost-based seats plan 2026-09-26) -- query-time thresholds, never
    stored, so a tuning change needs no refold. A seat's allowance is its team's monthly budget /
    members, scaled to the viewed window and prorated from the seat's start; `over`/`under` are
    shares of that allowance (a seat with none gets `None`, never a guessed "healthy"). No
    low-use flag within `new_seat_grace_days` of a seat's first activity."""

    over: float = 0.95              # >= this share of the window's allowance -> "over"
    under: float = 0.40             # <  this share -> "under" / low use
    new_seat_grace_days: int = 14
    room_to_save: RoomToSaveConfig = Field(default_factory=RoomToSaveConfig)


class PeerConfig(BaseModel):
    """A5-peer-task-mix: `cheapest_peer`'s $/KLOC comparability gates -- query-time thresholds,
    never stored, so a tuning change needs no refold. $/KLOC is measured over **linked work
    only** (merged PRs with linked sessions, and those sessions' cost), so every gate below
    applies to that linked subset, for the subject and every candidate alike:

    - `max_pr_lines`: a PR changing more lines than this is not sized at all -- left out of lines,
      the linked-share denominator, and cost. **Off (`None`) by default** (decision 2026-09-17):
      leaving a whole PR out also drops its real spend (82% of one person's linked cost on the
      live stack), can be gamed by committing generated files alongside expensive work, and can't
      tell a generated 15K-line PR from a real migration. TODO(peer-generated-lines): handle
      generated/vendored code at file level (per-file line stats) instead, then retire this knob.
    - `min_linked_prs`: fewer linked PRs than this is not a pattern.
    - `min_linked_share`: linked PRs / sizable merged PRs -- below it we can't see how most of the
      person's work was made (e.g. a teammate without session telemetry, who would otherwise
      read as $0).
    - `min_lines_for_compare`: linked-line floor (below it, $/KLOC is noise).
    - `band_low`/`band_high`: optional ratio band on linked lines (candidate ÷ subject) so a peer
      stays within a comparable scale -- wide by default (0.2x-5x); `None` disables it."""

    max_pr_lines: int | None = None
    min_linked_prs: int = 3
    min_linked_share: float = 0.5
    min_lines_for_compare: int = 200
    band_low: float | None = 0.2
    band_high: float | None = 5.0


class DashboardConfig(BaseModel):
    flags: FlagsConfig = Field(default_factory=FlagsConfig)
    precedence: PrecedenceConfig = Field(default_factory=PrecedenceConfig)


class ModelFitDemandConfig(BaseModel):
    """X4 reasoning-demand scorer (model-fit-reasoning-demand-rework plan §5): a small,
    explainable weighted tally over a segment's own `label`/`beats`/`files`, mapped to a
    low/medium/high band by two cutoffs. Defaults encode the plan's stated intent -- PLAN/
    COMPREHEND/DESIGN are demanding, a rework/stall/delegation/context-squeeze beat is
    demanding, MISC is mechanical -- and are explicitly not final (need a product pass, same
    posture as every other `config/api.yaml` threshold)."""

    label_weights: dict[str, float] = {
        "PLAN": 3, "COMPREHEND": 3, "DESIGN": 2, "REVIEW": 0, "MISC": -1,
    }
    beat_weights: dict[str, float] = {
        "revision_loop": 3, "stall": 2, "delegation": 2, "context_squeeze": 1,
    }
    cross_file_at: int = 4        # files touched at/above this adds a cross-file signal
    cross_file_weight: float = 1
    demand_high_at: float = 3     # total tally >= this -> "high"
    demand_low_below: float = 1   # total tally <  this -> "low" (else "medium")


class ModelFitConfig(BaseModel):
    """X4 model-fit (EFF): the tier-vs-demand comparison's own tunables, replacing the
    hardcoded `_MODEL_TIER_HIGH/LOW_SUBSTR` and `_LIGHT_TASK_LABELS` (model-fit-reasoning-
    demand-rework plan). `tier_reference_model` names the representative model per tier used
    only for the counterfactual savings ratio (§3.4) -- display/comparison only, never a
    booked spend."""

    tier_by_name: dict[str, list[str]] = {"high": ["opus"], "low": ["haiku"]}
    tier_reference_model: dict[str, str] = {
        "high": "claude-opus-4-8", "mid": "claude-sonnet-5", "low": "claude-haiku-4-5-20251001",
    }
    demand: ModelFitDemandConfig = Field(default_factory=ModelFitDemandConfig)


class ApiConfig(BaseModel):
    http: HttpConfig = Field(default_factory=HttpConfig)
    #: Path to the storage module's own YAML (config/storage.yaml) -- the API never
    #: re-declares a DSN, same convention as ingest.
    storage_config: str = "config/storage.yaml"
    #: `GET /v1/pricing/models` (A8) serves this table for display only -- rule 4 forbids the
    #: API itself re-deriving dollars from it.
    #: Org id for channel selection in `common.model_pricing.Pricer`; None = vendor channels.
    pricing_org: str | None = None
    #: Catalog version pin; None = current version.
    pricing_version: str | None = None
    #: Built front-end bundle (`src/webui` -> `npm run build`), served at `/` after `/v1/*`
    #: (MVP plan U0). Resolved from cwd, same as every other path here. Absent until
    #: `vite build` has run -- the mount is skipped, not an error, so the API still boots.
    webui_dist: str = "src/webui/dist"
    insights: InsightsConfig = Field(default_factory=InsightsConfig)
    story: StoryConfig = Field(default_factory=StoryConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    quality: QualityConfig = Field(default_factory=QualityConfig)
    forecast: ForecastConfig = Field(default_factory=ForecastConfig)
    model_fit: ModelFitConfig = Field(default_factory=ModelFitConfig)
    utilization: UtilizationConfig = Field(default_factory=UtilizationConfig)
    peer: PeerConfig = Field(default_factory=PeerConfig)
    #: F2's role gate reads Track G through `server.directory.resolve_viewer`, which takes
    #: this config (provider precedence, default org/role) -- declared once here rather than
    #: the API re-loading `config/directory.yaml` itself.
    directory: DirectoryConfig = Field(default_factory=DirectoryConfig)
    #: The budget label used when the org has no saved budget cycle: a recurring calendar month
    #: (`"monthly"`, the only value accepted then). With a saved cycle (`GET /v1/budget-cycle`)
    #: the cycle's own label governs and this is ignored. Seat allowances derive from the team's
    #: per-period budget (cost-based seats plan §2).
    budget_period: str = "monthly"
    api_version: str = "0.1.0"


def load_config(path: str | Path) -> ApiConfig:
    text = Path(path).read_text(encoding="utf-8")
    text = _ENV_REF.sub(lambda m: os.environ.get(m.group(1), ""), text)
    data = yaml.safe_load(text) or {}
    return ApiConfig.model_validate(data.get("api", data))
