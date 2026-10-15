"""Per-session request ledger simulation (plan §5 P7). This is what makes I1 exactness survive
all the way down to individual requests: every model's five token-type
totals (`input`, `cache_write_5m`, `cache_write_1h`, `cache_read`, `output`) (from `SessionPlan.models`) is conserved exactly, only ever redistributed across the
session's simulated requests -- never changed.

**Domain separation (I2b).** Exactly two kinds of randomness are involved, deliberately kept on
separate `rng_for` streams so a habits-only config change can never perturb the base token math:
- the `requests` stream decides request count, the model/turn partition, the base context-shape
  curve, gap magnitudes, and the per-position `lognormvariate` output-allocation weights;
- the four `habit_*` streams decide ONLY which positions become `bust`/`resume`/`post_compaction`
  /`interrupted` (the *kind* label) -- never a token total.

**Implementation judgment calls** (the plan specifies the algorithm at the formula level; a few
sequencing details are this module's own coherent reading, flagged here and in the progress
log):
- Compactions/resumes/busts can collide on the same request position; priority order is
  compaction > resume > bust (a request already forced into one kind never gets relabeled).
- Gap *magnitudes* (including for resume/bust positions) are drawn from the `requests` stream,
  since kind selection (which stream decides WHETHER a position is a resume/bust) and gap
  duration (a timing/magnitude concern, explicitly grouped under the `requests` domain's own
  description "request count, context profile, turns, gaps" in plan §4) are different concerns.
- When resume gaps are dropped to fit the 80% budget, the LATEST resume position (highest
  index) is dropped first, and steps 4-5 (shape + allocation) are recomputed from scratch each
  time -- never a partial patch of the previous shape.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Mapping

from ..allocation import allocate_integer
from ..errors import InputDataError
from ..randomness import rng_for
from .config import ResolvedOrg, SessionsDefaults
from .sessions import SessionPlan

_MAX_CONTEXT_ITERATIONS = 20
_KINDS = ("normal", "bust", "resume", "first", "post_compaction")


@dataclass
class Request:
    index: int
    ts: datetime
    model: str
    input: int
    cache_read: int
    cache_write_5m: int
    cache_write_1h: int
    output: int
    turn: int
    kind: str
    interrupted: bool = False

    @property
    def cache_write(self) -> int:
        """Combined writes: what `session_info.models` and the event pricing carry."""
        return self.cache_write_5m + self.cache_write_1h


@dataclass
class Compaction:
    position: int
    ts: datetime
    trigger: str
    before_tokens: int
    after_tokens: int
    duration_ms: int


@dataclass
class Ledger:
    requests: list[Request] = field(default_factory=list)
    turn_count: int = 0
    compactions: list[Compaction] = field(default_factory=list)


def _geometric(r, mean: float) -> int:
    """1-based geometric draw with the given mean, via inverse-CDF sampling."""
    p = min(max(1.0 / max(mean, 1e-6), 1e-6), 1.0)
    if p >= 1.0:
        return 1
    u = r.random()
    return max(1, math.ceil(math.log(1 - u) / math.log(1 - p)))


def _model_positions(resolved: ResolvedOrg, totals: Mapping[str, Mapping[str, int]], R: int, r) -> list[str]:
    weights = {m: sum(cats.values()) for m, cats in totals.items() if sum(cats.values()) > 0}
    counts = allocate_integer(R, weights)
    # Every model with tokens gets >= 1 position, stealing from whichever currently has the most.
    zeroed = [m for m in weights if counts[m] == 0]
    for m in zeroed:
        donor = max(counts, key=lambda k: counts[k])
        counts[donor] -= 1
        counts[m] += 1
    dominant = max(weights, key=lambda m: weights[m])
    order = [dominant] + sorted(m for m in weights if m != dominant)
    model_at_pos: list[str] = []
    for m in order:
        model_at_pos.extend([m] * counts[m])
    return model_at_pos


def _turn_sizes(r, R: int, mean_requests: float) -> list[int]:
    sizes = []
    remaining = R
    while remaining > 0:
        size = min(remaining, _geometric(r, mean_requests))
        sizes.append(size)
        remaining -= size
    return sizes


def _build_shape(resolved: ResolvedOrg, sid: str, R: int, turn_sizes: list[int],
                 cfg: SessionsDefaults, habits, extra_compaction_at: int | None,
                 base_r) -> tuple[list[float], list[str], list[int], list[dict]]:
    """Steps 3-4 (turns already sized): context curve `c`, per-position `kind`, `turn` index per
    position, and the resolved compaction list (position + trigger)."""
    seed = resolved.config.org.seed
    c = [0.0] * R
    c[0] = base_r.uniform(0.1, 0.3)
    for i in range(1, R):
        c[i] = c[i - 1] + base_r.expovariate(1.0) * 0.05

    turn_of = [0] * R
    pos = 0
    for t, size in enumerate(turn_sizes):
        for _ in range(size):
            turn_of[pos] = t
            pos += 1

    kind = ["normal"] * R
    kind[0] = "first"
    compactions_meta: list[dict] = []

    compact_r = rng_for(seed, "habit_compact", sid)
    mean_comp = habits.compactions_per_100_requests * R / 100
    k_comp = _poisson(mean_comp, compact_r)
    candidates = list(range(3, R)) if R > 3 else []
    compact_r.shuffle(candidates)
    positions = sorted(candidates[:k_comp])
    if extra_compaction_at is not None and extra_compaction_at not in positions and extra_compaction_at >= 3:
        positions = sorted(positions + [extra_compaction_at])
    for p in positions:
        if kind[p] != "normal" and kind[p] != "first":
            continue
        if p == 0:
            continue
        c[p] = compact_r.uniform(0.08, 0.2)
        kind[p] = "post_compaction"
        trigger = "manual" if compact_r.random() < habits.manual_compaction_share else "auto"
        compactions_meta.append({"position": p, "trigger": trigger})

    resume_r = rng_for(seed, "habit_resume", sid)
    turn_starts = []
    pos = 0
    for size in turn_sizes:
        turn_starts.append(pos)
        pos += size
    for t_idx, start in enumerate(turn_starts):
        if t_idx == 0:
            continue
        if kind[start] not in ("normal",):
            continue
        if resume_r.random() < habits.resume_prob_per_turn:
            kind[start] = "resume"

    bust_r = rng_for(seed, "habit_bust", sid)
    for i in range(1, R):
        if kind[i] != "normal":
            continue
        if bust_r.random() < habits.cache_bust_per_100_requests / 100:
            kind[i] = "bust"

    return c, kind, turn_of, compactions_meta


def _poisson(mean: float, r) -> int:
    if mean <= 0:
        return 0
    limit = math.exp(-mean)
    count = 0
    product = 1.0
    while True:
        product *= r.random()
        if product <= limit:
            return count
        count += 1


def simulate(resolved: ResolvedOrg, plan: SessionPlan, cfg, habits, pricer=None, *, log=None) -> Ledger:
    """`cfg` is the team's `SessionsDefaults` block, `habits` its `HabitsDefaults` block."""
    seed = resolved.config.org.seed
    sid = plan.session_id
    totals = plan.models
    base_r = rng_for(seed, "requests", sid)

    mean_ctx = base_r.uniform(cfg.mean_context_tokens.min, cfg.mean_context_tokens.max)
    # A request's context is its cache reads plus its new writes, so the request count is
    # derived from both (a session with no reads but large writes must not get R=2).
    context_total_all = sum(cats["cache_read"] + cats["cache_write_5m"] + cats["cache_write_1h"]
                            for cats in totals.values())
    n_models_with_tokens = sum(1 for cats in totals.values() if sum(cats.values()) > 0)
    R = max(2, min(1500, round(context_total_all / mean_ctx) if mean_ctx else 2))
    R = max(R, n_models_with_tokens)

    extra_compaction_at: int | None = None
    for _iteration in range(_MAX_CONTEXT_ITERATIONS):
        model_at_pos = _model_positions(resolved, totals, R, base_r)
        turn_sizes = _turn_sizes(base_r, R, cfg.turn_size_mean_requests)
        c, kind, turn_of, compactions_meta = _build_shape(
            resolved, sid, R, turn_sizes, cfg, habits, extra_compaction_at, base_r)

        positions_by_model: dict[str, list[int]] = {}
        for i, m in enumerate(model_at_pos):
            positions_by_model.setdefault(m, []).append(i)

        cr_shape = [0.0] * R
        cw_shape = [0.0] * R
        for i in range(R):
            if kind[i] == "normal":
                cr_shape[i] = c[i - 1] if i > 0 else 0.0
                cw_shape[i] = max(c[i] - (c[i - 1] if i > 0 else 0.0), 0.002)
            else:
                cr_shape[i] = 0.0
                cw_shape[i] = c[i]

        cr_by_pos = [0] * R
        cw5_by_pos = [0] * R
        cw1_by_pos = [0] * R
        input_by_pos = [0] * R
        output_by_pos = [0] * R

        for m, positions in positions_by_model.items():
            cats = totals[m]

            cr_weights = {f"r{i:05d}": cr_shape[i] for i in positions}
            if not any(cr_weights.values()):
                cr_weights = {k: 1.0 for k in cr_weights}
            cr_alloc = allocate_integer(cats["cache_read"], cr_weights)

            # Writes are given by Layer 1; `cw_shape` only decides WHICH request gets how
            # much. 5m and 1h are allocated over the requests separately with the same weights.
            cw_weights = {f"r{i:05d}": cw_shape[i] for i in positions}
            if not any(cw_weights.values()):
                cw_weights = {k: 1.0 for k in cw_weights}
            cw5_alloc = allocate_integer(cats["cache_write_5m"], cw_weights)
            cw1_alloc = allocate_integer(cats["cache_write_1h"], cw_weights)

            # No minimum input per request: some requests may get 0.
            input_weights = {f"r{i:05d}": 1.0 for i in positions}
            input_alloc = allocate_integer(cats["input"], input_weights)

            output_weights = {f"r{i:05d}": base_r.lognormvariate(0, 0.8) for i in positions}
            output_alloc = allocate_integer(cats["output"], output_weights)

            for i in positions:
                key = f"r{i:05d}"
                cr_by_pos[i] = cr_alloc[key]
                cw5_by_pos[i] = cw5_alloc[key]
                cw1_by_pos[i] = cw1_alloc[key]
                input_by_pos[i] = input_alloc[key]
                output_by_pos[i] = output_alloc[key]

        cw_by_pos = [cw5_by_pos[i] + cw1_by_pos[i] for i in range(R)]
        peak = max((cr_by_pos[i] + cw_by_pos[i] for i in range(R)), default=0)
        if peak <= cfg.context_window_tokens:
            break
        extra_compaction_at = max(range(R), key=lambda i: cr_by_pos[i] + cw_by_pos[i])
    else:
        raise InputDataError(f"session {sid}: context window exceeded after "
                             f"{_MAX_CONTEXT_ITERATIONS} compaction iterations")

    # ---- Step 7: timestamps ----
    gaps = [0.0] * R  # gap BEFORE request i (i=0 has no gap)
    resume_positions = [i for i in range(R) if kind[i] == "resume"]
    for i in range(1, R):
        if kind[i] == "resume":
            gaps[i] = cfg.ttl_s + 60 + base_r.uniform(0, 120)
        elif kind[i] == "bust":
            gaps[i] = min(base_r.uniform(2, 40), cfg.ttl_s * 0.99)
        elif turn_of[i] != turn_of[i - 1]:
            gaps[i] = base_r.uniform(30, 600)
        else:
            gaps[i] = base_r.uniform(2, 40)

    planned = plan.planned_duration_s
    while resume_positions:
        resume_total = sum(gaps[i] for i in resume_positions)
        if resume_total <= 0.8 * planned:
            break
        drop = max(resume_positions)
        kind[drop] = "normal"
        gaps[drop] = base_r.uniform(30, 600)  # falls back to an inter/intra-turn-shaped gap
        resume_positions.remove(drop)

    resume_total = sum(gaps[i] for i in resume_positions)
    non_resume_positions = [i for i in range(1, R) if i not in resume_positions]
    non_resume_total = sum(gaps[i] for i in non_resume_positions)
    budget = max(0.0, planned - resume_total)
    if non_resume_total > 0:
        scale = budget / non_resume_total
        for i in non_resume_positions:
            gaps[i] *= scale
            if kind[i] == "bust":
                gaps[i] = min(gaps[i], cfg.ttl_s * 0.99)

    interrupt_r = rng_for(seed, "habit_interrupt", sid)
    turn_last_pos = {}
    for i in range(R):
        turn_last_pos[turn_of[i]] = i
    interrupted = [False] * R
    for last_pos in turn_last_pos.values():
        if interrupt_r.random() < habits.interrupt_prob_per_turn:
            interrupted[last_pos] = True

    ts = [plan.started_at] * R
    cursor = plan.started_at
    for i in range(R):
        cursor = cursor + timedelta(seconds=gaps[i])
        ts[i] = cursor
    if ts and ts[-1] > plan.ended_at:
        # Renormalize into [started_at, ended_at] exactly (rounding-only drift, never a design
        # overflow -- the scale step above targets `planned` == the session's own duration).
        span = (plan.ended_at - plan.started_at).total_seconds()
        total = (ts[-1] - plan.started_at).total_seconds()
        if total > 0:
            factor = span / total
            cursor = plan.started_at
            for i in range(R):
                gaps[i] *= factor
                cursor = cursor + timedelta(seconds=gaps[i])
                ts[i] = cursor
    # Defensive final clamp: float drift from the scaling above must never let a timestamp
    # cross the session's own boundary, even by a microsecond.
    ts = [min(max(t, plan.started_at), plan.ended_at) for t in ts]

    requests = []
    for i in range(R):
        requests.append(Request(
            index=i, ts=ts[i], model=model_at_pos[i], input=input_by_pos[i],
            cache_read=cr_by_pos[i], cache_write_5m=cw5_by_pos[i],
            cache_write_1h=cw1_by_pos[i], output=output_by_pos[i],
            turn=turn_of[i], kind=kind[i], interrupted=interrupted[i]))

    compactions: list[Compaction] = []
    for meta in compactions_meta:
        p = meta["position"]
        before = (cr_by_pos[p - 1] + cw_by_pos[p - 1]) if p > 0 else 0
        after = cr_by_pos[p] + cw_by_pos[p]
        dur_r = rng_for(seed, "habit_compact", sid, "duration", str(p))
        compactions.append(Compaction(
            position=p, ts=ts[p], trigger=meta["trigger"], before_tokens=before,
            after_tokens=after, duration_ms=int(dur_r.uniform(20_000, 150_000))))

    turn_count = len(turn_sizes)
    return Ledger(requests=requests, turn_count=turn_count, compactions=compactions)
