"""Config for the causal graph's reasoning-token estimate. Layering: defaults here -> YAML,
mirroring `collector/normalizer/config.py`.

**Keyed by vendor first.** Every constant below was measured on Claude and none of it
transfers -- Codex runs OpenAI models, where `o200k_base` *is* the native tokenizer (ratio
1.0 is exact there, not a fallback), and its tool framing is a different, unmeasured shape. A
flat config would apply Claude's 27-token block to GPT output and call the result a
measurement. See docs/design/notes/claude_token_usage.md §7, §8, §10 for provenance.

**A vendor with no section does not get Claude's constants.** `for_vendor` returns a bare
`VendorReasoningConfig()` -- ratio 1.0, zero framing -- for anything not in `vendors`, same
rule as an unmeasured model within a known vendor (`ratio_for` falls through to
`ratio_default`). Where we have not measured, make no correction, so the estimate lands as an
upper bound rather than a confident wrong number.
"""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

__all__ = ["VendorReasoningConfig", "ReasoningConfig", "default_config", "load_config"]


class VendorReasoningConfig(BaseModel):
    """One vendor's reasoning-estimate constants. Defaults are the "unmeasured" posture --
    ratio 1.0, zero framing -- so a vendor with no explicit section behaves identically to one
    that is declared but has nothing measured yet."""

    # model name -> tiktoken-to-native ratio, longest-prefix matched (see ratio_for).
    ratio_by_model: dict[str, float] = Field(default_factory=dict)
    ratio_default: float = 1.0  # unmeasured model: tiktoken raw, unconverted
    tool_call_tokens: int = 0  # fixed block per tool call
    tool_call_offset: int = 0  # the constant term alongside tool_call_tokens * n_calls
    text_with_tools_tokens: int = 0  # text block sitting beside tool calls

    def ratio_for(self, model: str | None) -> float:
        """Longest matching prefix in `ratio_by_model`; `ratio_default` otherwise."""
        if not model:
            return self.ratio_default
        best_prefix_len = -1
        best_ratio = self.ratio_default
        for prefix, ratio in self.ratio_by_model.items():
            if model.startswith(prefix) and len(prefix) > best_prefix_len:
                best_prefix_len = len(prefix)
                best_ratio = ratio
        return best_ratio


# Measured on Claude only (docs/design/notes/claude_token_usage.md §7, §8, §10). 1.52 for the
# Claude 5 family is corpus/prose, +/- 6%; 1.13 for Sonnet 4.6 was checked directly against
# Anthropic's own tokenizer. 27 / -1 / 13 are exact on Sonnet 4.6, n = 1..5.
_DEFAULT_CLAUDE_CODE = VendorReasoningConfig(
    ratio_by_model={
        "claude-opus-5": 1.52,
        "claude-sonnet-5": 1.52,
        "claude-opus-4-8": 1.52,
        "claude-fable-5": 1.52,
        "claude-sonnet-4-6": 1.13,
    },
    ratio_default=1.0,
    tool_call_tokens=27,
    tool_call_offset=-1,
    text_with_tools_tokens=13,
)

# Codex publishes a reasoning count today, so estimation never runs for it -- present so the
# vendor is declared rather than silently defaulted, and so the numbers are here if it ever
# stops. o200k_base IS the native tokenizer for OpenAI models: ratio 1.0 is exact, not a
# fallback. Tool framing is unmeasured on this vendor.
_DEFAULT_CODEX = VendorReasoningConfig(
    ratio_default=1.0,
    tool_call_tokens=0,
    tool_call_offset=0,
    text_with_tools_tokens=0,
)


class ReasoningConfig(BaseModel):
    vendors: dict[str, VendorReasoningConfig] = Field(
        default_factory=lambda: {
            "claude_code": _DEFAULT_CLAUDE_CODE.model_copy(deep=True),
            "codex": _DEFAULT_CODEX.model_copy(deep=True),
        }
    )

    def for_vendor(self, vendor: str) -> VendorReasoningConfig:
        """No section for `vendor` -> ratio 1.0, zero framing. Never falls through to
        `claude_code` -- that is the failure that would be invisible in the output, a
        plausible-looking number computed with the wrong constants."""
        return self.vendors.get(vendor, VendorReasoningConfig())


def default_config() -> ReasoningConfig:
    """The built-in defaults, no YAML involved."""
    return ReasoningConfig()


def load_config(path: str | Path) -> ReasoningConfig:
    text = Path(path).read_text(encoding="utf-8")
    return ReasoningConfig.model_validate(yaml.safe_load(text) or {})
