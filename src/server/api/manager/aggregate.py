"""Shared aggregation module for the manager surface (MVP plan Track A / F3 / F5 / X3).

Framework-free (ground rule 1): every function takes plain rows (from `manager.yaml`'s
scoped fetches) or a `Session`+`QueryCatalog`, and returns plain dicts. Endpoint bodies in
`manager/service.py` call into this; **derived metrics are computed here once and reused by
every endpoint**, not recomputed per-route.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session as DbSession

from common.model_pricing import Pricer
from server import directory
from server.storage import session_scalars
from server.storage.queries import QueryCatalog

from .. import precedence
from ..config import ApiConfig, ModelFitConfig, PeerConfig, UtilizationConfig
from ..errors import BadRequestError
from . import budget_cycles

#: Moved to `session_scalars` (api-latency plan D6) -- re-exported so existing imports of
#: these two names keep working.
_priced_total = session_scalars.priced_total
dominant_segment_label = session_scalars.dominant_segment_label

#: Plan section 4: "period in 15d/30d/60d -> resolved to a window" (U4's own client-side
#: constant, `src/webui/src/data/source.ts::PERIODS` -- kept in sync by hand, both fixed).
PERIOD_DAYS = {"15d": 15, "30d": 30, "60d": 60}

#: The room-to-save floor is configured per 30 days (`min_usd_per_30d`) and scaled from this.
_FLOOR_BASE_DAYS = 30


def resolve_timeframe(period: str | None, frm: datetime | None,
                      to: datetime | None) -> tuple[datetime, datetime]:
    """Every Track A route accepts *either* an explicit `from`/`to` window or a named
    `period` (resolved here to the same `[to - N days, to)` shape the webui's own
    `timeframeForPeriod` computes) -- one place, so the two never drift apart."""
    if frm is not None and to is not None:
        return frm, to
    if period:
        days = PERIOD_DAYS.get(period)
        if days is None:
            raise BadRequestError(f"unknown period {period!r}; expected one of {sorted(PERIOD_DAYS)}")
        to = to or datetime.now(timezone.utc)
        return to - timedelta(days=days), to
    raise BadRequestError("supply either from&to, or period")


# ------------------------------------------------------------------------ scoped fetch

#: api-latency plan Phase D: three row shapes, named by shape rather than by endpoint --
#: `manager.scope_cost_facts` (cost chain only), `manager.scope_session_facts` (+ recoverable/
#: reasoning/output/session-type scalars), `manager.person_session_facts` (+ title/channel
#: scalars, D7). Callers ask for the narrowest shape their own aggregate functions need.
_SCOPE_QUERY_BY_SHAPE = {
    "cost": "scope_cost_facts",
    "session": "scope_session_facts",
    "person": "person_session_facts",
}


def fetch_scope_sessions(s: DbSession, catalog: QueryCatalog, user_ids: list[str],
                         frm: datetime, to: datetime, shape: str = "session") -> list:
    """`session_info` rows for every user a scope covers, overlapping `[frm, to)`. Empty
    `user_ids` (an org/team with no roster yet) is a real zero, not a query error."""
    if not user_ids:
        return []
    query_name = _SCOPE_QUERY_BY_SHAPE[shape]
    return s.execute(catalog.get("manager", query_name),
                     {"user_ids": user_ids, "from": frm, "to": to}).all()


def fetch_scope_merged_prs(s: DbSession, catalog: QueryCatalog, user_ids: list[str],
                           frm: datetime, to: datetime) -> list:
    """`git_artifacts` (kind=pr) merged within `[frm, to)`, authored by users in scope."""
    if not user_ids:
        return []
    return s.execute(catalog.get("manager", "scope_merged_prs_in_range"),
                     {"user_ids": user_ids, "from": frm, "to": to}).all()


def scope_cost_fetcher(s: DbSession, catalog: QueryCatalog, scope_type: str,
                       scope_id: str) -> tuple[Callable[[datetime, datetime], list], list[str] | None]:
    """The one answer to "which sessions does this scope cover", shared by `/v1/economics` and
    `/v1/forecast` so both read the same money. Returns `(fetch, user_ids)`:

    * `fetch(frm, to)` -> `cost`-shape `session_info` rows overlapping `[frm, to)`.
    * `user_ids` -> the users an `org` / `team` scope covers (economics needs them for its PR
      fetch), or `None` for a `repo` scope.

    `org` / `team` walk the user-scoped fetch (`directory.users_in_scope` ->
    `fetch_scope_sessions`): that scope's spend across whatever repos those users touch. `repo`
    walks `repo_cost_facts` instead, so a repo's contributors' *other* repos never leak into a
    project's figures. `scope_type` must already be one of org / team / repo."""
    if scope_type == "repo":
        def fetch_repo(frm: datetime, to: datetime) -> list:
            return s.execute(catalog.get("manager", "repo_cost_facts"),
                             {"repo_id": scope_id, "from": frm, "to": to}).all()
        return fetch_repo, None

    user_ids = directory.users_in_scope(s, catalog, scope_type, scope_id)

    def fetch_users(frm: datetime, to: datetime) -> list:
        return fetch_scope_sessions(s, catalog, user_ids, frm, to, shape="cost")
    return fetch_users, user_ids


def rows_in_local_range(rows: list, tz: Any, first: date, last: date) -> list:
    """The rows whose `started_at`, converted to `tz`, falls on a local day in `first..last`."""
    return [r for r in rows
            if r.started_at is not None and first <= r.started_at.astimezone(tz).date() <= last]


def daily_actuals(rows: list, tz: Any, first: date, last: date) -> dict[date, int]:
    """Actual spend per local calendar day: `cost_effective_usd_micros` summed over every row
    (roots and subagents, as `/v1/economics`' `budget_burn` does), bucketed by `started_at`
    converted to `tz` (a `tzinfo`), kept for days in `first..last`. Days without a session are
    absent from the result; callers zero-fill."""
    out: dict[date, int] = {}
    for r in rows_in_local_range(rows, tz, first, last):
        day = r.started_at.astimezone(tz).date()
        out[day] = out.get(day, 0) + (r.cost_effective_usd_micros or 0)
    return out


def local_window(first: date, last: date, tz: Any) -> tuple[datetime, datetime]:
    """UTC instants bounding the local days `first..last` for a fetch, padded by one day on each
    side so that a DST shift or an odd offset never clips a session; the caller filters by the
    local date afterwards (`daily_actuals`)."""
    start = datetime.combine(first - timedelta(days=1), time.min, tzinfo=tz)
    stop = datetime.combine(last + timedelta(days=2), time.min, tzinfo=tz)
    return start.astimezone(timezone.utc), stop.astimezone(timezone.utc)


def is_root(row: Any) -> bool:
    """FEAS §4/R0, the same session-vs-subagent split the single-user surface uses: count
    *sessions* over roots only; sum every other figure over every row."""
    return row.parent_session_id is None


# ------------------------------------------------------------------------------- F3

def repo_rollup_canonical(rows: list) -> list[dict]:
    """F3: group by `info.repo.repo_id` with canonical provenance for cross-user views
    (REPO §1.1/§1.4). A `pr_link`/`remote`-provenance `repo_id` is a real cross-org identity
    and merges across every user that touches it; a `path`-provenance `repo_id` (already
    written as `local:<key>`, a bare folder-name guess) is **not** a stable identity across
    machines/users, so it is bucketed per-user here -- two different users' `local:api` never
    merge, even though the string is identical.

    Session-level (each row's own primary `repo_id`) -- the headline-tile case (REPO §1.3).
    TODO(F3-segment-split): a mixed session's exact per-repo cost split
    (`segments[].cost_usd_micros` x `repo_key`, straightforward now that segments carry priced
    cost -- 2026-09-15 segment-pricing plan Phase 1) is deferred until a drilldown needs it;
    re-confirmed 2026-09-15 (grep of `src/server/api` + `src/webui` for a consumer of an
    intra-session per-repo split) that no screen renders one yet -- still dead code if built.
    """
    agg: dict[tuple[str, str | None], dict] = {}
    for r in rows:
        repo_id = getattr(r, "repo_id", None)
        if not repo_id:
            continue
        provenance = getattr(r, "repo_id_provenance", None)
        local_to_user = r.user_id if provenance == "path" else None
        key = (repo_id, local_to_user)
        bucket = agg.setdefault(key, {
            "repo_id": repo_id, "local_to_user": local_to_user,
            "sessions": 0, "cost_usd_micros": 0, "tokens_total": 0,
        })
        if is_root(r):
            bucket["sessions"] += 1
        bucket["cost_usd_micros"] += r.cost_effective_usd_micros or 0
        bucket["tokens_total"] += r.tokens_total or 0
    return sorted(agg.values(), key=lambda b: -b["cost_usd_micros"])


# ------------------------------------------------------------------------------- F5

def reasoning_share(rows: list) -> dict | None:
    """F5: session-total reasoning tokens + share of total tokens.

    `reasoning_tokens` is the graph fold's own session-wide rollup
    (`worker/graph_fold.py::reasoning_tokens_of`, summed from each assistant node's
    `estimated_reasoning_tokens`), read off the row's own scalar attribute (api-latency plan
    D3). An already-ingested session folded before this rollup shipped carries `None` there --
    "never estimated," not "estimated as zero" -- which is why `seen` gates the whole return on
    at least one row actually reporting a value, rather than defaulting every missing key to 0."""
    reasoning_total = 0
    tokens_total = 0
    seen = False
    for r in rows:
        rt = r.reasoning_tokens
        if rt is not None:
            seen = True
            reasoning_total += rt
        tokens_total += getattr(r, "tokens_total", 0) or 0
    if not seen:
        return None
    return {
        "reasoning_tokens": reasoning_total,
        "tokens_total": tokens_total,
        "pct": round(reasoning_total / tokens_total * 100, 1) if tokens_total else None,
    }


# ------------------------------------------------------------------------------- X3

def segment_has_beat(segment: dict, beat_kind: str) -> bool:
    """A segment's `beats` field is `{kind: count}` (`worker/segments/beats.py::build_segments`
    -- a per-kind tally, not a list of beat records) -- shared here so every caller (X3, X4)
    reads it the same correct way, rather than each re-deriving the shape."""
    beats = segment.get("beats") or {}
    return bool(beats.get(beat_kind))


def segment_cost_by_repo_key(info: dict) -> dict[str, int | None]:
    """R7.1's per-session cost-by-repo split: group an already-priced session's own segments
    by their per-segment `repo_key` (`worker/segments/_util.py::segment_repo_key`) and sum
    `cost_usd_micros` per repo -- attributing existing spend by a structural marker, never
    re-deriving a dollar figure from tokens (ground rule 3). A single-session view, not the
    cross-session `repo_rollup_canonical` (F3/`TODO(F3-segment-split)`) -- that one attributes
    a *whole* session to its one primary `repo_id` for a cross-user rollup; this one splits
    *one* session's own segments across however many repos it actually touched.

    A repo bucket is `None`, not a partial sum, the moment any of its own contributing
    segments is itself unpriced (`cost_usd_micros is None`) -- all-or-nothing, never a partial sum
    (ground rule 3). A `repo_key` that never appears on any segment (segments haven't
    run on this session, or it resolved no repo at all) is simply absent from the result --
    the caller's own repo list decides what a missing key means (cold-start vs. genuinely $0)."""
    totals: dict[str, int] = {}
    complete: dict[str, bool] = {}
    for seg in info.get("segments") or []:
        repo_key = seg.get("repo_key")
        if repo_key is None:
            continue
        complete.setdefault(repo_key, True)
        cost = seg.get("cost_usd_micros")
        if cost is None:
            complete[repo_key] = False
            continue
        totals[repo_key] = totals.get(repo_key, 0) + cost
    return {repo_key: (totals.get(repo_key, 0) if ok else None) for repo_key, ok in complete.items()}


#: Recoverable-spend bucket -> the tone-lexicon label A5/A7 present (never "waste"). Order is the
#: efficiency note §2 bucket order (1 resume, 2 cache bust, 3 interrupted).
#:
#: TODO(rework-detector): rework (§2 bucket 4) is a coaching signal only -- per-segment `beats.
#: revision_loop` on the session page, no dollar figure -- because its detector fires for any file
#: edited >= 3 times anywhere in the session (`worker/segments/beats.py::_revision_loop`), flagging
#: 61% of spend on the live stack (2026-09-16). Design a stricter detector (e.g. re-edits following a
#: failed verification, or lines removed that were added earlier in-session), then restore a
#: "rework" bucket here: the segment's cost minus any bucket 1-3 amount timestamped inside it (they
#: already carry `ts`), still under the per-session cap below.
_BUCKET_LABEL = {
    "resume": "Resuming after a pause",
    "cache_bust": "Cache resets mid-session",
    "interrupted": "Interrupted replies",
}


def _session_buckets(row: Any) -> dict[str, int]:
    """One session's recoverable buckets (efficiency note §2), disjoint and capped.

    Disjoint by the fold's own construction: resume fires only past the cache TTL, a bust only within
    it, and an interrupt is a different request. The total is capped at the session's own spend --
    when the cap binds, every bucket is scaled down proportionally so the buckets still sum to it.

    Reads the row's own `recoverable_*` scalar attributes (api-latency plan D3) rather than its
    `info` JSON -- the cap stays a read-time computation (D4: it mixes this fold-owned value
    with `cost_effective_usd_micros`, a true-up-owned column, and there is no single write-time
    point where both are current)."""
    buckets = {
        "resume": row.recoverable_resume_usd_micros,
        "cache_bust": row.recoverable_cache_bust_usd_micros,
        "interrupted": row.recoverable_interrupted_usd_micros,
    }
    total = sum(buckets.values())
    cap = max(0, row.cost_effective_usd_micros or 0)
    if total <= cap:
        return buckets
    scaled = {k: v * cap // total for k, v in buckets.items()}
    scaled[max(buckets, key=buckets.get)] += cap - sum(scaled.values())  # integer-division remainder
    return scaled


def recoverable(rows: list) -> dict:
    """X3: recoverable spend -- what the work cost minus what doing the same work well would have
    cost (efficiency note §2, decisions 2026-09-16). Three buckets, all measured and priced by the
    fold (ground rule 3, no dollar figure re-derived here):

    - **resume**      -- `resume_cost.total_recoverable_usd_micros`: context re-written after the cache
      expired over a pause, at the write-over-read premium (the counterfactual still reads it)
    - **cache_bust**  -- `cache_busts.total_usd_micros`: a still-warm cache re-written mid-session
    - **interrupted** -- `interrupted.total_usd_micros`: responses the user interrupted and discarded

    Not recoverable, by decision: context carry (cache reads are the cheap path) and ordinary cache
    writes (growth). Rework and model over-sizing are coaching signals, never summed here
    (`TODO(rework-detector)` above).

    Per session, the buckets are capped at that session's own `cost_effective_usd_micros`
    (`_session_buckets`), so recoverable spend can never exceed spend. Buckets sum to
    ``recoverable_usd_micros`` by construction; `dominant_cause` is the largest bucket, `None` when
    nothing is recoverable.
    """
    buckets = dict.fromkeys(_BUCKET_LABEL, 0)
    total_cost = 0
    for r in rows:
        total_cost += getattr(r, "cost_effective_usd_micros", 0) or 0
        for bucket, amount in _session_buckets(r).items():
            buckets[bucket] += amount

    recoverable_total = sum(buckets.values())
    dominant = max(buckets, key=buckets.get) if recoverable_total else None
    return {
        "total_cost_usd_micros": total_cost,
        "recoverable_usd_micros": recoverable_total,
        "recoverable_pct": (recoverable_total / total_cost) if total_cost else None,
        "dominant_cause": dominant,
        "buckets_usd_micros": buckets,
    }


def habits_from_recoverable(breakdown: dict) -> list[dict]:
    """A5/A7's `habits[]`: one entry per non-zero priced bucket, lexicon-safe label +
    category. `recoverableAmount` reconciles to `breakdown["recoverable_usd_micros"]` by
    construction (F1... X3's own Verify line)."""
    return [
        {"category": cat, "label": _BUCKET_LABEL[cat], "recoverable_usd_micros": amount}
        for cat, amount in breakdown["buckets_usd_micros"].items()
        if amount
    ]


# ------------------------------------------------------------------------------- X4

def model_tier(model: str | None, config: ModelFitConfig | None = None) -> str:
    """"opus" -> high, "haiku" -> low, everything else (sonnet, and any unrecognized or
    missing name) -> mid -- deterministic, no LLM. Substrings come from `config.tier_by_name`
    (model-fit-reasoning-demand-rework plan; was hardcoded `_MODEL_TIER_HIGH/LOW_SUBSTR`);
    `config=None` (every call site that isn't `model_fit` itself) falls back to the same
    defaults that constant pair encoded."""
    config = config or ModelFitConfig()
    name = (model or "").lower()
    if any(substr in name for substr in config.tier_by_name.get("high", [])):
        return "high"
    if any(substr in name for substr in config.tier_by_name.get("low", [])):
        return "low"
    return "mid"


def dominant_model(models: dict | None) -> str | None:
    """The most-used model by total tokens in a `{model: {input, output, cache_read,
    cache_write, ...}}` breakdown -- `graph_fold.py::_dominant_model`, duplicated rather than
    imported (this module stays worker-free, ground rule 1). Works on both `session_info.
    models` and a segment's own `models` (X4/model-fit-per-segment) -- summed over only the
    four known token fields, not `.values()`, so a segment bucket's extra `cost_usd_micros`
    key never pollutes the token-total ranking."""
    if not models:
        return None
    return max(models, key=lambda m: sum((models[m] or {}).get(f, 0) or 0 for f in _MODEL_TOKEN_FIELDS))


#: `model_split`'s two token-field sets: the OTel per-request ledger (`otel_trueup.py`'s
#: `_join_requests` record shape) and the JSONL fold's per-model rollup (`session_info.models`,
#: `graph_fold.py::models_of`'s bucket shape). Named the same as the fields on each side --
#: they don't line up 1:1 (the ledger has no separate cache_write, JSONL has no cost) which is
#: exactly why `model_split` reads them through two different branches, not one.
_LEDGER_TOKEN_FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens")
_MODEL_TOKEN_FIELDS = ("input", "output", "cache_read", "cache_write")


def model_split(rows: list) -> dict[str, dict]:
    """Per-model token/cost split, from the ledger where a session has one -- the OTel ledger
    carries `model` per request in every mode that has OTel evidence -- and falling back to
    the JSONL fold's `models` rollup for a session with none at all (`jsonl_only`). Tokens are
    always summed; cost is only ever added from the ledger, since the JSONL fold's `models`
    carries no per-model price (only the session-wide `cost_effective_usd_micros` the true-up
    computed, which cannot be split by model without re-deriving it -- the thing rule 4 exists
    to forbid).

    Moved here from `single_user/service.py` (mvp-gap-closure-plan QW1/QW3) so the manager
    surface (`pr_detail`, org/team model-mix) can share it instead of recomputing it -- same
    precedent as `model_tier`/`dominant_model` living here already."""
    split: dict[str, dict] = {}
    for r in rows:
        requests = (r.otel or {}).get("requests")
        if requests:
            for req in requests:
                model = req.get("model") or "unknown"
                bucket = split.setdefault(model, {"tokens": 0, "cost_usd_micros": 0})
                bucket["tokens"] += sum(int(req.get(f) or 0) for f in _LEDGER_TOKEN_FIELDS)
                cost = req.get("cost_usd_micros")
                bucket["cost_usd_micros"] += cost if cost is not None else (req.get("cost_est_usd_micros") or 0)
            continue
        for model, usage in (r.models or {}).items():
            bucket = split.setdefault(model, {"tokens": 0, "cost_usd_micros": 0})
            bucket["tokens"] += sum(int(usage.get(f) or 0) for f in _MODEL_TOKEN_FIELDS)
    return split


def model_split_from_rows(rows: list) -> dict[str, dict]:
    """Reshapes `manager.scope_model_split`'s SQL aggregate rows into the same `{model:
    {"tokens", "cost_usd_micros"}}` dict `model_split` builds from raw session rows -- so
    `model_usage_breakdown` doesn't need to know which of the two computed its split."""
    return {r.model: {"tokens": r.tokens, "cost_usd_micros": r.cost_usd_micros} for r in rows}


def model_usage_breakdown(split: dict) -> list[dict] | None:
    """`model_split` (or `model_split_from_rows`) reshaped for direct display: one entry per
    model with its token share (`pct`, a fraction) and `tier` (`model_tier` -- a category,
    never a colour, per `schemas/README.md`'s strip-rule 1), ranked by tokens descending.
    `None` when there is nothing to show (no rows, or every model folded to zero tokens) so
    callers render the same cold-start treatment as every other derived block, rather than an
    empty list.

    QW1 (mvp-gap-closure-plan 2026-09-12): the mockup's "Models used" section was dropped
    entirely while porting Session/PR onto the real contract, apparently on the read that any
    per-model list was a prototype-ism -- but strip-rule 1 only asks that *presentation*
    (colour) be stripped, the same treatment `workQuality[]`/`channels[]`/`sessionTypes[]`
    already got while keeping their underlying data. `session_info.models` was already folded
    and stored the whole time; this was a wiring gap, not a missing capability.

    Takes an already-computed split, not rows (api-latency plan Phase D): org-wide callers
    compute it in SQL (`scope_model_split`, which never detoasts a whole scope's `otel`/
    `models` in Python); single-session/PR/feature callers still have rows carrying `otel`/
    `models` and pass `model_split(rows)` straight through."""
    total = sum(v["tokens"] for v in split.values())
    if not total:
        return None
    entries = [
        {"model": model, "tokens": v["tokens"], "pct": v["tokens"] / total,
         "cost_usd_micros": v["cost_usd_micros"] or None, "tier": model_tier(model)}
        for model, v in split.items()
    ]
    entries.sort(key=lambda e: e["tokens"], reverse=True)
    return entries


def segment_reasoning_demand(segment: dict, config: ModelFitConfig) -> str:
    """X4 (model-fit-reasoning-demand-rework plan §3.1): "low"/"medium"/"high" reasoning
    demand for one segment, from evidence already on it -- no new extraction. A small,
    explainable weighted tally, not an ML score: `label` (PLAN/COMPREHEND/DESIGN score high,
    MISC scores low, per `config.demand.label_weights`), any struggle/orchestration beat
    (`revision_loop`/`stall`/`delegation`/`context_squeeze`, `config.demand.beat_weights`),
    and a cross-file bump once `files` clears `config.demand.cross_file_at`. Mapped to a band
    by two cutoffs so the result stays "flagged because: PLAN label + a rework loop"-legible
    on the card, never a formula a reader has to trust blindly.

    Supersedes the old proxy ("did this segment write files") that this plan exists to fix:
    PLAN/COMPREHEND produce little code and are exactly the highest-demand work a session
    does, the mirror image of what "few files touched" used to imply.
    """
    d = config.demand
    tally = d.label_weights.get(segment.get("label"), 0.0)
    beats = segment.get("beats") or {}
    for beat_kind, weight in d.beat_weights.items():
        if beats.get(beat_kind):
            tally += weight
    if (segment.get("files") or 0) >= d.cross_file_at:
        tally += d.cross_file_weight
    if tally >= d.demand_high_at:
        return "high"
    if tally < d.demand_low_below:
        return "low"
    return "medium"


def segment_fit(segment: dict, tier: str, config: ModelFitConfig) -> str:
    """X4: per-segment `fit` in good/over/under -- a tier-vs-demand comparison
    (model-fit-reasoning-demand-rework plan §3.2), replacing the old "did it write code"
    proxy. `tier` is the caller's resolved comparison tier -- the segment's own (X4/
    model-fit-per-segment, `_segment_tier`, when the segment carries a `models` breakdown) or
    the session's single dominant one as a fallback (a segment folded before that landed).
    Compares it against this segment's own reasoning demand (`segment_reasoning_demand`):

    - **over** -- a `high` tier model ran genuinely low-demand (mechanical, no signs of
      difficulty) work. Never fires on PLAN/COMPREHEND/DESIGN -- the whole point of this
      rework, since those are high-demand by definition.
    - **under** -- a `low` tier model ran genuinely high-demand work (struggle signals
      present, or a demanding task class) -- a stronger model would plausibly have done it
      right the first time (EFF "Quick & dirty" vs cost-of-cheap-model). This *inverts* the
      old rule: a `revision_loop` used to suppress "over"; now it's a high-demand signal that
      can drive "under" instead -- the intended correction, not a regression.
    - **good** -- anything else: the tier plausibly matched the work.
    """
    demand = segment_reasoning_demand(segment, config)
    if tier == "high" and demand == "low":
        return "over"
    if tier == "low" and demand == "high":
        return "under"
    return "good"


def _model_fit_note(verdict: str, tier: str, flagged: list[dict]) -> str:
    """Deterministic template, no LLM/content (X4's own Verify line). Explains the *demand*
    reason (model-fit-reasoning-demand-rework plan §3.3), lexicon-clean: names the tier word,
    never a raw model name."""
    count = len(flagged)
    plural = "s" if count != 1 else ""
    labels = sorted({s["label"] for s in flagged if s.get("label")})
    if verdict == "over":
        return (f"A {tier}-tier model ran {count} low-effort segment{plural} "
                f"({', '.join(labels) or 'misc work'}) with no signs of difficulty; a "
                "lighter model may have handled these for less.")
    if verdict == "under":
        return (f"A {tier}-tier model ran {count} demanding segment{plural} "
                f"({', '.join(labels) or 'rework'}); a stronger model may have avoided the "
                "rework and cost less overall.")
    return "Model choice matched the work in this session."


def _segment_at(segment: dict, row: Any) -> date | None:
    """The date a flagged segment's own requests were actually priced at (`t0`), falling
    back to the session's `started_at` for a segment fixture/fold that predates `t0`
    (`beats.py::build_segments` has set it on every real segment since R4) -- the same
    "priced at its own date" convention `channel_cost_of`/`carry_of` already use."""
    t0 = segment.get("t0")
    if isinstance(t0, str):
        try:
            return datetime.fromisoformat(t0.replace("Z", "+00:00")).date()
        except ValueError:
            pass
    started_at = getattr(row, "started_at", None)
    return started_at.date() if started_at else None


def _blended_rate_per_million(model: str | None, at: date,
                              pricing: Pricer) -> Decimal | None:
    """A representative $/M-token rate for a tier's reference model: the mean of its input
    and output rates on `at`. A blended proxy, not a re-pricing of the segment's own exact
    token mix -- a segment carries no per-kind token breakdown to re-price exactly (only its
    already-priced `cost_usd_micros`) -- used only for the counterfactual downgrade ratio
    below (§3.4), never a booked spend. `None` when the model does not resolve or that token
    kind has no price on `at`."""
    if not model:
        return None
    rates = pricing.rates(model, at)
    if rates is None or rates["input"] is None or rates["output"] is None:
        return None
    return (rates["input"] + rates["output"]) / 2 * 1_000_000


def _tier_downgrade_ratio(cheaper_tier: str, actual_tier: str, at: date | None,
                          config: ModelFitConfig,
                          pricing: Pricer | None) -> Decimal | None:
    """§3.4's `price_ratio`: the cheaper tier's representative blended rate over the actual
    tier's, both priced at `at` -- `None` (never a guessed ratio) when there is no pricing
    table, no segment date, or either reference model has no price block covering `at`."""
    if at is None or pricing is None:
        return None
    cheaper_rate = _blended_rate_per_million(config.tier_reference_model.get(cheaper_tier), at,
                                             pricing)
    actual_rate = _blended_rate_per_million(config.tier_reference_model.get(actual_tier), at,
                                            pricing)
    if not cheaper_rate or not actual_rate:
        return None
    return cheaper_rate / actual_rate


def _over_savings_usd_micros(flagged: list[dict], row: Any, config: ModelFitConfig,
                             pricing: Pricer | None) -> int | None:
    """§3.4: the real, counterfactual saving of running each `over`-flagged segment one tier
    down instead -- `cost * (1 - price_ratio(mid, high, at))`, priced at each segment's own
    date (`over` only ever fires at tier `high`, so the downgrade is always to `mid`).
    All-or-nothing (ground rule 3), same posture as `segment_cost_by_repo_key`: `None`, not a
    partial total, the moment any flagged segment's cost or its ratio can't be priced. This
    is a counterfactual estimate -- the same real tokens' dollar figure re-priced at a real
    cheaper rate is legitimate, but it is still hypothetical, never a booked saving."""
    total = Decimal(0)
    for seg in flagged:
        cost = seg.get("cost_usd_micros")
        if cost is None:
            return None
        ratio = _tier_downgrade_ratio("mid", "high", _segment_at(seg, row), config, pricing)
        if ratio is None:
            return None
        total += Decimal(cost) * (Decimal(1) - ratio)
    return int(total.to_integral_value(rounding="ROUND_HALF_EVEN"))


def _segment_tier(segment: dict, session_tier: str, config: ModelFitConfig) -> str:
    """X4/model-fit-per-segment: the segment's own tier when it carries a per-model
    breakdown (`models`, `beats.py::build_segments`) -- `model_tier(dominant_model(...))`,
    the same derivation the session-level tier already uses, just scoped to this segment's
    own requests. Falls back to the session's single dominant tier for a segment folded
    before this landed (`models` absent -- a backfill re-runs `build_segments` to populate
    it, same as every other fold-level addition; optional, deferrable)."""
    models = segment.get("models")
    if not models:
        return session_tier
    return model_tier(dominant_model(models), config)


def _pick_model_fit_verdict(segments: list[dict]) -> tuple[str, list[dict]]:
    """`under` (a cheap model driving rework) wins over `over` (an oversized model on easy
    work) when a session's segments carry both -- reachable now that per-segment tiers
    (X4/model-fit-per-segment) let one session show a genuinely `high`-tier segment and a
    genuinely `low`-tier one at once, not just one session-wide tier applied everywhere.
    `under` is the sharper, more actionable finding (EFF frames "Quick & dirty" as the
    costlier of the two failure modes)."""
    over = [s for s in segments if s["fit"] == "over"]
    under = [s for s in segments if s["fit"] == "under"]
    if under:
        return "under", under
    if over:
        return "over", over
    return "good", []


def model_fit(row: Any, config: ApiConfig,
             pricing: Pricer | None = None) -> dict | None:
    """X4: this session's `modelFit{verdict, note, savings_usd_micros}` plus each segment's
    own `fit` (D1's session drilldown wants both). `None` (cold-start, not a "good" verdict)
    when the session has no segments at all.

    `pricing` prices the `over` savings estimate (§3.4) at each flagged segment's own
    date; omit it (`None`) to get every other field with `savings_usd_micros` always `None`
    for an `over` verdict, same cold-start posture as everywhere else pricing is optional.
    """
    info = getattr(row, "info", None) or {}
    raw_segments = info.get("segments") or []
    if not raw_segments:
        return None
    mf_config = config.model_fit
    # X4/model-fit-per-segment: each segment compares against its *own* tier when it carries
    # a per-model breakdown, falling back to the session's single dominant tier otherwise
    # (`_segment_tier`) -- this is the site the rework left a TODO marker at.
    session_tier = model_tier(dominant_model(getattr(row, "models", None)), mf_config)
    segments = [
        {**seg, "fit": segment_fit(seg, _segment_tier(seg, session_tier, mf_config), mf_config)}
        for seg in raw_segments
    ]
    verdict, flagged = _pick_model_fit_verdict(segments)
    savings_usd_micros = 0
    if verdict == "over":
        savings_usd_micros = _over_savings_usd_micros(flagged, row, mf_config, pricing)
    # `segment_fit` only ever returns "over" at tier "high" and "under" at tier "low" (its own
    # branching), so every flagged segment in `verdict`'s bucket shares that tier by
    # construction, regardless of whether it came from the segment's own model or the
    # session-wide fallback -- the note names that tier, not the (now potentially mixed)
    # session-level one.
    note_tier = {"over": "high", "under": "low"}.get(verdict, session_tier)
    return {
        "verdict": verdict,
        "note": _model_fit_note(verdict, note_tier, flagged),
        "savings_usd_micros": savings_usd_micros,
        "segments": segments,
    }


# ------------------------------------------------------------------------ shared derived metrics

def merged_pr_count(pr_rows: list) -> int:
    return len(pr_rows)


def blended_cost_per_pr_usd_micros(total_cost_usd_micros: int, merged_prs: int) -> int | None:
    """"Blended" $/PR (dashboard-iteration-plan precedent): total scope spend over the window
    divided by merged PRs in the same window -- not a per-PR attribution walk
    (`session_git_links`, precise but heavier; reserved for D2's PR detail). `None`, not a
    div-by-zero 0, when nothing merged in range."""
    if merged_prs <= 0:
        return None
    return round(total_cost_usd_micros / merged_prs)


def _pr_final_lines(stats: dict | None) -> int | None:
    """A merged PR's line-volume (`additions + deletions`, churn -- deletions count as real
    delivered work, `post_open_commit_churn`'s own `final_lines` expression mirrored here in
    Python since `scope_merged_prs_in_range` already selects `git_artifacts.stats`). `None`,
    not a guessed `0`, when the PR carries no line stats yet -- a PR ingested before
    `map_github.py` started capturing `additions`/`deletions`, or a non-GitHub PR."""
    if not stats:
        return None
    additions, deletions = stats.get("additions"), stats.get("deletions")
    if additions is None or deletions is None:
        return None
    return int(additions) + int(deletions)


def linked_work_volume(pr_rows: list, sessions_by_pr: dict[str, list[str]],
                       session_cost_usd_micros: dict[str, int], peer_config: PeerConfig) -> dict:
    """A5-peer-task-mix: one person's $/KLOC inputs over **linked work only** -- their merged PRs
    that have linked sessions (`session_git_links`), and those sessions' cost. Cost and lines then
    describe the same work: all-session spend against all-PR lines didn't, and read a teammate
    with no session telemetry as $0/KLOC.

    A PR with no line stats can't be sized; a PR over `peer_config.max_pr_lines` isn't (generated
    or vendored code). Both are left out of lines, cost, and `sizable_prs` -- the linked-share
    denominator. A session linked to several of the person's PRs is costed once. Its cost can't
    be split, so it counts in full whenever any one of its PRs is sized.

    Returns `{merged_prs, sizable_prs, linked_prs, lines_changed, cost_usd_micros,
    cost_per_kloc_usd_micros}`; `cost_per_kloc_usd_micros` is `None` when there's no linked cost."""
    sizable = linked = lines = 0
    session_ids: set[str] = set()
    for pr in pr_rows:
        pr_lines = _pr_final_lines(pr.stats)
        if pr_lines is None or (peer_config.max_pr_lines is not None and pr_lines > peer_config.max_pr_lines):
            continue
        sizable += 1
        pr_sessions = sessions_by_pr.get(pr.artifact_id) or []
        if not pr_sessions:
            continue
        linked += 1
        lines += pr_lines
        session_ids.update(pr_sessions)
    cost = sum(session_cost_usd_micros.get(sid) or 0 for sid in session_ids)
    return {
        "merged_prs": len(pr_rows), "sizable_prs": sizable, "linked_prs": linked,
        "lines_changed": lines, "cost_usd_micros": cost,
        "cost_per_kloc_usd_micros": cost_per_kloc_usd_micros(cost, lines) if cost > 0 else None,
    }


def cost_per_kloc_usd_micros(total_cost_usd_micros: int, lines_changed: int | None) -> int | None:
    """A5-peer-task-mix: $/KLOC (cost per 1,000 lines changed) -- the volume-normalized
    efficiency metric that replaces $/PR (a PR is not a unit of volume: a one-line fix and a
    2,000-line feature are both "1 PR"). `None`, never a div-by-zero or a div-by-`None`, when
    `lines_changed` is absent or non-positive."""
    if not lines_changed or lines_changed <= 0:
        return None
    return round(total_cost_usd_micros / lines_changed * 1_000)


def rework_rate(pr_rows: list) -> float | None:
    """Average `pr_classification.rework_ratio` over merged PRs that have one. `None` (not
    `0.0`) when nothing in scope has been classified for rework yet -- the sibling rework
    module (`pr_class.py`'s own docstring) may not have run at all, which is a different
    truth than "measured, and it's zero"."""
    ratios = [float(r.rework_ratio) for r in pr_rows if r.rework_ratio is not None]
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def avg_time_to_merge_days(pr_rows: list) -> float | None:
    """A3's avgTimeToPR -- mean `merged_at - created_at` over merged PRs in scope, in days.
    `None` (not `0`) when nothing merged."""
    spans = [
        (r.merged_at - r.created_at).total_seconds() / 86400
        for r in pr_rows if r.merged_at is not None and r.created_at is not None
    ]
    if not spans:
        return None
    return sum(spans) / len(spans)


def last_active_at(rows: list) -> datetime | None:
    """Most recent activity in a scope -- the max `last_event_at` over its sessions."""
    stamps = [r.last_event_at for r in rows if getattr(r, "last_event_at", None) is not None]
    return max(stamps) if stamps else None


def budget_pct(spend_usd_micros: int, window_budget_usd_micros: int | None) -> float | None:
    """% of budget -- `None` (F1's own rule), never a division by zero, when the scope has no
    budget set. The budget must already be scaled to the viewed window
    (`window_budget_usd_micros`): comparing a window's spend with a whole month's budget made a
    15-day view read half as full as it was (cost-based seats plan §0.4)."""
    if not window_budget_usd_micros:
        return None
    return float(spend_usd_micros) / float(window_budget_usd_micros)


# ------------------------------------------------------------------ cost-based seats (plan §2)

def _aware(dt: datetime) -> datetime:
    """Every comparison below is between aware timestamps; a naive one is taken as UTC (the
    storage layer's own convention) rather than raising on a mixed comparison."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def window_days(frm: datetime, to: datetime) -> float:
    """The viewed window's length in days (fractional). An empty or inverted window is a request
    error, never a zero-length denominator further down."""
    days = (_aware(to) - _aware(frm)).total_seconds() / 86400
    if days <= 0:
        raise BadRequestError("the window must end after it starts")
    return days


def budget_usd_micros(amount) -> int | None:
    """A stored budget (`Numeric(12,2)` USD, or its decimal-string API form) as integer USD
    micros -- the one conversion every seat/budget figure goes through. `None` stays `None`."""
    if amount is None:
        return None
    return int(Decimal(str(amount)) * 1_000_000)


def _cycle_of(period: str | budget_cycles.Cycle | budget_cycles.ActiveCycle) -> budget_cycles.Cycle:
    """The cycle a budget is measured against: given directly, or inside an `ActiveCycle`, or
    from a bare budget label (only the calendar labels name a whole cycle; `"sprint"`/`"custom"`
    need the org's configuration and are a `BadRequestError` as a bare string)."""
    if isinstance(period, budget_cycles.ActiveCycle):
        return period.cycle
    if isinstance(period, budget_cycles.Cycle):
        return period
    for kind in budget_cycles.CALENDAR_KINDS:
        if budget_cycles.CYCLE_LABELS[kind] == period:
            return budget_cycles.Cycle(kind=kind)
    raise BadRequestError(f"unsupported budget period {period!r}; sprint and custom budgets "
                          f"need the org's cycle")


def _utc(dt: datetime) -> datetime:
    return _aware(dt).astimezone(timezone.utc)


def _full_period(cycle: budget_cycles.Cycle, frm: datetime, to: datetime) -> dict | None:
    """The cycle period `[frm, to)` is a whole period of or a period-to-date of, else `None`:
    `frm` is exactly a period's first instant (00:00 UTC) and `to` falls after it and no later
    than the period's end. The server's own periods (`GET /v1/budget-cycle/periods`) satisfy this
    exactly; a window that merely resembles one -- another start, another timezone -- does not,
    and is treated as rolling."""
    f, t_ = _utc(frm), _utc(to)
    if (f.hour, f.minute, f.second, f.microsecond) != (0, 0, 0, 0):
        return None
    period = budget_cycles.period_containing(cycle, f.date())
    if period["start"] != f.date():
        return None
    end = datetime(period["end_exclusive"].year, period["end_exclusive"].month,
                   period["end_exclusive"].day, tzinfo=timezone.utc)
    return period if f < t_ <= end else None


def _length_at_end(cycle: budget_cycles.Cycle, to: datetime) -> int:
    """Days in the period that contains the window's last instant (`to` is exclusive, so a
    window ending exactly at midnight on the 1st ends in the period before)."""
    return budget_cycles.period_containing(cycle, (_utc(to) - timedelta(microseconds=1)).date())["days_total"]


def window_budget_usd_micros(period_budget_usd_micros: int | None, frm: datetime, to: datetime,
                             period: str | budget_cycles.Cycle | budget_cycles.ActiveCycle) -> int | None:
    """A per-period budget measured against the viewed window:
      * `[frm, to)` is a whole period of the cycle, or a period-to-date of it (starts exactly at a
        period start, ends inside it): the FULL period budget -- spend so far against the whole
        period's budget;
      * any other (rolling) window: `budget x window_days / days in the period containing the
        window's end`, the period's actual length (28-31 for a month, 14 for a two-week sprint).
    `period` is the org's cycle (an `ActiveCycle`/`Cycle`) or a calendar budget label such as
    `"monthly"`. `None` with no budget (or a zero one), so callers show "--" rather than 0% or
    infinity."""
    if not period_budget_usd_micros:
        return None
    cycle = _cycle_of(period)
    if _full_period(cycle, frm, to) is not None:
        return period_budget_usd_micros
    return round(period_budget_usd_micros * window_days(frm, to) / _length_at_end(cycle, to))


def seat_allowance_usd_micros(seat_period_usd_micros: int | None, seat_start: datetime | None,
                              frm: datetime, to: datetime,
                              period: str | budget_cycles.Cycle | budget_cycles.ActiveCycle) -> int | None:
    """One seat's allowance for the viewed window: the team's per-period budget / members, in the
    same terms as `window_budget_usd_micros`, prorated from the seat's start when that falls
    inside the window.
      * whole-period / period-to-date window: the seat's full period allowance, or -- for a seat
        that started inside the period -- the share of the period from its start to the period's
        END (a person who joined 10 days into a 30-day period is allowed 20/30, whatever day the
        window stops on);
      * rolling window: `allowance x days from the seat's start (or the window's) to `to`, over the days
        in the period containing `to`.
    `None` with no seat budget, or when the seat starts at/after the window's end -- never 0,
    which would read as infinite usage."""
    if not seat_period_usd_micros:
        return None
    cycle = _cycle_of(period)
    start = _aware(frm)
    if seat_start is not None and _aware(seat_start) > start:
        start = _aware(seat_start)
    if start >= _aware(to):
        return None
    full = _full_period(cycle, frm, to)
    if full is not None:
        if start == _aware(frm):
            return seat_period_usd_micros
        end = datetime(full["end_exclusive"].year, full["end_exclusive"].month,
                       full["end_exclusive"].day, tzinfo=timezone.utc)
        remaining_days = (end - _utc(start)).total_seconds() / 86400
        return round(seat_period_usd_micros * remaining_days / full["days_total"])
    return round(seat_period_usd_micros * window_days(start, to) / _length_at_end(cycle, to))


def usage_pct(spend_usd_micros: int, allowance_usd_micros: int | None) -> float | None:
    """Spend as a share of an allowance; `None` with no allowance to compare against."""
    if not allowance_usd_micros:
        return None
    return spend_usd_micros / allowance_usd_micros


def in_grace(seat_start: datetime | None, to: datetime, grace_days: int) -> bool:
    """A seat younger than `grace_days` at the window's end is never flagged low use -- someone
    who joined last week isn't under-using their seat. No known start is not a new seat."""
    if seat_start is None:
        return False
    return (_aware(to) - _aware(seat_start)) < timedelta(days=grace_days)


def is_low_use(pct: float | None, seat_start: datetime | None, to: datetime,
               config: UtilizationConfig) -> bool:
    """The one low-use rule (plan §2): an allowance to compare against, usage below `under`,
    and past the new-seat grace period."""
    return (pct is not None and pct < config.under
            and not in_grace(seat_start, to, config.new_seat_grace_days))


def spend_by_user(rows: list, config: ApiConfig) -> dict[str, int]:
    """Per-user spend under the same cost precedence every `spend` field uses (`_cost` in
    `service.py`), so a person's seat usage reconciles to the spend shown next to it."""
    by_user: dict[str, list] = {}
    for r in rows:
        if r.user_id:
            by_user.setdefault(r.user_id, []).append(r)
    chain = config.dashboard.precedence.cost
    return {uid: int(precedence.resolve_aggregate(urows, chain)[0]) for uid, urows in by_user.items()}


def fetch_seat_starts(s: DbSession, catalog: QueryCatalog, user_ids: list[str]) -> dict[str, datetime]:
    """`manager.seat_start_for_users`: LEAST(users.created_at, first root session) per user."""
    if not user_ids:
        return {}
    rows = s.execute(catalog.get("manager", "seat_start_for_users"), {"user_ids": list(user_ids)})
    return {r.user_id: r.seat_start for r in rows if r.seat_start is not None}


def previous_window(frm: datetime, to: datetime) -> tuple[datetime, datetime]:
    """The equal-length window immediately before `[frm, to)`, for "vs last period" (A1's
    headline). A calendar-naive shift -- a 15d period compares to the 15d before it, not the
    same calendar dates last month; good enough for the rolling `period` selector U4 offers."""
    span = to - frm
    return frm - span, frm


def pct_change(current: float, previous: float) -> float | None:
    """`None` (not a divide-by-zero or a misleading +inf) when there was nothing last period
    to compare against."""
    if not previous:
        return None
    return (current - previous) / previous


def group_rows_by_team(rows: list, user_team: dict[str, str | None]) -> dict[str, list]:
    """Split scope rows by each row's `user_id`'s team (A1/A3's per-team breakdown from one
    org-wide fetch, rather than one query per team). A user with no team mapping (not on the
    roster, or org-scope rows for a user whose membership lookup missed) is dropped -- a
    team breakdown cannot place spend on a team the user isn't on."""
    by_team: dict[str, list] = {}
    for r in rows:
        team_id = user_team.get(getattr(r, "user_id", None))
        if team_id:
            by_team.setdefault(team_id, []).append(r)
    return by_team


def group_rows_by_user(rows: list) -> dict[str, list]:
    by_user: dict[str, list] = {}
    for r in rows:
        if r.user_id:
            by_user.setdefault(r.user_id, []).append(r)
    return by_user


def person_status(recoverable_usd_micros: int, spend_usd_micros: int, frm: datetime, to: datetime,
                  config: UtilizationConfig) -> str:
    """A4's "room to save" flag (cost-based seats plan D6): recoverable spend is at least
    `room_to_save.min_share` of the person's spend AND at least `min_usd_per_30d` scaled to the
    window. A share because it is the same at every window length and spend level -- a flat
    dollar cutoff only ever caught the biggest spenders on the longest windows; the scaled floor
    keeps trivial amounts (a $3 spender with $1 recoverable) from being flagged. No spend, no
    flag."""
    if not spend_usd_micros or spend_usd_micros <= 0:
        return "on-track"
    rule = config.room_to_save
    share = recoverable_usd_micros / spend_usd_micros
    floor = rule.min_usd_per_30d * 1_000_000 * window_days(frm, to) / _FLOOR_BASE_DAYS
    return ("room-to-save" if share >= rule.min_share and recoverable_usd_micros >= floor
            else "on-track")


def velocity_weekly(weekly_rows: list) -> list[dict]:
    """A5's velocity series -- one point per ISO week a merged PR landed (weeks with zero
    are simply absent; the client fills gaps if it wants a continuous axis)."""
    return [{"week": r.week.date().isoformat(), "merged_prs": r.n} for r in weekly_rows]


def _peer_comparable(c: dict | None, peer_config: PeerConfig) -> bool:
    """Whether one person's `linked_work_volume` clears every `PeerConfig` gate."""
    return (
        c is not None
        and c["cost_per_kloc_usd_micros"] is not None
        and c["linked_prs"] >= peer_config.min_linked_prs
        and c["sizable_prs"] > 0 and c["linked_prs"] / c["sizable_prs"] >= peer_config.min_linked_share
        and c["lines_changed"] >= peer_config.min_lines_for_compare
    )


def cheapest_peer(candidates: dict[str, dict], exclude: str, peer_config: PeerConfig) -> dict | None:
    """A5's `peerCompare` (A5-peer-task-mix) -- the teammate with the lowest linked-work $/KLOC,
    excluding `exclude` (the person themself, who must be in `candidates`). $/PR rewarded trivial
    one-line PRs; $/KLOC over *all* spend and *all* PR lines named a teammate with no session
    telemetry "cheapest" at $0. `candidates` maps user_id -> `linked_work_volume(...)`.

    The subject and every candidate must clear the same `PeerConfig` gates (`_peer_comparable`):
    enough linked PRs, a large enough linked share of their sizable PRs, a linked-line floor, and
    real linked cost. A subject who doesn't returns `None` outright -- no honest peer to name.
    `band_low`/`band_high` (optional, wide by default) additionally keep a candidate's linked
    lines within that ratio of the subject's own.

    Returns `{"user_id", "cost_per_kloc_usd_micros"}` for display, or `None` when nothing
    qualifies.

    TODO(A5-peer-task-mix): "cheapest peer on a similar task-mix" (the plan's fuller framing)
    needs a task-class signal this doesn't have yet (X2/session_type territory) -- this
    compares across the whole team by volume, not a matched task-mix subset, until that lands.
    """
    subject = candidates.get(exclude)
    if not _peer_comparable(subject, peer_config):
        return None
    subject_lines = subject["lines_changed"]

    def _in_band(lines: int) -> bool:
        if peer_config.band_low is None or peer_config.band_high is None:
            return True
        return peer_config.band_low <= lines / subject_lines <= peer_config.band_high

    qualifying = {
        uid: c["cost_per_kloc_usd_micros"] for uid, c in candidates.items()
        if uid != exclude and _peer_comparable(c, peer_config) and _in_band(c["lines_changed"])
    }
    if not qualifying:
        return None
    best_uid = min(qualifying, key=qualifying.get)
    return {"user_id": best_uid, "cost_per_kloc_usd_micros": qualifying[best_uid]}


def usage_status(pct: float | None, config: UtilizationConfig) -> str | None:
    """A2's `over` / `healthy` / `under` utilization band, or `None` with no allowance to
    compare against (never a guessed "healthy"). `pct` is spend over a dollar allowance
    (cost-based seats plan). Bands are `config.over`/`config.under` (iteration-plan D: never
    "limit exceeded" -- that wording lives entirely client-side; these are just the sort
    buckets)."""
    if pct is None:
        return None
    if pct >= config.over:
        return "over"
    if pct < config.under:
        return "under"
    return "healthy"


def seat_usage_status(pct: float | None, seat_start: datetime | None, to: datetime,
                      config: UtilizationConfig) -> str | None:
    """A seat's `usage_status` band with the new-seat grace applied: a seat inside its grace
    period that would read `under` reads `new` instead (cost-based seats plan D7) -- the same
    rule `is_low_use` applies. One helper so the Team, Usage and My-usage views can't disagree
    on a seat."""
    band = usage_status(pct, config)
    if band == "under" and in_grace(seat_start, to, config.new_seat_grace_days):
        return "new"
    return band


def person_headline_status(room_status: str, seat_band: str | None) -> str:
    """A4's one-word verdict for a person: `room-to-save` first (the actionable finding), then
    the seat band -- `over`, `under`, `new` -- and `on-track` only when neither flags anything
    (a healthy seat, or no allowance to compare against). `status` used to carry the
    recoverable flag alone, so a seat at 117% of its allowance read "on-track"."""
    if room_status == "room-to-save":
        return room_status
    if seat_band in ("over", "under", "new"):
        return seat_band
    return "on-track"


def session_duration_minutes(row: Any) -> float | None:
    ended = getattr(row, "ended_at", None) or getattr(row, "last_event_at", None)
    started = getattr(row, "started_at", None)
    if ended is None or started is None:
        return None
    return max((ended - started).total_seconds() / 60, 0)


def avg_session_minutes(rows: list) -> float | None:
    """Root sessions only (FEAS section 4/R0) -- a sub-agent's own duration is not a
    top-level session a user "ran"."""
    durations = [d for r in rows if is_root(r) and (d := session_duration_minutes(r)) is not None]
    if not durations:
        return None
    return sum(durations) / len(durations)


def sessions_with_no_output_pct(rows: list) -> float | None:
    """A2's `SESSIONS_NO_OUTPUT_PCT` -- the share of root sessions that touched no files at
    all (the row's own `has_files` scalar, api-latency plan D3). `None` when there are no root
    sessions in range."""
    roots = [r for r in rows if is_root(r)]
    if not roots:
        return None
    no_output = sum(1 for r in roots if not r.has_files)
    return no_output / len(roots)


def tokens_by_user(rows: list) -> dict[str, int]:
    totals: Counter[str] = Counter()
    for r in rows:
        if r.user_id:
            totals[r.user_id] += r.tokens_total or 0
    return dict(totals)


def seats_under_threshold(member_ids: list[str], spend_by_user: dict[str, int],
                          seat_allowance_by_user: dict[str, int | None],
                          seat_start_by_user: dict[str, datetime], to: datetime,
                          config: UtilizationConfig) -> int | None:
    """A2's low-seat-use count over a team's MEMBERS -- including members with no sessions in the
    window, who are the clearest low-use case and were previously never counted (the old version
    iterated active users only). Each seat is judged by `is_low_use` against its own prorated
    allowance (cost-based seats plan §2). `None` (not 0) when no member has an allowance -- there
    is nothing to be "under" without one."""
    if not any(seat_allowance_by_user.get(uid) for uid in member_ids):
        return None
    return sum(
        1 for uid in member_ids
        if is_low_use(usage_pct(spend_by_user.get(uid, 0), seat_allowance_by_user.get(uid)),
                      seat_start_by_user.get(uid), to, config)
    )


def session_type_counts(rows: list) -> list[dict]:
    """A2's `sessionTypes[]` -- each root session's dominant segment label (the row's own
    `session_type` scalar, api-latency plan D3 -- `dominant_segment_label` over `info.segments`,
    computed in SQL). A row with no segments yet (cold-start on this fold, or an otel_only
    stub) has `session_type` `None` and is omitted, not bucketed into a synthetic "unknown"."""
    counts: Counter[str] = Counter()
    for r in rows:
        if not is_root(r):
            continue
        if r.session_type:
            counts[r.session_type] += 1
    return [{"type": t, "count": n} for t, n in sorted(counts.items(), key=lambda kv: -kv[1])]


def agent_task_mix(rows: list) -> list[dict]:
    """Overview's task usage -- root sessions per (agent, dominant session type), counted by
    session (never dollars). The same rows and the same rules as `session_type_counts` (roots
    only; a row with no `session_type` is omitted), so summing `count` per `type` reproduces it
    exactly. Ordered agent asc, count desc, type asc, so the result is deterministic."""
    counts: Counter[tuple[str, str]] = Counter()
    for r in rows:
        if is_root(r) and r.session_type:
            counts[(r.agent, r.session_type)] += 1
    return [{"agent": agent, "type": type_, "count": n}
            for (agent, type_), n in sorted(counts.items(), key=lambda kv: (kv[0][0], -kv[1], kv[0][1]))]


def resolve_git_repo_ids(s: DbSession, catalog: QueryCatalog,
                         session_repo_ids: list[str]) -> dict[str, str]:
    """Bridge two repo-identity namespaces this codebase has **not yet unified** (documented
    as deferred in `storage/schema/git.py`'s own `GitRepo` docstring: "distinct from
    `repositories.repo_id`... designed to be adopted by [that scheme] once the identity phase
    lands"):

    - `session_info.repo_id` / F3's canonical rollup use git_ident's scheme
      (`github:owner/name`, `common/git_ident.py`) -- what A3's projects table and F3 key rows on.
    - `git_artifacts.repo_id` (PRs, issues, reviews) uses git-source's own numeric scheme
      (`github:<repo_num>`, `ingest/git/map_github.py`) -- what F4/X1 and the merged-PR/
      contributor/feature queries need to filter on.

    Given a set of session-side repo_ids, returns `{session_repo_id: git_repo_id}` for the
    ones `git_repos` has onboarded (a GitHub App installed and backfilled) -- a repo missing
    from the result has no git-source data connected, not a lookup bug.

    TODO(repo-identity-unification): this is the interim seam until the identity phase
    git.py's docstring names actually lands; A6/F4 use it per-repo, A3 uses it in bulk.
    """
    full_names = {rid.removeprefix("github:").lower() for rid in session_repo_ids
                 if rid.startswith("github:")}
    if not full_names:
        return {}
    rows = s.execute(catalog.get("manager", "git_repo_ids_for_full_names"),
                     {"full_names": sorted(full_names)})
    by_full_name = {r.full_name: r.repo_id for r in rows}
    return {
        rid: by_full_name[rid.removeprefix("github:").lower()]
        for rid in session_repo_ids
        if rid.startswith("github:") and rid.removeprefix("github:").lower() in by_full_name
    }


def resolve_git_repo_id(s: DbSession, catalog: QueryCatalog, session_repo_id: str) -> str | None:
    """Single-repo form of `resolve_git_repo_ids` (A6/F4's own call shape)."""
    return resolve_git_repo_ids(s, catalog, [session_repo_id]).get(session_repo_id)


def resolve_session_repo_id(s: DbSession, catalog: QueryCatalog, git_repo_id: str) -> str | None:
    """The reverse of `resolve_git_repo_id`: a git-source `repo_id` (what a PR's own
    `git_artifacts.repo_id` carries) back to the session-side git_ident scheme --
    D2 needs this to gate a PR route through `gate.require_repo`, whose `Viewer.visible_repos`
    is populated in the session-side scheme, the same identity-namespace bridge as the
    forward direction (see `resolve_git_repo_ids`'s own docstring for the full why).
    `None` when the git-source repo_id doesn't resolve to an onboarded repo."""
    row = s.execute(catalog.get("manager", "full_name_for_git_repo_id"),
                    {"repo_id": git_repo_id}).first()
    return f"github:{row.full_name.lower()}" if row else None


def repo_contributors(rows: list) -> dict[tuple[str, str | None], set[str]]:
    """Distinct contributing `user_id`s per canonical repo bucket -- same key scheme as
    `repo_rollup_canonical` (A3/A6's contributor counts)."""
    out: dict[tuple[str, str | None], set[str]] = {}
    for r in rows:
        repo_id = getattr(r, "repo_id", None)
        if not repo_id or not r.user_id:
            continue
        provenance = getattr(r, "repo_id_provenance", None)
        key = (repo_id, r.user_id if provenance == "path" else None)
        out.setdefault(key, set()).add(r.user_id)
    return out


def merged_prs_by_repo(pr_rows: list) -> dict[str, list]:
    """`git_artifacts.repo_id` is already canonical (PRs only exist on a real GitHub repo,
    never a `path`-tier local guess) -- no per-user bucketing needed here."""
    out: dict[str, list] = {}
    for r in pr_rows:
        out.setdefault(r.repo_id, []).append(r)
    return out


def _extract(item: dict, dotted_key: str) -> Any:
    """`"spend.usd_micros"` -> `item["spend"]["usd_micros"]` -- a sortable scalar buried one
    level inside a `Money`/similar sub-object, without every caller flattening its rows."""
    value: Any = item
    for part in dotted_key.split("."):
        if value is None:
            return None
        value = value.get(part)
    return value


def sort_desc_none_last(items: list[dict], dotted_key: str) -> list[dict]:
    """A3's table sort: descending by `dotted_key` (a top-level field, or `"field.sub"` for a
    scalar nested one level, e.g. a `Money`'s `usd_micros`), items with no value pushed to
    the end (never treated as a `0` that would rank above a real small positive figure)."""
    with_value = [(_extract(it, dotted_key), it) for it in items]
    have_value = sorted((p for p in with_value if p[0] is not None), key=lambda p: p[0], reverse=True)
    missing = [it for v, it in with_value if v is None]
    return [it for _, it in have_value] + missing


def paginate(items: list[dict], limit: int, offset: int) -> list[dict]:
    return items[offset:offset + limit]


def search_by_name(items: list[dict], q: str | None, name_key: str = "name") -> list[dict]:
    if not q:
        return items
    needle = q.lower()
    return [it for it in items if needle in it[name_key].lower()]


def elapsed_utc_days(frm: datetime, to: datetime, now: datetime) -> list[date]:
    """The UTC calendar days a `[frm, to)` window has lived through by `now`: `frm`'s day up to
    the day of `min(to - 1us, now)`, inclusive of today. `to` is exclusive, so a window ending
    exactly at midnight does not include the next day. A window that has not started yet has no
    elapsed days."""
    first = _aware(frm).astimezone(timezone.utc).date()
    last = min(_aware(to) - timedelta(microseconds=1), _aware(now)).astimezone(timezone.utc).date()
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def daily_spend(rows: list, chain: list[str], frm: datetime, to: datetime,
                now: datetime) -> list[dict]:
    """Overview's spend-over-time series: one `{day, spend_usd_micros}` per elapsed UTC day of
    the window (`elapsed_utc_days`), missing days zero-filled.

    Each row resolves through the cost `chain` on its own (the precedence rule: per session,
    then sum -- unresolved rows contribute nothing, exactly as in `resolve_aggregate`) and is
    bucketed on the UTC day of `max(started_at, frm)`: a session that began before the window is
    counted on the window's first day. A bucket day outside the elapsed range (a session dated
    after `now`, i.e. clock skew or synthetic data) is clamped into the nearest edge day rather
    than dropped, so the series always sums to `resolve_aggregate(rows, chain)` -- the headline
    spend. Only a window with no elapsed day at all (it starts in the future) yields `[]`."""
    days = elapsed_utc_days(frm, to, now)
    if not days:
        return []
    first, last = days[0], days[-1]
    start = _aware(frm)
    totals = dict.fromkeys(days, 0)
    for r in rows:
        resolved = precedence.resolve(r, chain)
        if resolved is None:
            continue
        bucket = max(_aware(r.started_at), start).astimezone(timezone.utc).date()
        totals[min(max(bucket, first), last)] += resolved[0]
    return [{"day": d, "spend_usd_micros": totals[d]} for d in days]


def group_rows_by_agent(rows: list) -> dict[str, list]:
    """Split scope rows by `sessions.agent` (already the vendor/CLI dimension: claude_code |
    codex | cursor | ...; no join needed), every row (roots and sub-agents) into exactly one
    group. The caller resolves each group's cost through the precedence chain -- resolution is
    per row and additive, so the groups' totals sum to the whole-scope total."""
    groups: dict[str, list] = {}
    for r in rows:
        groups.setdefault(r.agent, []).append(r)
    return groups


# ------------------------------------------------------------------------------- D2

def channel_totals(rows: list) -> dict | None:
    """D2: `{carry, recache, output, input}` usd_micros totals across a PR's linked
    sessions -- the same four channels `single_user/service.py::_cost_by_channel_block`
    reads per-session (R7.2), summed here across however many sessions fed one PR.

    `None` (cold-start) when not one row in scope carries both rollups yet. `complete` is
    `False` (not silently dropped) the moment any row is missing either rollup, so a caller
    can tell "every dollar of this total is priced" from "some sessions aren't rolled up
    yet" -- ground rule 3 never lets an incomplete total pass as a whole one.

    Reads the row's own `channel_priced`/`channel_*` scalar attributes (api-latency plan D3,
    person/PR-only fragments, D7) rather than `info.channel_cost`/`info.carry`."""
    totals = {"carry": 0, "recache": 0, "output": 0, "input": 0}
    complete = True
    any_priced = False
    for r in rows:
        if not r.channel_priced:
            complete = False
            continue
        any_priced = True
        totals["carry"] += r.channel_carry_usd_micros or 0
        totals["recache"] += r.channel_recache_usd_micros or 0
        totals["output"] += r.channel_output_usd_micros or 0
        totals["input"] += r.channel_input_usd_micros or 0
    if not any_priced:
        return None
    return {"totals_usd_micros": totals, "complete": complete}


def pre_post_open_cost_split(rows: list, opened_at: datetime) -> dict:
    """D2: a PR's linked-session spend, split at the moment the PR was opened -- "making"
    spend before, "review/rework" spend after. A session with no `started_at` is bucketed
    with the pre-open spend (it can't have started *after* an event with no timestamp to
    compare), documented rather than silently dropped."""
    pre = post = 0
    for r in rows:
        started_at = getattr(r, "started_at", None)
        cost = getattr(r, "cost_effective_usd_micros", 0) or 0
        if started_at is not None and started_at >= opened_at:
            post += cost
        else:
            pre += cost
    return {"pre_open_usd_micros": pre, "post_open_usd_micros": post}


#: D3-benchmark: a repo needs at least this many *other* features with a real spend before a
#: cross-feature percentile means anything. Below it, `feature_benchmark` returns None
#: (cold-start) rather than rank a feature against one or two peers.
_BENCHMARK_MIN_PEERS = 3


def feature_benchmark(features: list[dict], subject_id: str) -> dict | None:
    """D3-benchmark: where the subject feature's spend sits among its repo peers -- the
    "vs similar features" panel. `features` is `quality.list_features_for_repo`'s output (every
    feature in the repo, each already carrying `spend.usd_micros`); "similar" is repo-wide for
    this first cut (a size/label/type cohort is a later refinement). Cost axis only for now --
    a lead-time axis needs per-feature lifecycle timestamps `list_features_for_repo` doesn't
    compute (TODO(D3-benchmark-time)).

    `None` (cold-start) when there aren't enough priced peers to compare against (a repo with
    only this feature, or where no other feature has any spend) -- never a rank against one peer.
    """
    from .. import forecast

    subject = next((ft for ft in features if ft["id"] == subject_id), None)
    if subject is None:
        return None
    peer_costs_micros = [
        (ft["spend"]["usd_micros"] or 0) for ft in features
        if ft["id"] != subject_id and (ft["spend"]["usd_micros"] or 0) > 0
    ]
    if len(peer_costs_micros) < _BENCHMARK_MIN_PEERS:
        return None
    subject_cost_micros = subject["spend"]["usd_micros"] or 0
    cheaper = sum(1 for c in peer_costs_micros if c < subject_cost_micros)
    # percentile rank: share of peers this feature is more expensive than, 0-100.
    cost_percentile = round(100 * cheaper / len(peer_costs_micros))
    # `Dist` is unitless whole dollars (forecast.dist_stats' own contract) -- divide out of micros.
    cost_dist = forecast.dist_stats([c / 1_000_000 for c in peer_costs_micros])
    return {
        "subject_cost_usd_micros": subject_cost_micros,
        "cost_percentile": cost_percentile,
        "peer_count": len(peer_costs_micros),
        "cost_dist": cost_dist,
    }
