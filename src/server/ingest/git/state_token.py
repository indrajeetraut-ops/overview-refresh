"""The tenant-binding `state` token (git-source.md §6.1 mechanism 1).

A customer clicks "Connect GitHub" inside our product while authenticated as `org_id`; we send
them to `https://github.com/apps/<slug>/installations/select_target?state=<token>` and remember
nothing server-side until GitHub redirects back. The token carries `org_id` and an expiry,
HMAC-signed so tampering (a forged org_id, a replayed expired token) is detectable without a
database round trip -- there is nothing to look up until the redirect actually arrives.

**Caveat, stated plainly (§6.1):** the redirect's `installation_id` is spoofable -- anyone can
craft a GET request to our setup URL with an arbitrary `installation_id` and a stolen or replayed
`state`. This token proves "the org that owns this `state` value initiated a connect flow," not
"this specific installation belongs to that org." The signed `installation.created` webhook (or
a `cli_installations reconcile` pass) is what confirms an installation is real; `state` only
narrows which org a *pending, unconfirmed* installation_id should bind to if GitHub's own
`installation.created` webhook later shows up with the same id (`git_pending_installs`,
"popped" the moment either the webhook or a reconcile adopts it).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time

_SEP = ":"


def issue_state(org_id: str, secret: str, ttl_s: int = 900, now: float | None = None) -> str:
    """A short-lived, tamper-evident token binding a connect-flow click to `org_id`."""
    if _SEP in org_id:
        raise ValueError(f"org_id must not contain {_SEP!r}: {org_id!r}")
    now = now if now is not None else time.time()
    exp = int(now) + ttl_s
    payload = f"{org_id}{_SEP}{exp}"
    sig = hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}{_SEP}{sig}".encode("utf-8")).decode("ascii")


def verify_state(token: str, secret: str, now: float | None = None) -> str | None:
    """The `org_id` a valid, unexpired token names, or `None` if the token is malformed,
    tampered with, or expired. Never raises -- a bad `state` is an ordinary "can't bind this
    one" outcome, not a crash."""
    now = now if now is not None else time.time()
    try:
        decoded = base64.urlsafe_b64decode(token.encode("ascii")).decode("utf-8")
        org_id, exp_str, sig = decoded.rsplit(_SEP, 2)
        exp = int(exp_str)
    except Exception:
        return None
    expected = hmac.new(secret.encode("utf-8"), f"{org_id}{_SEP}{exp_str}".encode("utf-8"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return None
    if exp < now:
        return None
    return org_id
