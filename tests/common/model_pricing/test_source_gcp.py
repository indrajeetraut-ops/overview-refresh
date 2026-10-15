"""`sources.gcp_billing` -- acquisition gate only (design §4.2, §6.2, §12).

GCP's SKU -> model/token_type/scope mapping is an open design question (§12); this
source has no `parse()` to test against a fixture the way the other three do. What is
implemented, and what these tests cover, is the enable/disable gate: `auto` (default)
follows `GCP_BILLING_API_KEY`; an explicit toggle overrides the environment; and an
enabled-but-unmapped source raises `SourceError` rather than silently doing nothing
(§6.1: an enabled source that fails must fail the whole `update`).

No network in this module at all yet, so no fixture file either -- `monkeypatch` stands
in for ".env" by setting/clearing the environment variable directly.
"""
import pytest

from common.model_pricing.config import SourceToggle
from common.model_pricing.errors import SourceError
from common.model_pricing.normalize import Registry
from common.model_pricing.sources import gcp_billing


# ------------------------------------------------------------------ auto (default): env-gated


def test_auto_without_env_var_is_skipped(monkeypatch):
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)
    payload, provenance = gcp_billing.fetch(SourceToggle(enabled="auto"))
    assert payload is None
    assert provenance == {"status": "skipped", "reason": "GCP_BILLING_API_KEY not set"}


def test_default_toggle_argument_behaves_like_auto(monkeypatch):
    """`fetch()` with no argument at all must behave exactly like an absent
    `model_pricing.yaml` (§4.4): `SourcesToggles`' own default is `enabled="auto"`."""
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)
    payload, provenance = gcp_billing.fetch()
    assert payload is None
    assert provenance["status"] == "skipped"


def test_auto_with_env_var_set_is_enabled_and_raises(monkeypatch):
    monkeypatch.setenv("GCP_BILLING_API_KEY", "test-key-value")
    with pytest.raises(SourceError, match="gcp_billing mapping not implemented"):
        gcp_billing.fetch(SourceToggle(enabled="auto"))


# ------------------------------------------------------------------ explicit toggle overrides the environment


def test_explicit_false_is_skipped_even_with_env_var_set(monkeypatch):
    monkeypatch.setenv("GCP_BILLING_API_KEY", "test-key-value")
    payload, provenance = gcp_billing.fetch(SourceToggle(enabled=False))
    assert payload is None
    assert provenance["status"] == "skipped"
    assert provenance["reason"] == "disabled in model_pricing.yaml"


def test_explicit_true_raises_even_without_env_var(monkeypatch):
    monkeypatch.delenv("GCP_BILLING_API_KEY", raising=False)
    with pytest.raises(SourceError, match="gcp_billing mapping not implemented"):
        gcp_billing.fetch(SourceToggle(enabled=True))


# ------------------------------------------------------------------ parse() interface symmetry


def test_parse_of_no_payload_gives_no_rows():
    rows, unmapped, warnings = gcp_billing.parse(None, Registry())
    assert rows == []
    assert unmapped == []
    assert warnings == []


def test_parse_of_a_payload_raises_not_implemented():
    """No code path can produce a real payload yet (`fetch()` always raises before
    returning one when enabled), but `parse()` must not silently accept one either --
    it is a stub for the whole source, not just the acquisition half."""
    with pytest.raises(SourceError, match="gcp_billing mapping not implemented"):
        gcp_billing.parse({"some": "payload"}, Registry())
