"""Webhook signature verification (git-source.md §5.1, arch §18.2).

GitHub signs each delivery with HMAC-SHA256 over the **raw** request body, keyed by the
webhook secret (a symmetric secret shared at App registration -- distinct from the App's
private key, which never touches the webhook path). The header is `X-Hub-Signature-256:
sha256=<hex>`. Absence of the header is a rejection, not a pass.

The compare is constant-time: a byte-by-byte early-exit comparison leaks, through timing, how
much of a forged signature was correct, which is enough to forge one. `hmac.compare_digest`
is the constant-time primitive.
"""
from __future__ import annotations

import hashlib
import hmac

_PREFIX = "sha256="


def compute_signature(secret: str, body: bytes) -> str:
    """The header value GitHub would send for this body under this secret."""
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _PREFIX + digest


def verify(secret: str, body: bytes, header: str | None) -> bool:
    """True iff `header` is a valid X-Hub-Signature-256 for `body` under `secret`.

    `body` MUST be the exact bytes received -- any re-serialization (json.loads then
    json.dumps) changes whitespace and key order and breaks the HMAC. This is why the route
    reads the raw body and never binds a pydantic body model (git-source.md §4.1).
    """
    if not header or not header.startswith(_PREFIX):
        return False
    expected = compute_signature(secret, body)
    # compare_digest over the full "sha256=..." strings; both are fixed length here.
    return hmac.compare_digest(expected, header)
