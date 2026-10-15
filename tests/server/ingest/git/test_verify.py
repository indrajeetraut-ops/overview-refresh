"""Webhook signature verification -- the four cases git-source.md §11 mandates:
valid, invalid, absent, and a body mutated after signing."""
from __future__ import annotations

from server.ingest.git.verify import compute_signature, verify

SECRET = "poc-webhook-secret-do-not-use-in-prod"
BODY = b'{"action":"opened","number":1184}'


def test_valid_signature_accepted():
    header = compute_signature(SECRET, BODY)
    assert verify(SECRET, BODY, header) is True


def test_invalid_signature_rejected():
    assert verify(SECRET, BODY, "sha256=deadbeef") is False


def test_absent_signature_rejected():
    assert verify(SECRET, BODY, None) is False
    assert verify(SECRET, BODY, "") is False


def test_body_mutated_after_signing_rejected():
    header = compute_signature(SECRET, BODY)
    mutated = BODY.replace(b"opened", b"closed")
    assert verify(SECRET, mutated, header) is False


def test_wrong_secret_rejected():
    header = compute_signature("some-other-secret", BODY)
    assert verify(SECRET, BODY, header) is False


def test_missing_prefix_rejected():
    # a bare hex digest without the "sha256=" prefix is not a valid header
    raw = compute_signature(SECRET, BODY).removeprefix("sha256=")
    assert verify(SECRET, BODY, raw) is False
