"""Session segmentation: split a session into phases (beats find the boundaries, R4) and give each
phase its own Tier-0 label (R5 scoring model). Vendored from the retired EconFold fold's `beats.py` +
`session_type.py` (behavior-preserving) so GraphFold can drive the same proven logic from the graph
turn model -- see `graph_fold.info_of`. The library is events + turns based (no graph coupling); it
takes the graph-derived `turns_list`, so its output is graph-native (accepted wholesale by the A/B,
like the turn model itself, ledger #7)."""
from __future__ import annotations

from datetime import datetime

from common.model_pricing import Pricer

from .beats import beats_in_range, build_segments, detect_beats
from .config import _DEFAULT_GIT_COMMANDS, _DEFAULT_REVIEW_AGENT_TYPES, _DEFAULT_VERIFIERS, BeatsConfig, SessionTypeConfig
from .session_type import classify_segments
from ._util import segment_repo_key

__all__ = ["compute_segments"]

_BEATS = BeatsConfig()
_SESSION_TYPE = SessionTypeConfig()


def compute_segments(events: list[dict], turns_list: list[dict], repo: dict | None,
                     pricing: Pricer | None = None, at: datetime | None = None) -> list[dict]:
    """info.segments (R4+R5): beats find the boundaries, each span gets its own Tier-0 label + a
    per-segment repo attribution. `turns_list` is the graph turn model (turns_of); `repo` is
    graph_fold.repo_info(events). `pricing`/`at` (the session's own date) are the same pair
    `info_of` threads into `channel_cost_of`/`carry_of` -- passed straight to `build_segments`
    (its `pricing` param), which already prices each segment's own requests at their own
    event timestamps when given a table; `None`/`None` (the default) keeps every existing call
    site's `cost_usd_micros`/`carry_usd_micros` at `None`, unchanged."""
    all_beats = detect_beats(events, turns_list, _BEATS, _DEFAULT_VERIFIERS)
    built = build_segments(events, turns_list, all_beats, _BEATS, pricing)
    slices = [
        (segment, segment_events, beats_in_range(all_beats, segment["t0"], segment["t1"]))
        for segment, segment_events in built
    ]
    classifications = classify_segments(
        slices, _SESSION_TYPE, _DEFAULT_REVIEW_AGENT_TYPES, _DEFAULT_VERIFIERS, _DEFAULT_GIT_COMMANDS,
    )
    segments = []
    for (segment, segment_events, _segment_beats), classification in zip(slices, classifications):
        repo_key = segment_repo_key(segment_events, repo)
        segments.append({**segment, **classification, "repo_key": repo_key})
    return segments
