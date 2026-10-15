"""Deterministic id / sha / number derivation for the synthetic org (plan §2 D8/D12, §5 P2).

Every function here is pure: same input, same output, forever. None of them read the clock,
touch randomness, or depend on iteration order.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from typing import Mapping

#: Every `rng_for(org.seed, domain, *keys)` call in this package must use one of these domains
#: (plan §4). `test_ids.py::test_domain_registry_complete` greps the source for `rng_for(` /
#: `rng(` calls and asserts every literal domain string used is listed here.
DOMAINS = (
    "lead", "user_mult", "window", "sess_count", "sess_weight", "dominant", "subagent",
    "layout", "link", "item", "item_pick", "feature", "commits", "review", "outcome",
    "revert", "rework", "backlog", "text", "files", "requests", "habit_bust", "habit_resume",
    "habit_interrupt", "habit_compact", "practices", "tools", "segments",
)

_SLUG_RE = re.compile(r"[^a-z0-9-]+")


def repo_num(full_name: str) -> int:
    """D8: `900_000_000 + int(sha256("synthetic-org:repo:<full_name>").hexdigest()[:8], 16) % 90_000_000`."""
    digest = hashlib.sha256(f"synthetic-org:repo:{full_name}".encode()).hexdigest()
    return 900_000_000 + int(digest[:8], 16) % 90_000_000


def installation_id(owner: str) -> int:
    """D8: `800_000_000 + int(sha256("synthetic-org:installation:<owner>").hexdigest()[:8], 16) % 90_000_000`."""
    digest = hashlib.sha256(f"synthetic-org:installation:{owner}".encode()).hexdigest()
    return 800_000_000 + int(digest[:8], 16) % 90_000_000


def session_id(l1_user: str, date_iso: str, project: str, agent: str, k: str) -> str:
    key = f"synthetic-org:session:{l1_user}:{date_iso}:{project}:{agent}:{k}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


def child_session_id(parent_id: str, j: int) -> str:
    """Matches the real `agent-<hex>` look (mainline subagent session ids)."""
    digest = hashlib.sha1(f"{parent_id}:{j}".encode()).hexdigest()
    return f"agent-{digest[:17]}"


def sha(*parts: str) -> str:
    return hashlib.sha1(("synthetic-org:" + ":".join(parts)).encode()).hexdigest()


def review_id(pr_key: str, i: int) -> int:
    digest = hashlib.sha256(f"synthetic-org:review:{pr_key}:{i}".encode()).hexdigest()
    return int(digest[:12], 16)


def team_slug(name: str) -> str:
    """D7: the Layer 1 team_name, lower-cased, with `[^a-z0-9-]` runs collapsed to `-`."""
    slug = _SLUG_RE.sub("-", name.strip().lower()).strip("-")
    return slug or "team"


def artifact_id(repo_num_: int, kind: str, key: str) -> str:
    return f"github:{repo_num_}:{kind}:{key}"


def choice(weights: Mapping[str, float], rng) -> str:
    """Layer 1 `series._choice`'s technique, copied (not imported -- it is private there) and
    shared across this package rather than duplicated per module. `weights` need not already
    sum to 1; only relative magnitude and the positive-key filter matter."""
    total = sum(v for v in weights.values() if v > 0)
    positive = [key for key in sorted(weights) if weights[key] > 0]
    if not positive:
        raise ValueError("choice() needs at least one positive weight")
    draw = rng.random() * total
    cumulative = 0.0
    for key in positive:
        cumulative += weights[key]
        if draw < cumulative:
            return key
    return positive[-1]
