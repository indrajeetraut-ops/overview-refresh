"""The tenant-binding state token (git-source.md §6.1 mechanism 1): round-trips, tamper
detection, and expiry -- all without a database, since the token carries everything by design."""
from __future__ import annotations

from server.ingest.git.state_token import issue_state, verify_state

SECRET = "state-secret-do-not-use-in-prod"


def test_issue_then_verify_round_trips_the_org_id():
    token = issue_state("org_acme", SECRET)
    assert verify_state(token, SECRET) == "org_acme"


def test_tampered_token_is_rejected():
    token = issue_state("org_acme", SECRET)
    tampered = token[:-4] + ("A" if token[-4] != "A" else "B") + token[-3:]
    assert verify_state(tampered, SECRET) is None


def test_wrong_secret_is_rejected():
    token = issue_state("org_acme", SECRET)
    assert verify_state(token, "some-other-secret") is None


def test_expired_token_is_rejected():
    token = issue_state("org_acme", SECRET, ttl_s=10, now=1_000_000.0)
    assert verify_state(token, SECRET, now=1_000_000.0 + 5) == "org_acme"     # still valid
    assert verify_state(token, SECRET, now=1_000_000.0 + 11) is None          # expired


def test_garbage_input_never_raises():
    assert verify_state("not-a-token-at-all", SECRET) is None
    assert verify_state("", SECRET) is None
    assert verify_state("====", SECRET) is None


def test_org_id_with_separator_rejected_at_issue_time():
    import pytest
    with pytest.raises(ValueError):
        issue_state("org:with:colons", SECRET)
