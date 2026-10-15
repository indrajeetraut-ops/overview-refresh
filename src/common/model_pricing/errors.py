"""Exceptions for `common.model_pricing` (design §9).

Three, matching the three ways this module fails at its boundaries -- config, catalog,
source acquisition. `resolve()` itself never raises for an unmatched or ambiguous model:
those are results (`MatchResult.status`), not errors, because a caller pricing a whole
day of usage cannot let one unrecognized model string abort the run.
"""


class ModelPricingError(Exception):
    """Base class. Never raised directly."""


class ConfigError(ModelPricingError):
    """`model_pricing.yaml`, `channel_config.yaml`, or `aliases.yaml` is invalid, or a
    channel config / alias entry names a channel or model that is not priced (§9)."""


class CatalogError(ModelPricingError):
    """`load_catalog` cannot produce a usable catalog: no version exists, the requested
    version is unknown, a version file is unreadable, or its `hash8` does not match its
    content (§9)."""


class SourceError(ModelPricingError):
    """`update` only: a source fetch or parse failed. The current catalog version is left
    untouched -- a failed source never silently becomes a fallback (§6.1, §11)."""
