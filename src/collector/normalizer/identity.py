"""Account identity, read from the agent's own config file.

The transcript carries none: no email, no account, no organization. OTEL does -- it stamps
`user.email`, `user.account_uuid`, `organization.id` on **every span**, reading them from the
same place this does. A JSONL-only pipeline therefore has no identity at all unless it is given
one, which is what this supplies.

Deliberately **opt-in**. The caller passes an explicit path (`--user-config`); there is no
default lookup and no implicit read of `~/.claude.json`. Identity is the one thing the rest of
the normalizer works to keep *out* of the output (the scrubs remove content; OTEL spans carry PII and
JSONL does not) -- so putting it in is an operator's decision, made per run, not a default.

Read once per run and attached to `meta_info`, not per event: it is a property of who ran the
session, not of anything that happened inside it.
"""
from __future__ import annotations

import json
from pathlib import Path

__all__ = ["AccountIdentity", "load_account_identity"]

# oauthAccount.<vendor field> -> emitted field. Everything else in that block --
# organizationRole, rate-limit tiers, accountCreatedAt -- is entitlement data, not identity,
# and is not read.
_FIELDS = (
    ("emailAddress", "email"),
    ("accountUuid", "account_uuid"),
    ("organizationName", "organization_name"),
    ("organizationUuid", "organization_uuid"),
)

AccountIdentity = dict[str, str | None]


def load_account_identity(path: str | Path) -> AccountIdentity:
    """Reads the four identity fields from a Claude Code config file.

    Every field is present in the result and `null` when the file omits it -- a personal
    account has no organization, and a consumer should not have to tell "absent" from "not
    looked for". `meta_info.user` being `null` means identity was never requested; the object
    with null members means it was requested and partly unavailable.

    Raises with the path named when the file cannot be read or parsed. An operator who passed
    `-u` asked for identity; silently returning nulls would look like a personal account.
    """
    path = Path(path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise FileNotFoundError(f"user config not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise ValueError(f"user config is not valid JSON: {path} ({exc})") from None

    account = data.get("oauthAccount")
    if not isinstance(account, dict):
        raise ValueError(f"no `oauthAccount` block in {path} -- is this a Claude Code config file?")

    return {emitted: account.get(vendor) for vendor, emitted in _FIELDS}
