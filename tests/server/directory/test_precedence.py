"""Provider precedence (IDENT §3, plan G3). No DB."""
import pytest

from server.directory import DEFAULT_CHAIN, Precedence


@pytest.fixture
def p() -> Precedence:
    return Precedence(DEFAULT_CHAIN)


def test_unset_field_is_always_writable(p):
    assert p.wins("telemetry", None) is True


def test_same_provider_reimport_updates(p):
    assert p.wins("csv", "csv") is True


def test_higher_rank_overwrites_lower(p):
    assert p.wins("csv", "telemetry") is True
    assert p.wins("scim", "csv") is True


def test_lower_rank_cannot_clobber_higher(p):
    # the load-bearing case: a CSV re-import must not overwrite an IdP-owned field.
    assert p.wins("csv", "scim") is False
    assert p.wins("telemetry", "csv") is False


def test_unknown_provider_sits_below_named_but_above_unset(p):
    assert p.wins("mystery", None) is True
    assert p.wins("mystery", "csv") is False
    assert p.wins("csv", "mystery") is True


def test_empty_chain_rejected():
    with pytest.raises(ValueError):
        Precedence([])


def test_custom_chain_order_respected():
    # if a deployment ranks csv above ldap, csv then wins against ldap.
    p = Precedence(["csv", "ldap", "telemetry"])
    assert p.wins("csv", "ldap") is True
    assert p.wins("ldap", "csv") is False
