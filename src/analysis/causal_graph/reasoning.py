"""Reasoning-token estimation for the collapsed assistant node (design causal-graph-design.md
§4.1, reasoning subsection). Called from `builder._collapse_assistants`, which already has the
whole `message_id` group.

`output_tokens` bills reasoning but never breaks it out. Codex publishes a `reasoning_tokens`
count today; Claude does not, so the number is recovered by subtraction:

    thinking tokens = output_tokens - tokens(visible reply text + tool name/arguments)

**Reported always beats estimated** -- the branch is on `payload.reasoning_tokens` being
non-null, so Codex needs no special case and a future Claude release needs no code change.

Constants and their provenance: docs/design/notes/claude_token_usage.md §7 (the subtraction
is exact, checked against Anthropic's own recount), §8 (the tiktoken-to-Claude ratio, per
model family), §10 (the fixed per-tool-call block). All of it was measured on Claude; vendor
isolation is `config.py`'s job, not this module's -- `estimate_reasoning` only ever asks the
config for the vendor it was handed.

Negatives are floored at 0 (measured ~6.6% of responses, where true reasoning is near zero and
estimator error crosses it). Floor, never silently: every estimate and every floor is counted
into `common.metrics` (`causal_graph.reasoning.estimated` / `causal_graph.reasoning.floored`,
labeled by vendor) rather than a bespoke stats object threaded through the builder -- the
negative-floor rate is `floored / estimated` read back from a `metrics()` snapshot, the same
place every other anomaly in this package reports (plan Step 3's "one counter idiom, not two";
previously a `ReasoningStats` class built per `build_graph` call and logged once at the end --
folded in here so the package no longer carries two ways to count the same kind of thing).
"""
from __future__ import annotations

from typing import Any

from common import metrics

from .config import ReasoningConfig

__all__ = ["estimate_reasoning"]

# content_kind values (normalized_schema.json, assistant_message) that carry visible reply
# text, as opposed to pure thinking or pure tool_use. Used only to decide whether the
# text-alongside-tool-calls framing block (§10, +13) applies.
_TEXT_CONTENT_KINDS = frozenset({"text", "mixed"})


def _sum_numeric(payloads: list[dict], field: str) -> int:
    total = 0
    for p in payloads:
        val = p.get(field)
        if isinstance(val, (int, float)):
            total += val
    return int(total)


def _has_text(payloads: list[dict]) -> bool:
    return any(p.get("content_kind") in _TEXT_CONTENT_KINDS for p in payloads)


def estimate_reasoning(
    group_payloads: list[dict],
    fb_payload: dict,
    *,
    model: str | None,
    vendor: str,
    config: ReasoningConfig,
) -> dict[str, Any]:
    """The estimate plus its ingredients, for one `message_id` group.

    ``fb_payload`` is the group's `first_block_of_message=True` record -- authoritative for
    `output_tokens` / `reasoning_tokens`, the same rule the builder already applies to the
    other usage fields (response-scoped, replicated across the group). ``group_payloads`` is
    every record's payload in the group, for the two ingredients that are per-record:
    `estimated_visible_tokens` and `tool_call_count`.

    Returns the five new assistant attrs: `estimated_reasoning_tokens`, `reasoning_source`
    (`reported | estimated | None`), `reasoning_ratio` (the ratio actually applied, or `None`
    when not applicable), plus the two ingredients (always populated, even on the reported
    path, so a ratio change can recompute from the graph alone).
    """
    visible_sum = _sum_numeric(group_payloads, "estimated_visible_tokens")
    calls_sum = _sum_numeric(group_payloads, "tool_call_count")
    ingredients = {"estimated_visible_tokens": visible_sum, "tool_call_count": calls_sum}

    reported = fb_payload.get("reasoning_tokens")
    if reported is not None:
        return {
            "estimated_reasoning_tokens": reported,
            "reasoning_source": "reported",
            "reasoning_ratio": None,
            **ingredients,
        }

    output_tokens = fb_payload.get("output_tokens")
    if not isinstance(output_tokens, (int, float)):
        # Contract guard's territory (builder.py), not this module's: an upstream field
        # actually missing yields null attrs, never a crash here.
        return {
            "estimated_reasoning_tokens": None,
            "reasoning_source": None,
            "reasoning_ratio": None,
            **ingredients,
        }

    cfg = config.for_vendor(vendor)  # no section -> ratio 1.0, zero framing (config.py)
    ratio = cfg.ratio_for(model)

    framing = 0
    if calls_sum > 0:
        framing = cfg.tool_call_tokens * calls_sum + cfg.tool_call_offset
        if _has_text(group_payloads):
            framing += cfg.text_with_tools_tokens

    raw_value = output_tokens - ratio * visible_sum - framing
    metrics.counter("causal_graph.reasoning.estimated", vendor=vendor)
    if raw_value < 0:
        metrics.counter("causal_graph.reasoning.floored", vendor=vendor)
    value = int(round(max(0.0, raw_value)))

    return {
        "estimated_reasoning_tokens": value,
        "reasoning_source": "estimated",
        "reasoning_ratio": ratio,
        **ingredients,
    }
