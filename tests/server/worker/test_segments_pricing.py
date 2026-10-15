"""compute_segments pricing wire-through (segment-level-pricing plan Phase 1) + what reads it
downstream (`savings_usd_micros` in `aggregate.model_fit`; rework is a signal, not a
recoverable-spend bucket -- efficiency note §2).

`build_segments` (`worker/segments/beats.py`) already prices each segment's own requests in
full when handed a `Pricer` -- the pre-Phase-1 bug was `compute_segments` always
calling it with `None`, so every real fold's `cost_usd_micros`/`carry_usd_micros` came back
null. This locks in the fix (mirrors `test_graph_fold_pricing.py`'s unpriced/priced/
all-or-nothing shape) and proves the two aggregate.py buckets that depended on it -- unpriced
$0 before, real dollars after -- with no code change to aggregate.py itself.
"""
from datetime import date

from common.model_pricing import Pricer
from server.api.config import ApiConfig, ModelFitConfig
from server.api.manager import aggregate as agg
from server.storage import session_scalars
from server.worker.segments import compute_segments

PRICING = Pricer.load(None, None)  # the real catalog -- claude-opus-5: $5/$25/$0.50/$6.25/$10 per M
AT = date(2026, 1, 1)  # after every block's effective_from in the real table


def _assistant(ts: str, model: str, input_tokens=0, output_tokens=0, cache_read=0, cache_write=0) -> dict:
    return {
        "event_type": "assistant_message", "event_ts": ts,
        "payload": {
            "model": model, "input_tokens": input_tokens, "output_tokens": output_tokens,
            "cache_read_tokens": cache_read, "cache_write_tokens": cache_write,
        },
    }


def test_compute_segments_is_unpriced_without_a_pricing_table():
    """The pre-fix baseline, still the default: no `pricing`/`at` given -- `None`, matching
    every existing call site and test fixture that predates pricing."""
    events = [_assistant("2026-01-01T00:00:00Z", "claude-opus-5", 1_000_000)]
    segments = compute_segments(events, [], None)
    assert len(segments) == 1
    assert segments[0]["cost_usd_micros"] is None
    assert segments[0]["carry_usd_micros"] is None


def test_compute_segments_prices_a_single_segment_when_given_pricing():
    events = [_assistant("2026-01-01T00:00:00Z", "claude-opus-5",
                         input_tokens=1_000_000, output_tokens=200_000,
                         cache_read=2_000_000, cache_write=100_000)]
    segments = compute_segments(events, [], None, PRICING, AT)
    assert len(segments) == 1
    # input $5 + output $5 + cache_read $1 (@ $0.50/M) + cache_write $1 (@ $10/M, no TTL detail)
    assert segments[0]["cost_usd_micros"] == 12_000_000
    assert segments[0]["carry_usd_micros"] == 1_000_000  # cache_read alone, @ $0.50/M


def test_compute_segments_accumulates_per_model_breakdown():
    """X4/model-fit-per-segment: a segment run partly on one model and partly on another gets
    a per-model breakdown, summing to the same total the old single `cost_usd_micros` figure
    already reported (D1 context-curve/reasoning-rollup precedent: keep the per-turn series
    that was already computed instead of discarding it)."""
    events = [
        _assistant("2026-01-01T00:00:00Z", "claude-opus-5", input_tokens=1_000_000),
        _assistant("2026-01-01T00:00:10Z", "claude-sonnet-5", input_tokens=2_000_000),
    ]
    segments = compute_segments(events, [], None, PRICING, AT)
    assert len(segments) == 1
    seg = segments[0]
    assert set(seg["models"]) == {"claude-opus-5", "claude-sonnet-5"}
    assert seg["models"]["claude-opus-5"]["input"] == 1_000_000
    assert seg["models"]["claude-sonnet-5"]["input"] == 2_000_000
    assert sum(m["cost_usd_micros"] for m in seg["models"].values()) == seg["cost_usd_micros"]


def test_compute_segments_models_is_none_when_unpriced():
    events = [_assistant("2026-01-01T00:00:00Z", "claude-opus-5", input_tokens=1_000_000)]
    segments = compute_segments(events, [], None)
    assert segments[0]["models"] is None


def test_compute_segments_is_all_or_nothing_within_a_segment():
    """One priceable request and one unpriced (unknown model) request in the same segment --
    the segment's cost is `None`, never a partial sum (ground rule 3), same posture
    `build_segments`'s own docstring already commits to."""
    events = [
        _assistant("2026-01-01T00:00:00Z", "claude-opus-5", input_tokens=1_000_000),
        _assistant("2026-01-01T00:05:00Z", "some-future-model-nobody-priced-yet", input_tokens=500),
    ]
    segments = compute_segments(events, [], None, PRICING, AT)
    assert len(segments) == 1
    assert segments[0]["cost_usd_micros"] is None
    assert segments[0]["carry_usd_micros"] is None


def _block(ts: str, message_id: str, first_block: bool, **usage) -> dict:
    """One content-block record of a multi-block response, the real Claude Code shape: every
    record repeats the whole response's usage (`usage_scope: "response"`)."""
    event = _assistant(ts, "claude-opus-5", **usage)
    event["payload"].update({"message_id": message_id, "first_block_of_message": first_block,
                             "usage_scope": "response"})
    return event


def test_compute_segments_prices_a_multi_block_response_once():
    """A thinking + text + tool_use response is three records carrying the same usage -- it is
    billed once, not three times (the over-count that put segment totals at ~2x session cost)."""
    usage = dict(input_tokens=1_000_000, output_tokens=200_000, cache_read=2_000_000, cache_write=100_000)
    events = [
        _block("2026-01-01T00:00:00Z", "msg_1", True, **usage),
        _block("2026-01-01T00:00:01Z", "msg_1", False, **usage),
        _block("2026-01-01T00:00:02Z", "msg_1", False, **usage),
    ]
    seg = compute_segments(events, [], None, PRICING, AT)[0]
    assert seg["cost_usd_micros"] == 12_000_000  # same as the single-record case above
    assert seg["carry_usd_micros"] == 1_000_000
    assert seg["models"]["claude-opus-5"]["output"] == 200_000


def test_compute_segments_dedupes_across_responses_but_not_within_distinct_ones():
    events = [
        _block("2026-01-01T00:00:00Z", "msg_1", True, input_tokens=1_000_000),
        _block("2026-01-01T00:00:01Z", "msg_1", False, input_tokens=1_000_000),
        _block("2026-01-01T00:00:05Z", "msg_2", True, input_tokens=1_000_000),
        _assistant("2026-01-01T00:00:06Z", "claude-opus-5", input_tokens=1_000_000),  # no message_id
    ]
    seg = compute_segments(events, [], None, PRICING, AT)[0]
    assert seg["models"]["claude-opus-5"]["input"] == 3_000_000
    assert seg["cost_usd_micros"] == 15_000_000  # 3M input @ $5/M


def test_compute_segments_skips_a_zero_usage_record_on_an_unpriced_model():
    """Claude Code's `<synthetic>` marker messages carry no usage and a model with no price; one of
    them must not void an otherwise fully priced segment."""
    events = [
        _assistant("2026-01-01T00:00:00Z", "claude-opus-5", input_tokens=1_000_000),
        _assistant("2026-01-01T00:00:05Z", "<synthetic>"),
    ]
    seg = compute_segments(events, [], None, PRICING, AT)[0]
    assert seg["cost_usd_micros"] == 5_000_000
    assert set(seg["models"]) == {"claude-opus-5"}


# ------------------------------------------------------------------- Phase 3a (aggregate free wins)

def _file_change(ts: str, path: str) -> dict:
    return {"event_type": "file_change", "event_ts": ts, "payload": {"path": path}}


def _explore_spawn(ts: str) -> dict:
    return {"event_type": "sub_agent_spawn", "event_ts": ts, "payload": {"agent_type": "explore"}}


def _row(**kw):
    from types import SimpleNamespace
    defaults = dict(cost_effective_usd_micros=0, info={}, models={}, files=None)
    defaults.update(kw)
    scalars = session_scalars.fold_scalars(defaults["info"], defaults["files"])
    return SimpleNamespace(**{**scalars, **defaults})


def test_revision_loop_segments_are_priced_but_rework_is_not_recoverable_spend():
    """Revision-loop segments carry real priced `cost_usd_micros` (it was `None` before Phase 1),
    but rework is a coaching signal, not a recoverable-spend bucket (efficiency note §2,
    `TODO(rework-detector)` in aggregate.py) -- so `recoverable()` counts none of it.

    `model_fit`'s own verdict here is `good`, not the `over` this fixture asserted before the
    model-fit-reasoning-demand-rework plan: COMPREHEND is now a high-demand label (it produces
    little code but is genuinely reasoning-heavy work), so a `high`-tier model spending on it
    is no longer misread as oversized -- exactly the bug that plan exists to fix.

    Two segments, split by the `delegation` beat (a `sub_agent_spawn` is a strong/boundary
    beat, R4.2):
      - segment 0: three `file_change`s on the same path -> a `revision_loop` beat (R4.1,
        `revision_loop_min_edits=3`) -- priced via its own assistant_message. High-demand (a
        rework signal), so the session's `high` tier (claude-opus-5) is a match.
      - segment 1: an `explore` subagent spawn -> Tier-A fact-tier `COMPREHEND` label
        (`session_type.py::_fact_tier_label`) -- high-demand by label alone.
    """
    events = [
        _file_change("2026-01-01T00:00:00Z", "a.py"),
        _file_change("2026-01-01T00:00:10Z", "a.py"),
        _file_change("2026-01-01T00:00:20Z", "a.py"),
        _assistant("2026-01-01T00:00:30Z", "claude-opus-5", input_tokens=100_000),  # segment 0's cost
        _explore_spawn("2026-01-01T00:10:00Z"),
        _assistant("2026-01-01T00:10:10Z", "claude-opus-5", input_tokens=50_000),  # segment 1's cost
    ]
    segments = compute_segments(events, [], None, PRICING, AT)
    assert len(segments) == 2
    assert agg.segment_has_beat(segments[0], "revision_loop")
    assert segments[0]["cost_usd_micros"] == 500_000  # 100k input tokens @ $5/M
    assert segments[1]["label"] == "COMPREHEND"
    assert segments[1]["cost_usd_micros"] == 250_000  # 50k input tokens @ $5/M

    info = {"segments": segments}
    row = _row(cost_effective_usd_micros=750_000, info=info, models={"claude-opus-5": {"input": 150_000}})

    breakdown = agg.recoverable([row])
    assert "rework" not in breakdown["buckets_usd_micros"]
    assert breakdown["recoverable_usd_micros"] == 0

    fit = agg.model_fit(row, ApiConfig())
    assert fit["verdict"] == "good"
    assert [s["fit"] for s in fit["segments"]] == ["good", "good"]
    assert fit["savings_usd_micros"] == 0


# --------------------------------------------------------- X4/model-fit-per-segment

def test_segment_tier_uses_the_segments_own_models_when_present():
    seg = {"models": {"claude-opus-5": {"input": 100, "output": 0, "cache_read": 0, "cache_write": 0}}}
    assert agg._segment_tier(seg, session_tier="low", config=ModelFitConfig()) == "high"


def test_segment_tier_falls_back_to_session_tier_when_models_absent():
    assert agg._segment_tier({"models": None}, session_tier="low", config=ModelFitConfig()) == "low"
    assert agg._segment_tier({}, session_tier="mid", config=ModelFitConfig()) == "mid"


def test_model_fit_surfaces_both_over_and_under_segments_with_under_winning():
    """The exact case a single session-wide tier made structurally impossible before this
    rework (model-fit-reasoning-demand-rework's own TODO marker, now removed): segment 0 runs
    a high-tier model on mechanical work (-> over), segment 1 runs a low-tier model on
    high-demand work (-> under) -- both now reachable in one session, with `under` winning
    the session-level verdict (X4's own documented precedence)."""
    events = [
        # segment 0: high tier (opus), no demand signal at all -> MISC/low-demand -> "over".
        _assistant("2026-01-01T00:00:00Z", "claude-opus-5", input_tokens=100_000),
        # boundary (a sub_agent_spawn is a strong/boundary beat, R4.2)
        _explore_spawn("2026-01-01T00:10:00Z"),
        # segment 1: low tier (haiku), COMPREHEND label -> high-demand by label alone -> "under".
        _assistant("2026-01-01T00:10:10Z", "claude-haiku-4-5-20251001", input_tokens=50_000),
    ]
    segments = compute_segments(events, [], None, PRICING, AT)
    assert len(segments) == 2
    assert segments[1]["label"] == "COMPREHEND"

    # Session-level dominant model would itself resolve to "high" -- irrelevant now that both
    # segments carry their own `models` and compare against their own tier instead.
    row = _row(cost_effective_usd_micros=segments[0]["cost_usd_micros"] + segments[1]["cost_usd_micros"],
              info={"segments": segments}, models={"claude-opus-5": {"input": 100_000}})
    fit = agg.model_fit(row, ApiConfig(), PRICING)
    assert [s["fit"] for s in fit["segments"]] == ["over", "under"]
    assert fit["verdict"] == "under"
