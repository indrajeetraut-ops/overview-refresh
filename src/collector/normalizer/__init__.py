"""The normalizer's public API 

A registry of VendorNormalizer implementations, selected by SessionSource.vendor. v1 ships
one: ClaudeCodeNormalizer. Adding a vendor is a new implementation behind this Protocol --
no change here.
"""
from __future__ import annotations

from typing import Iterator, Protocol

from .claude_code import ClaudeCodeNormalizer
from .config import NormalizerConfig, load_config
from .models import EventIn, NormalizedSession, SessionSource, SubagentStream

__all__ = [
    "ClaudeCodeNormalizer",
    "EventIn",
    "NormalizedSession",
    "NormalizerConfig",
    "SessionSource",
    "SubagentStream",
    "VendorNormalizer",
    "load_config",
    "normalize",
    "registry",
]


class VendorNormalizer(Protocol):
    vendor: str

    def normalize(self, src: SessionSource) -> Iterator[NormalizedSession]: ...


registry: dict[str, VendorNormalizer] = {
    "claude_code": ClaudeCodeNormalizer(),
}


def normalize(src: SessionSource) -> Iterator[NormalizedSession]:
    """Dispatches to the registered VendorNormalizer for src.vendor."""
    try:
        normalizer = registry[src.vendor]
    except KeyError:
        raise ValueError(f"no VendorNormalizer registered for vendor: {src.vendor!r}") from None
    return normalizer.normalize(src)
