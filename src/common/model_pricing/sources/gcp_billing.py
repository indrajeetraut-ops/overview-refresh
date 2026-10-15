"""GCP Billing Catalog source adapter (design §6.2-§6.4, §12).

Unlike the other three sources, GCP's SKU -> `(model_id, token_type, scope)` mapping is
an **open** design question (§6.3's `token_type` table lists every GCP column as
`[open]`; §12 records "GCP Billing Catalog: model and price-name normalization;
coverage of Claude on Vertex" as not yet decided). This module therefore implements only
the acquisition gate (§4.2, §6.2):

- `sources.gcp_billing` is `auto` by default (`config.SourcesToggles`) -- enabled only
  when `GCP_BILLING_API_KEY` is set in the environment (`.env`, loaded by the shell,
  never committed). An explicit `true`/`false` in `model_pricing.yaml` overrides the
  environment check either way.
- Disabled (the common case today): `fetch()` makes no network call and returns a
  `"skipped"` provenance (§3.3's `sources.gcp_billing` example) -- `gcp_vertex` channels
  are still priced, from LiteLLM's fallback rows only (§4.1's "Until the GCP adapter
  exists (§12), LiteLLM only").
- Enabled: since the mapping is not implemented, `fetch()` raises `SourceError`
  immediately rather than pretending to acquire something and silently producing zero
  rows -- an enabled source that fails must fail the whole `update` (§6.1), same as a
  network failure in any other source.

TODO(§12 GCP): implement the real Cloud Billing Catalog `fetch()` (endpoint, paging,
auth via `GCP_BILLING_API_KEY`) and a `parse()` that reads GCP's SKU descriptions to
recover `(model_id, token_type, scope)` the way `sources/aws_price_list.py` reads
`usagetype` and `sources/azure_retail.py` reads `meterName` -- both the SKU naming
convention for Vertex-hosted foundation models and how `regional` vs. `global` Vertex
endpoints are distinguished in the catalog need to be worked out first (§6.3, §6.4).
Once that lands, this module should stop raising and instead fetch real payload +
provenance for `parse()` to read, the same two-function shape as every other adapter.
"""
from __future__ import annotations

import os
from typing import Any

from common.model_pricing.catalog import SourceRow, UnmappedEntry
from common.model_pricing.config import SourceToggle
from common.model_pricing.errors import SourceError
from common.model_pricing.normalize import Registry

#: The environment variable that gates this source (§4.2, §4.4, §6.2).
_API_KEY_ENV_VAR = "GCP_BILLING_API_KEY"


def _is_enabled(toggle: SourceToggle) -> bool:
    """Effective enablement for this run: `toggle.enabled` is `model_pricing.yaml`'s
    `sources.gcp_billing` (§4.2) -- `"auto"` (the default, `SourcesToggles`) defers to
    whether `GCP_BILLING_API_KEY` is set; an explicit `True`/`False` is used as-is and
    is not overridden by the environment either way."""
    if toggle.enabled == "auto":
        return bool(os.environ.get(_API_KEY_ENV_VAR))
    return bool(toggle.enabled)


def fetch(
    toggle: SourceToggle | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Acquire GCP Billing Catalog prices, or record why nothing was acquired (§6.2).

    Returns `(payload, provenance)`. `toggle` defaults to `SourceToggle(enabled="auto")`
    (`SourcesToggles`' own default), matching what an absent `model_pricing.yaml`
    produces (§4.4).

    Disabled -- `payload` is `None` (§3.3's `sources.gcp_billing` example provenance
    shape exactly, so `build.py` can drop it into the version file's `sources` dict
    unchanged):

    >>> fetch()[1]
    {'status': 'skipped', 'reason': 'GCP_BILLING_API_KEY not set'}

    Enabled -- raises `SourceError` before making any request, since the SKU mapping
    this source needs does not exist yet (§12). Callers must treat this exactly like a
    real fetch failure (§6.1: "Any enabled source fails -> update fails (exit 1); the
    current version stays").
    """
    toggle = toggle or SourceToggle(enabled="auto")
    if _is_enabled(toggle):
        # TODO(§12 GCP): replace with the real Cloud Billing Catalog request once the
        # SKU -> model/token_type/scope mapping is designed (module docstring above).
        raise SourceError("gcp_billing mapping not implemented")

    reason = (
        f"{_API_KEY_ENV_VAR} not set" if toggle.enabled == "auto" else "disabled in model_pricing.yaml"
    )
    return None, {"status": "skipped", "reason": reason}


def parse(
    payload: dict[str, Any] | None, registry: Registry
) -> tuple[list[SourceRow], list[UnmappedEntry], list[str]]:
    """Pure parse step, kept for interface symmetry with the other source adapters
    (`build.py` calls `fetch()` then `parse()` the same way for every source).

    In V1 no payload ever reaches this function with real content: `fetch()` returns
    `None` when disabled (nothing to parse -- zero rows, zero unmapped, zero warnings)
    and raises `SourceError` before returning when enabled (module docstring). A `None`
    payload is simply "no rows".
    """
    if payload is None:
        return [], [], []
    # TODO(§12 GCP): parse real payload once fetch() can produce one.
    raise SourceError("gcp_billing mapping not implemented")
